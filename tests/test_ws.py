#!/usr/bin/env python3
"""The stdlib WebSocket client, tested against a server side written here.

    python3 tests/test_ws.py

There is no API key and no internet access in this test. The server below implements the
*other* half of RFC 6455 — independently, from the spec, not by calling into ws.py — so
the two halves agreeing is real evidence rather than a tautology. What is exercised:

  1. the Sec-WebSocket-Accept digest, and that a wrong one is rejected
  2. client frames are masked (a compliant server drops the connection otherwise)
  3. all three payload-length encodings: 7-bit, 16-bit extended, 64-bit extended
  4. TCP fragmentation — a frame arriving in dribs and drabs across many recv() calls
  5. WebSocket fragmentation — continuation frames, with a ping interleaved mid-message
  6. ping is answered with a pong carrying the same payload
  7. a close frame ends the conversation cleanly

What this does NOT test is aisstream.io's own message schema. That needs a key and a live
connection; see the note at the top of ais.py.
"""

from __future__ import annotations

import base64
import hashlib
import socket
import struct
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openboat.ws import GUID, WebSocket, WebSocketError

failures: list[str] = []


def check(label: str, got, want) -> None:
    ok = got == want
    shown = repr(got)
    if isinstance(got, (bytes, str)) and len(shown) > 46:
        shown = f"{type(got).__name__} len={len(got)} {shown[:28]}…"
    print(f"   {'✓' if ok else '✗'} {label:<44} {shown}")
    if not ok:
        failures.append(f"{label}: got {got!r}, expected {want!r}")


# -- the server half of RFC 6455, written from the spec ---------------------------------
def server_frame(opcode: int, payload: bytes, fin: bool = True) -> bytes:
    """A server frame: FIN/opcode, then length, then payload. Never masked (§5.1)."""
    header = bytearray([(0x80 if fin else 0x00) | opcode])
    length = len(payload)
    if length < 126:
        header.append(length)
    elif length < 65536:
        header.append(126)
        header += struct.pack("!H", length)
    else:
        header.append(127)
        header += struct.pack("!Q", length)
    return bytes(header) + payload


def server_read_frame(conn: socket.socket) -> tuple[int, bytes, bool]:
    """Read one client frame, insisting it is masked, and unmask it."""
    def exact(n):
        out = b""
        while len(out) < n:
            chunk = conn.recv(n - len(out))
            if not chunk:
                raise AssertionError("client vanished mid-frame")
            out += chunk
        return out

    first, second = exact(2)
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F
    if length == 126:
        (length,) = struct.unpack("!H", exact(2))
    elif length == 127:
        (length,) = struct.unpack("!Q", exact(8))
    mask = exact(4) if masked else b""
    payload = exact(length)
    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return opcode, payload, masked


def serve(handler, accept_key: bool = True) -> int:
    """Start a one-shot server on an ephemeral port; return the port."""
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def run():
        conn, _ = listener.accept()
        listener.close()
        head = b""
        while b"\r\n\r\n" not in head:
            head += conn.recv(4096)

        key = ""
        for line in head.decode("latin-1").split("\r\n"):
            name, _, value = line.partition(":")
            if name.strip().lower() == "sec-websocket-key":
                key = value.strip()

        digest = hashlib.sha1((key + GUID).encode()).digest()
        accept = base64.b64encode(digest).decode()
        if not accept_key:
            accept = base64.b64encode(b"x" * 20).decode()      # deliberately wrong

        conn.sendall(
            b"HTTP/1.1 101 Switching Protocols\r\n"
            b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
            + f"Sec-WebSocket-Accept: {accept}\r\n\r\n".encode()
        )
        try:
            handler(conn)
        finally:
            try:
                conn.close()
            except OSError:
                pass

    threading.Thread(target=run, daemon=True).start()
    return port


# =======================================================================================
print("\n1. Handshake, masking, and a short text message")
seen: dict = {}


def handler_echo(conn):
    opcode, payload, masked = server_read_frame(conn)
    seen.update(opcode=opcode, payload=payload, masked=masked)
    conn.sendall(server_frame(0x1, b"pong from the server"))
    time.sleep(0.2)


