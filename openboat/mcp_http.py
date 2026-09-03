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
import secrets
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

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
    },
    {
        "name": "fetch",
        "description": "Retrieve one passage in full by the id returned from search.",
        "inputSchema": {"type": "object",
                        "properties": {"id": {"type": "string"}},
                        "required": ["id"]},
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


class Door(BaseHTTPRequestHandler):
    token = ""

    def log_message(self, fmt, *args):
        sys.stderr.write(f"{self.address_string()} {fmt % args}\n")

    def _authorised(self) -> bool:
        """Constant-time compare. The token is short and an attacker can retry all night."""
        offered = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not offered.startswith(prefix):
            return False
        return secrets.compare_digest(offered[len(prefix):], self.token)

    def _refuse(self) -> None:
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Bearer realm="openboat"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if not self._authorised():
            return self._refuse()
        if self.path.rstrip("/") not in ("/sse", "/mcp"):
            self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers()
            return
        # The SSE endpoint OpenAI's connector expects. It stays open; this server has
        # nothing to push, so it holds the stream and answers on POST like any other
        # streamable-HTTP MCP server.
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            self.wfile.write(b": openboat mcp\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

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
    server = HTTPServer((BIND, port), Door)
    print(f"OpenBoat MCP on http://{BIND}:{port}/mcp  (SSE at /sse/)\n"
          f"{len(TOOLS)} tools. Bearer token required. Bound to localhost — expose it with "
          f"a tunnel, deliberately.", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
