"""A minimal RFC 6455 WebSocket client, in stdlib Python and nothing else.

## Why this file exists at all

OpenBoat OS is stdlib-only on purpose: it has to run on a Pi, on a laptop, anywhere with no
virtualenv and no network install. `websockets` and `websocket-client` are both one
`pip install` away and both would be the obvious choice anywhere else — but adding the
first dependency to this project to talk to one optional data source is a bad trade, and
half-building the protocol is worse than either.

So it is built properly, and it is *tested* properly: `tests/test_ws.py` runs this client
against a server side of RFC 6455 written in the test itself, and exercises the parts that
actually go wrong — the accept-key digest, split TCP reads, the three payload-length
encodings, masking, fragmentation, and ping/pong.

## What it is not

Not a general-purpose library. Client only, no extensions (no permessage-deflate, no
subprotocol negotiation), text and binary frames, close/ping/pong. That is the entire
surface a shore-side AIS feed needs. Sending large frames is supported but this client is
only ever expected to send one small subscription message.

## The bits that are easy to get wrong

- **A client MUST mask every frame it sends** (RFC 6455 §5.3) and a server MUST NOT mask
  the frames it sends back. Compliant servers close the connection on an unmasked client
  frame, which presents as a mysterious disconnect immediately after the handshake.
- **`recv()` is not a message boundary.** TCP hands back whatever it has. Every read here
  goes through `_exact()`, which loops until it has the bytes it was promised.
- **A message can arrive in fragments**: an initial frame with the real opcode and FIN=0,
  then continuation frames with opcode 0. Control frames (ping/close) may be interleaved
  *inside* a fragmented message and must be handled without breaking it up.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import urllib.parse

# The magic string from RFC 6455 §1.3. It exists so that a cached HTTP response can never
# be mistaken for a successful handshake.
GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONT, OP_TEXT, OP_BINARY = 0x0, 0x1, 0x2
OP_CLOSE, OP_PING, OP_PONG = 0x8, 0x9, 0xA


class WebSocketError(Exception):
    """Handshake refused, protocol violated, or the peer went away."""


class WebSocket:
    """One connection. Use as a context manager; it closes politely on the way out."""

    def __init__(self, url: str, headers: dict[str, str] | None = None,
                 timeout: float = 30.0) -> None:
        parts = urllib.parse.urlparse(url)
        if parts.scheme not in ("ws", "wss"):
            raise WebSocketError(f"not a websocket url: {url}")

        secure = parts.scheme == "wss"
        port = parts.port or (443 if secure else 80)
        host = parts.hostname or ""
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"

        self.timeout = timeout
        self._buffer = b""
        self._closed = False

        raw = socket.create_connection((host, port), timeout=timeout)
        if secure:
            # Default context: verifies the certificate and the hostname. Do not relax
            # this — an unauthenticated feed of "where the ships are" is worth nothing.
            raw = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
        self.sock = raw

        self._handshake(host, port, path, secure, headers or {})

    # -- handshake ---------------------------------------------------------------------
    def _handshake(self, host: str, port: int, path: str, secure: bool,
                   headers: dict[str, str]) -> None:
        key = base64.b64encode(os.urandom(16)).decode()
        default_port = 443 if secure else 80
        host_header = host if port == default_port else f"{host}:{port}"

        lines = [
            f"GET {path} HTTP/1.1",
            f"Host: {host_header}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {key}",
            "Sec-WebSocket-Version: 13",
        ]
        lines += [f"{name}: {value}" for name, value in headers.items()]
        self.sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())

        head = self._until(b"\r\n\r\n")
        status, _, rest = head.partition("\r\n")
        if "101" not in status.split(" ")[:2]:
            raise WebSocketError(f"upgrade refused: {status.strip()}")

        received = {}
        for line in rest.split("\r\n"):
            name, _, value = line.partition(":")
            if name:
                received[name.strip().lower()] = value.strip()

        expected = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        if received.get("sec-websocket-accept") != expected:
            raise WebSocketError("bad Sec-WebSocket-Accept — this is not a websocket server")
        if received.get("upgrade", "").lower() != "websocket":
            raise WebSocketError("server did not upgrade to websocket")

    def _until(self, marker: bytes) -> str:
        """Read the HTTP response head. Only used before framing starts."""
        while marker not in self._buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise WebSocketError("connection closed during handshake")
            self._buffer += chunk
            if len(self._buffer) > 65536:
                raise WebSocketError("handshake response absurdly long")
        head, _, self._buffer = self._buffer.partition(marker)
        return head.decode("latin-1")

    # -- framing -----------------------------------------------------------------------
    def _exact(self, count: int) -> bytes:
        """Exactly `count` bytes, however many recv() calls that takes."""
        while len(self._buffer) < count:
            chunk = self.sock.recv(max(4096, count - len(self._buffer)))
            if not chunk:
                raise WebSocketError("connection closed mid-frame")
            self._buffer += chunk
        out, self._buffer = self._buffer[:count], self._buffer[count:]
        return out

    def _read_frame(self) -> tuple[bool, int, bytes]:
        first, second = self._exact(2)
        fin = bool(first & 0x80)
        if first & 0x70:
            raise WebSocketError("reserved bits set — an extension we did not negotiate")
        opcode = first & 0x0F

        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            (length,) = struct.unpack("!H", self._exact(2))
        elif length == 127:
            (length,) = struct.unpack("!Q", self._exact(8))

        mask = self._exact(4) if masked else b""
        payload = self._exact(length)
        if masked:
            payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        return fin, opcode, payload

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        if self._closed:
            raise WebSocketError("send on a closed socket")
        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)               # 0x80 = the mandatory client mask bit
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack("!H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", length)

        mask = os.urandom(4)
        header += mask
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    # -- messages ----------------------------------------------------------------------
    def send(self, message: str | bytes) -> None:
        if isinstance(message, str):
            self._send_frame(OP_TEXT, message.encode("utf-8"))
        else:
            self._send_frame(OP_BINARY, message)

    def send_json(self, obj) -> None:
        self.send(json.dumps(obj))

    def recv(self, timeout: float | None = None) -> str | bytes | None:
        """One complete message, reassembling fragments. None when the peer closed.

        Control frames are answered here and never surface to the caller: a ping gets its
        pong, a close gets its close. A `socket.timeout` propagates — a watch loop wants
        to know that nothing arrived, which is different from the feed having ended.
        """
        if timeout is not None:
            self.sock.settimeout(timeout)

        buffer = bytearray()
        message_op: int | None = None

        while True:
            fin, opcode, payload = self._read_frame()

            if opcode == OP_CLOSE:
                self._respond_close(payload)
                return None
            if opcode == OP_PING:
                self._send_frame(OP_PONG, payload)
                continue
            if opcode == OP_PONG:
                continue

            if opcode == OP_CONT:
                if message_op is None:
                    raise WebSocketError("continuation frame with nothing to continue")
            else:
                if message_op is not None:
                    raise WebSocketError("new message started before the last one finished")
                message_op = opcode

            buffer += payload
            if fin:
                if message_op == OP_TEXT:
                    return buffer.decode("utf-8")
                return bytes(buffer)

    def _respond_close(self, payload: bytes) -> None:
        """Echo the peer's close frame back, then consider ourselves shut.

        The flag is set *after* the send, not before: `_send_frame` refuses to write to a
        closed socket, so marking first makes the polite goodbye impossible to send.
        """
        if not self._closed:
            try:
                self._send_frame(OP_CLOSE, payload[:2] or struct.pack("!H", 1000))
            except (OSError, WebSocketError):
                pass
            self._closed = True

    def close(self, code: int = 1000) -> None:
        if not self._closed:
            try:
                self._send_frame(OP_CLOSE, struct.pack("!H", code))
            except (OSError, WebSocketError):
                pass
            self._closed = True
        try:
            self.sock.close()
        except OSError:
            pass

    def __enter__(self) -> "WebSocket":
        return self

    def __exit__(self, *_) -> None:
        self.close()


def connect(url: str, headers: dict[str, str] | None = None,
            timeout: float = 30.0) -> WebSocket:
    return WebSocket(url, headers, timeout)