port = serve(handler_echo)
with WebSocket(f"ws://127.0.0.1:{port}/stream") as sock:
    sock.send("hello from a 9 m boat")
    reply = sock.recv(timeout=5)
time.sleep(0.1)
check("client frame was masked (RFC 6455 §5.3)", seen["masked"], True)
check("server unmasked the payload correctly", seen["payload"], b"hello from a 9 m boat")
check("opcode was TEXT", seen["opcode"], 0x1)
check("client received the reply", reply, "pong from the server")


# =======================================================================================
print("\n2. All three payload-length encodings")


def handler_lengths(conn):
    conn.sendall(server_frame(0x1, b"a" * 10))        # 7-bit length
    conn.sendall(server_frame(0x1, b"b" * 300))       # 126 + 16-bit
    conn.sendall(server_frame(0x1, b"c" * 70000))     # 127 + 64-bit
    time.sleep(0.3)


port = serve(handler_lengths)
with WebSocket(f"ws://127.0.0.1:{port}/") as sock:
    short, medium, long_one = (sock.recv(timeout=5) for _ in range(3))
check("7-bit length  (10 bytes)", len(short), 10)
check("16-bit length (300 bytes)", len(medium), 300)
check("64-bit length (70000 bytes)", len(long_one), 70000)
check("64-bit payload intact", long_one == "c" * 70000, True)


# =======================================================================================
print("\n3. A frame dribbled across many TCP reads")


def handler_dribble(conn):
    frame = server_frame(0x1, b"harbour anchorage watch" * 20)
    for i in range(0, len(frame), 7):                 # 7 bytes at a time, with pauses
        conn.sendall(frame[i:i + 7])
        time.sleep(0.002)
    time.sleep(0.2)


port = serve(handler_dribble)
with WebSocket(f"ws://127.0.0.1:{port}/") as sock:
    dribbled = sock.recv(timeout=5)
check("reassembled from 7-byte TCP chunks", dribbled, "harbour anchorage watch" * 20)


# =======================================================================================
print("\n4. WebSocket fragmentation with a ping interleaved")
pong: dict = {}


def handler_fragments(conn):
    conn.sendall(server_frame(0x1, b"vessel 210", fin=False))    # TEXT, not final
    conn.sendall(server_frame(0x9, b"are you there"))            # PING, mid-message
    conn.sendall(server_frame(0x0, b"456789 ", fin=False))       # CONTINUATION
    conn.sendall(server_frame(0x0, b"crossing", fin=True))       # CONTINUATION, final
    opcode, payload, _ = server_read_frame(conn)                # expect the pong
    pong.update(opcode=opcode, payload=payload)
    time.sleep(0.2)


port = serve(handler_fragments)
with WebSocket(f"ws://127.0.0.1:{port}/") as sock:
    assembled = sock.recv(timeout=5)
time.sleep(0.1)
check("three fragments reassembled", assembled, "vessel 210456789 crossing")
check("ping was answered with a PONG (0xA)", pong.get("opcode"), 0xA)
check("pong echoed the ping payload", pong.get("payload"), b"are you there")


# =======================================================================================
print("\n5. A close frame ends the conversation")


def handler_close(conn):
    conn.sendall(server_frame(0x1, b"last position report"))
    conn.sendall(server_frame(0x8, struct.pack("!H", 1000)))
    time.sleep(0.2)


port = serve(handler_close)
with WebSocket(f"ws://127.0.0.1:{port}/") as sock:
    last = sock.recv(timeout=5)
    ended = sock.recv(timeout=5)
check("message before the close", last, "last position report")
check("close frame surfaces as None, not an error", ended, None)


# =======================================================================================
print("\n6. A wrong Sec-WebSocket-Accept is refused")
port = serve(lambda conn: time.sleep(0.2), accept_key=False)
try:
    WebSocket(f"ws://127.0.0.1:{port}/").close()
    check("rejected the bad accept key", "no error raised", "WebSocketError")
except WebSocketError as exc:
    check("rejected the bad accept key", "bad Sec-WebSocket-Accept" in str(exc), True)


print("\n" + "-" * 78)
if failures:
    print(f"FAILED — {len(failures)} check(s):")
    for line in failures:
        print(f"  {line}")
    sys.exit(1)
print("RFC 6455 client agrees with an independently written server on all counts.")
