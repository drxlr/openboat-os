#!/usr/bin/env python3
"""The same MCP server, reachable over HTTP — so a hosted assistant can know the boat.

    export OPENBOAT_MCP_TOKEN="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"
    python3 -m openboat.mcp_http                    # 127.0.0.1:8748

`openboat.mcp` speaks over a pipe, which is right for a model running on the same machine.
ChatGPT is not on the same machine. Its connectors reach out over the network to a URL, so
the same tools need an HTTP door: JSON-RPC on `POST /mcp`, and the server-sent-events
interface at `/sse/` that OpenAI's connector documentation asks for.

## Why this is worth the trouble

Read `docs/COMPANION.md` for the evidence, but the short version came out of reading three
years of a boat owner's actual chat history. The same handful of facts get asked for over
and over — engine variant, drive type, whether it is raw-water cooled, what was already
tried, what the last quote was — because a conversation cannot remember the boat between
sessions. One exchange in that history is an owner insisting he had *already* said which
engine he has, to an assistant with no way to know he had.

Those facts are not hard. They are written down. They are simply not where the model is.
This module is the door between the two: the model brings vision and reasoning, the boat
brings its own identity, its papers and its live readings, and nobody has to type the
engine serial into a chat window ever again.

## What it will not do

**No route to the helm.** The tool list is the read-only set plus `log_check`, which
appends a line to a maintenance log. There is no import of `openboat.control` here and
`tests/test_control_gate.py` fails the build if one appears. A hosted model with a
connector to your boat must not be able to steer it, and the reason is not that the gate
would refuse — it is that the gate should never be asked.

**No unauthenticated start.** `OPENBOAT_MCP_TOKEN` is required and has no default. A boat's
papers, position and engine history behind a URL with no token is a boat's papers,
position and engine history published. If the variable is missing the server refuses to
start and says so, rather than starting helpfully and quietly.

**No exposing itself.** It binds to localhost. Reaching it from the internet is a tunnel
you set up deliberately — see `docs/COMPANION.md` — because the moment a boat's server
listens on a public interface is a decision, not a default.
"""

from __future__ import annotations

import json
import os
import queue
import secrets
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import knowledge, mcp

PORT = 8748

#: Localhost. The tunnel is the thing that decides who can reach this, and it is a separate,
#: deliberate act — see the module docstring.
BIND = "127.0.0.1"

#: OpenAI's connector documentation asks that a server for ChatGPT implement two read-only
#: tools named exactly `search` and `fetch`. They are the deep-research contract rather than
#: general MCP, so they live here rather than in the stdio server: they are a translation of
#: `boat_docs` into the shape that client expects, not a ninth thing the boat can do.
CHATGPT_TOOLS = [
    {
        "name": "search",
        "description": "Search this boat's own papers — survey, manuals, yard invoices, "
                       "the owner's working notes — and return matching passages. Use it "
                       "before answering anything specific to this vessel.",
        "inputSchema": {"type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"]},
        "annotations": {"title": "search", **mcp.READ_ONLY},
    },
    {
        "name": "fetch",
        "description": "Retrieve one passage in full by the id returned from search.",
        "inputSchema": {"type": "object",
                        "properties": {"id": {"type": "string"}},
                        "required": ["id"]},
        "annotations": {"title": "fetch", **mcp.READ_ONLY},
    },
]


def _passage_id(hit) -> str:
    return f"{hit.doc.name}#{hit.line}"


def tool_search(query, **_) -> str:
    library = knowledge.load()
    if not library.paths:
        return json.dumps({"results": []})
    results = [{"id": _passage_id(h), "title": f"{h.heading} ({h.where})",
                "url": f"file://{h.doc}#L{h.line}",
                "text": h.text[:400]}
               for h in library.search(query, limit=8)]
    return json.dumps({"results": results}, ensure_ascii=False)


def tool_fetch(id, **_) -> str:
    """One passage in full. Matched on name#line so an id survives an edit elsewhere."""
    name, _, line = str(id).partition("#")
    for passage in knowledge.load().passages():
        if passage.doc.name == name and str(passage.line) == line:
            return json.dumps({"id": id, "title": passage.heading, "text": passage.text,
                               "url": f"file://{passage.doc}#L{passage.line}",
                               "metadata": {"document": name, "line": passage.line}},
                              ensure_ascii=False)
    return json.dumps({"id": id, "title": "not found", "text":
                       "No such passage. The documents may have been edited since the "
                       "search; run search again rather than guessing.", "url": ""})


HANDLERS = {**mcp.HANDLERS, "search": tool_search, "fetch": tool_fetch}
TOOLS = mcp.TOOLS + CHATGPT_TOOLS


