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

**No route to the helm.** The tool list is the read-only set plus the writers, each of
which appends to a file of the companion's own — a maintenance log, a notes file, a
transcription, or the boat's inbox, which is not the boat's documents. There is no import
of `openboat.control` here and
`tests/test_control_gate.py` fails the build if one appears. A hosted model with a
connector to your boat must not be able to steer it, and the reason is not that the gate
would refuse — it is that the gate should never be asked.

**No unauthenticated start.** `OPENBOAT_MCP_TOKEN` is required and has no default. A boat's
papers, position and engine history behind a URL with no token is a boat's papers,
position and engine history published. If the variable is missing the server refuses to
start and says so, rather than starting helpfully and quietly.

**One token per assistant, if you want to know who did what.**

    export OPENBOAT_MCP_TOKENS="chatgpt=$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))'),claude=$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"

Each name gets its own token and the name behind the token that was offered becomes the
caller's identity for that request. It is not a permission system — every token opens the
same door — it is attribution, and it matters because of `openboat/intake.py`: an assistant
can put a document into the boat's inbox, and "who brought this" is the first thing the
person deciding whether to accept it will want to know. `OPENBOAT_MCP_TOKEN` still works
exactly as it did, under the name `assistant`, and the two can be set together.

**No exposing itself.** It binds to localhost. Reaching it from the internet is a tunnel
you set up deliberately — see `docs/COMPANION.md` — because the moment a boat's server
listens on a public interface is a decision, not a default.
"""

from __future__ import annotations

import json
import os
import queue
import re
import secrets
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import knowledge, mcp
from .profile import pinned

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


def _passage_id(key: str, hit) -> str:
    """`boat:document#line` — the boat first, so `fetch` opens the right library."""
    return f"{key}:{hit.doc.name}#{hit.line}"


def _libraries(boat: str = "") -> list[tuple[dict, knowledge.Library]]:
    """The boats to search: the one named, or every one when none is."""
    boats = [b for b in mcp.fleet() if not boat or b["key"] == boat]
    out = []
    for entry in boats:
        with pinned(entry["profile"]):
            out.append((entry, knowledge.load()))
    return out


def tool_search(query, boat="", **_) -> str:
    """Passages from the boat's papers — or from every boat's, when none is named.

    The deep-research contract fixes this tool's shape, so unlike the rest it cannot
    insist on a key. It answers by naming the boat in every title and id instead: a
    passage from the other hull is labelled as one, and nothing in the result reads as if
    it came from the boat the question was about.
    """
    several = len(mcp.fleet()) > 1
    results = []
    for entry, library in _libraries(boat):
        if not library.paths:
            continue
        for h in library.search(query, limit=8):
            title = f"{h.heading} ({h.where})"
            if several:
                title = f"{entry['name']}: {title}"
            results.append({"id": _passage_id(entry["key"], h), "title": title,
                            "url": f"file://{h.doc}#L{h.line}", "text": h.text[:400]})
    return json.dumps({"results": results}, ensure_ascii=False)


def tool_fetch(id, boat="", **_) -> str:
    """One passage in full. Matched on boat:name#line so an id survives an edit elsewhere."""
    key, _, rest = str(id).rpartition(":")
    name, _, line = rest.partition("#")
    for entry, library in _libraries(key or boat):
        for passage in library.passages():
            if passage.doc.name == name and str(passage.line) == line:
                return json.dumps({"id": id, "title": f"{entry['name']}: {passage.heading}",
                                   "text": passage.text,
                                   "url": f"file://{passage.doc}#L{passage.line}",
                                   "metadata": {"boat": entry["key"], "document": name,
                                                "line": passage.line}},
                                  ensure_ascii=False)
    return json.dumps({"id": id, "title": "not found", "text":
                       "No such passage. The documents may have been edited since the "
                       "search; run search again rather than guessing.", "url": ""})


HANDLERS = {**mcp.HANDLERS, "search": tool_search, "fetch": tool_fetch}
#: `search` and `fetch` cover every boat when none is named — the contract fixes their
#: shape — so their `boat` is optional even where the other tools' is required.
ANY_BOAT = frozenset({"search", "fetch"})
TOOLS = mcp.TOOLS + mcp.widen(CHATGPT_TOOLS, required=False)


def handle(request: dict) -> dict | None:
    """MCP dispatch, borrowing the stdio server's plumbing and widening the tool list."""
    method = request.get("method")
    request_id = request.get("id")

    if method == "tools/list":
        return mcp.reply(request_id, {"tools": TOOLS})

    if method == "tools/call":
        params = request.get("params", {})
        result = mcp.dispatch(HANDLERS, params.get("name"), params.get("arguments"),
                              any_boat=ANY_BOAT)
        if result is None:
            return mcp.error(request_id, -32602, f"unknown tool {params.get('name')!r}")
        return mcp.reply(request_id, result)

    return mcp.handle(request)


#: Open SSE streams, by session id. One queue each; the POST handler drops answers in and
#: the GET handler, blocked on the queue, writes them out.
SESSIONS: dict[str, queue.Queue] = {}


def named_tokens(raw: str = "") -> dict[str, str]:
    """`"chatgpt=abc,claude=def"` → `{"chatgpt": "abc", "claude": "def"}`.

    A name is reduced to letters, digits, hyphen and underscore, because it is written into
    the boat's own files as the submitter of a document and a name arriving from an
    environment variable should not be able to be a newline. A token under sixteen
    characters is dropped rather than accepted, the same reasoning `openboat.snag` uses for
    its own keys: a gate that accepts a four-character token is not a gate, and silently
    accepting one is worse than refusing it loudly.
    """
    out: dict[str, str] = {}
    for chunk in (raw or "").split(","):
        name, _, token = chunk.partition("=")
        clean = re.sub(r"[^A-Za-z0-9_-]", "", name.strip())[:32]
        if clean and len(token.strip()) >= 16:
            out[clean] = token.strip()
    return out


class Door(BaseHTTPRequestHandler):
    #: The legacy single token. Its caller is named `assistant`.
    token = ""
    #: name -> token, from `OPENBOAT_MCP_TOKENS`. Attribution, not permission: every one of
    #: these opens exactly the same door as the one above.
    tokens: dict[str, str] = {}
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        # On the URL-auth route the path IS the credential, and the base class logs the
        # request line verbatim — so the token of every caller ended up in a log file
        # anyone with read access to the logs could paste into a connector. Mask any
        # segment that matches a token this door accepts before the line is written.
        line = fmt % args
        for _, token in self._known():
            if token:
                line = line.replace(token, "<token>")
        sys.stderr.write(f"{self.address_string()} {line}\n")

    def _known(self) -> list[tuple[str, str]]:
        """Every (name, token) pair this door accepts, the legacy single token included."""
        pairs = [("assistant", self.token)] if self.token else []
        return pairs + sorted(self.tokens.items())

    def _match(self, offered: str) -> str | None:
        """The name behind a token, or None. Constant-time, and every pair is compared.

        Deliberately not short-circuiting on the first match: returning early would make the
        time taken depend on which token was offered, which is the leak the constant-time
        comparison is there to close in the first place.
        """
        found = None
        for name, token in self._known():
            if token and secrets.compare_digest(offered, token):
                found = name
        return found

    def _who(self) -> str | None:
        """Who is calling, or None if they may not. A Bearer header, or the token as the
        first path segment.

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
            return self._match(offered[len("Bearer "):])
        first = self.path.lstrip("/").split("/")[0].split("?")[0]
        return self._match(first) if first else None

    def _authorised(self) -> bool:
        return self._who() is not None

    def _route(self) -> str:
        """The path with any leading token segment removed."""
        parts = [p for p in self.path.split("?")[0].split("/") if p]
        if parts and self._match(parts[0]) is not None:
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

        # Whichever token this caller used stays in the path, so the POST is authorised the
        # same way the GET was — and so that a client holding one of several tokens is not
        # sent back with somebody else's.
        first = self.path.lstrip("/").split("/")[0].split("?")[0]
        prefix = "/" + first if first and self._match(first) is not None else ""
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
        who = self._who()
        if who is None:
            return self._refuse()
        length = min(int(self.headers.get("Content-Length", 0)), 1_000_000)
        try:
            request = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._send({"jsonrpc": "2.0", "id": None,
                               "error": {"code": -32700, "message": "parse error"}})

        # Who is calling, for the duration of this request only. A `ContextVar` set on the
        # handler's own thread, because this is a threading server: a module attribute here
        # would let one connection's identity end up stamped on another's write.
        token = mcp.CALLER.set(who)
        try:
            response = handle(request)
        finally:
            mcp.CALLER.reset(token)

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
    named = named_tokens(os.environ.get("OPENBOAT_MCP_TOKENS", ""))
    if not token and not named:
        print(
            "OPENBOAT_MCP_TOKEN is not set, so this server will not start.\n\n"
            "It serves your boat's papers, its position and its engine history. Behind a\n"
            "URL with no token, that is all of it published. Make one and keep it:\n\n"
            "  export OPENBOAT_MCP_TOKEN=\"$(python3 -c 'import secrets;"
            "print(secrets.token_urlsafe(32))')\"\n\n"
            "Or one per assistant, so the boat knows who brought which document:\n\n"
            "  export OPENBOAT_MCP_TOKENS=\"chatgpt=<token>,claude=<token>\"\n",
            file=sys.stderr)
        return 2

    Door.token = token
    Door.tokens = named
    port = int(argv[0]) if argv and argv[0].isdigit() else PORT
    # Threading, because an SSE stream blocks its handler for as long as the client is
    # connected, and the POSTs that carry the actual requests arrive on other connections
    # while it does. A single-threaded server accepts the stream and then never hears them.
    server = ThreadingHTTPServer((BIND, port), Door)
    print(f"OpenBoat MCP: {len(TOOLS)} tools, bound to {BIND}:{port}\n"
          f"  header auth   Authorization: Bearer <token>  ->  /mcp  or  /sse/\n"
          f"  URL auth      /<token>/sse/   (for a client that takes only a link)\n"
          + (f"  callers       assistant (OPENBOAT_MCP_TOKEN)\n" if token else "")
          + "".join(f"  callers       {name} (OPENBOAT_MCP_TOKENS)\n"
                    for name in sorted(named))
          + f"Localhost only. Put it behind a tunnel deliberately; treat the URL as the "
            f"password.", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