def handle(request: dict) -> dict | None:
    """MCP dispatch, borrowing the stdio server's plumbing and widening the tool list."""
    method = request.get("method")
    request_id = request.get("id")

    if method == "tools/list":
        return mcp.reply(request_id, {"tools": TOOLS})

    if method == "tools/call":
        params = request.get("params", {})
        handler = HANDLERS.get(params.get("name"))
        if handler is None:
            return mcp.error(request_id, -32602, f"unknown tool {params.get('name')!r}")
        try:
            text = handler(**(params.get("arguments") or {}))
        except Exception as exc:
            return mcp.reply(request_id, {
                "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                "isError": True})
        return mcp.reply(request_id, {"content": [{"type": "text", "text": text}]})

    return mcp.handle(request)


#: Open SSE streams, by session id. One queue each; the POST handler drops answers in and
#: the GET handler, blocked on the queue, writes them out.
SESSIONS: dict[str, queue.Queue] = {}


class Door(BaseHTTPRequestHandler):
    token = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write(f"{self.address_string()} {fmt % args}\n")

    def _authorised(self) -> bool:
        """A Bearer header, or the token as the first path segment. Constant-time either way.

        The header is the right way and the path is the way that works. A hosted assistant's
        connector setup often takes a URL and nothing else — no place to put a header — and
        the alternative to a token in the path is no token at all, on a public URL, in front
        of a boat's papers and position. A secret in a URL leaks more easily than one in a
        header (it lands in logs, in history, in anything that stores the link), so treat
        the whole URL as the credential: do not paste it anywhere you would not paste a
        password, and rotate it by restarting with a new token.
        """
        offered = self.headers.get("Authorization", "")
        if offered.startswith("Bearer "):
            return secrets.compare_digest(offered[len("Bearer "):], self.token)
        first = self.path.lstrip("/").split("/")[0].split("?")[0]
        return bool(first) and secrets.compare_digest(first, self.token)

    def _route(self) -> str:
        """The path with any leading token segment removed."""
        parts = [p for p in self.path.split("?")[0].split("/") if p]
        if parts and secrets.compare_digest(parts[0], self.token):
            parts = parts[1:]
        return "/" + "/".join(parts)

    def _refuse(self) -> None:
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Bearer realm="openboat"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        """Open an SSE session, or answer a health check.

        This is the older HTTP+SSE transport, and it has one step that is easy to miss and
        fatal to omit: immediately after the stream opens the server must send an
        `endpoint` event naming the URL the client should POST its requests to. Without it
        the client has a stream and nowhere to talk back, so it hangs up — which is exactly
        what a first attempt at this did. Every reply then travels back down this stream
        rather than in the POST's own body.
        """
        if not self._authorised():
            return self._refuse()
        route = self._route().rstrip("/")
        if route not in ("/sse", "/mcp", ""):
            self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers()
            return

        session = uuid.uuid4().hex
        outbox: queue.Queue = queue.Queue()
        SESSIONS[session] = outbox

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        # The token stays in the path so the POST is authorised the same way the GET was.
        prefix = "/" + self.token if self.path.lstrip("/").startswith(self.token) else ""
        try:
            self._event("endpoint", f"{prefix}/messages?sessionId={session}")
            while True:
                try:
                    message = outbox.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": keep-alive\n\n")     # or a proxy closes it for us
                    self.wfile.flush()
                    continue
                if message is None:
                    break
                self._event("message", json.dumps(message))
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            SESSIONS.pop(session, None)

    def _event(self, name: str, data: str) -> None:
        self.wfile.write(f"event: {name}\ndata: {data}\n\n".encode())
        self.wfile.flush()

    def do_POST(self):
        if not self._authorised():
            return self._refuse()
        length = min(int(self.headers.get("Content-Length", 0)), 1_000_000)
        try:
            request = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._send({"jsonrpc": "2.0", "id": None,
                               "error": {"code": -32700, "message": "parse error"}})

        response = handle(request)

        # A POST that named a session belongs to the SSE transport: acknowledge it here and
        # put the answer on that session's stream. A POST without one is streamable HTTP,
        # where the answer goes straight back in this response.
        session = ""
        if "?" in self.path:
            from urllib.parse import parse_qs
            session = parse_qs(self.path.split("?", 1)[1]).get("sessionId", [""])[0]
        outbox = SESSIONS.get(session) if session else None

        if outbox is not None:
            if response is not None:
                outbox.put(response)
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if response is None:
            self.send_response(202); self.send_header("Content-Length", "0"); self.end_headers()
            return
        self._send(response)

    def _send(self, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    token = os.environ.get("OPENBOAT_MCP_TOKEN", "")
    if not token:
        print(
            "OPENBOAT_MCP_TOKEN is not set, so this server will not start.\n\n"
            "It serves your boat's papers, its position and its engine history. Behind a\n"
            "URL with no token, that is all of it published. Make one and keep it:\n\n"
            "  export OPENBOAT_MCP_TOKEN=\"$(python3 -c 'import secrets;"
            "print(secrets.token_urlsafe(32))')\"\n",
            file=sys.stderr)
        return 2

    Door.token = token
    port = int(argv[0]) if argv and argv[0].isdigit() else PORT
    # Threading, because an SSE stream blocks its handler for as long as the client is
    # connected, and the POSTs that carry the actual requests arrive on other connections
    # while it does. A single-threaded server accepts the stream and then never hears them.
    server = ThreadingHTTPServer((BIND, port), Door)
    print(f"OpenBoat MCP: {len(TOOLS)} tools, bound to {BIND}:{port}\n"
          f"  header auth   Authorization: Bearer <token>  ->  /mcp  or  /sse/\n"
          f"  URL auth      /{token}/sse/   (for a client that takes only a link)\n"
          f"Localhost only. Put it behind a tunnel deliberately; treat the URL as the "
          f"password.", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
