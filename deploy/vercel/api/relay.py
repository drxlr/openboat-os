#!/usr/bin/env python3
"""The public address. Everything it receives it hands to the gate, and it decides nothing.

A boat's computer is on a boat, or in a locker, or on a shelf at home behind a router that
has never had a port opened in its life — and it changes network every time the boat moves.
There is no stable address to give anybody. `cloudflared` produces one, but a fresh one on
every restart, which is not an address you can put in a phone.

So: a fixed hostname on Vercel that forwards to whatever the tunnel currently is. The
current tunnel URL lives in one Edge Config item, written by `scripts/gate_tunnel.py` on the
boat's machine each time the tunnel comes up. The person's phone keeps one bookmark forever.

**This file holds no policy.** It has no idea who is logged in, which boats exist, or what
any route means. It copies a request to the gate and copies the answer back. Every decision
— the password, the session, which boat, whose name goes on a snag — is made by
`openboat.gate` on the boat's own machine, where the data is. That is deliberate: the thing
on the public internet should be the thing with nothing in it.

The one thing it does add is `X-OpenBoat-Gate`, the shared secret that proves to the gate
that a request came through here. A `trycloudflare.com` hostname is a public address, and
the gate refuses everything that does not carry the header, so the tunnel is reachable and
useless to anybody but this function.

    deploy/vercel/README.md   how to deploy it and what to set

Stdlib only, like the rest of the project.
"""

from __future__ import annotations

import http.client
import json
import os
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler

#: A snag with a dozen phone photographs in it, base64'd. The gate caps itself at 20 MB and
#: the boat's own service at 24; this sits under both so an over-sized request is refused
#: here, before it is carried down a tunnel to be refused there.
MAX_BODY = 4 * 1024 * 1024
MAX_RESPONSE = 64 * 1024 * 1024
TIMEOUT = 25

#: The Edge Config read is one HTTP call on a cold request, so it is remembered briefly. A
#: tunnel that has just been restarted is unreachable for at most this long, which is the
#: right trade: the alternative is a network round trip on every asset the console loads.
ORIGIN_TTL = 30

#: Headers a client sends that are ours to reproduce. Everything else — Host, the whole
#: `x-vercel-*` family, hop-by-hop headers — describes this hop and not the browser's.
FORWARD = ("Cookie", "Content-Type", "Accept", "Accept-Language")

#: Headers to copy back. Set-Cookie is handled separately because there can be several and
#: a dict keeps one: a login response that sets a session and clears something else would
#: silently lose half of what it did.
COPY_BACK = ("Content-Type", "Content-Length", "Cache-Control", "Content-Disposition",
             "Location", "Content-Security-Policy", "X-Content-Type-Options",
             "Referrer-Policy", "WWW-Authenticate")

#: Where Vercel may have left the path the browser actually asked for, in preference order.
#: The rewrite in vercel.json puts it in `__path`; these are the belt to that pair of
#: braces, because a rewrite that silently stops carrying it would make every page on the
#: site the same page.
PATH_HEADERS = ("x-vercel-original-path", "x-original-path", "x-vercel-rewrite-path",
                "x-forwarded-path")

_origin_cache: tuple[float, str] = (0.0, "")


# ── where the boat is ──────────────────────────────────────────────────────────────────

def read_origin(now: float | None = None) -> str:
    """The tunnel URL, from Edge Config, or `$OPENBOAT_ORIGIN`, or "" when neither.

    Edge Config first because that is the one that changes: the boat writes it every time
    cloudflared restarts. The environment variable is the fallback for a fixed tunnel, and
    for running this function locally against a gate on the same machine.
    """
    global _origin_cache
    now = time.time() if now is None else now
    when, cached = _origin_cache
    if cached and now - when < ORIGIN_TTL:
        return cached

    found = _edge_config_item("origin") or os.environ.get("OPENBOAT_ORIGIN", "").strip()
    found = found.rstrip("/")
    if found:
        _origin_cache = (now, found)
    return found


def _edge_config_item(key: str) -> str:
    """One item out of the Edge Config named by `$EDGE_CONFIG`, or "" for any failure.

    The connection string is `https://edge-config.vercel.com/<id>?token=<t>`; a single item
    is read from `<base>/item/<key>?token=<t>`. Never raises: a relay that 500s because a
    configuration service was slow is worse than one that says the boat is not connected.
    """
    raw = os.environ.get("EDGE_CONFIG", "").strip()
    if not raw:
        return ""
    try:
        parts = urllib.parse.urlsplit(raw)
        token = urllib.parse.parse_qs(parts.query).get("token", [""])[0]
        target = f"{parts.path.rstrip('/')}/item/{urllib.parse.quote(key)}"
        if token:
            target += "?token=" + urllib.parse.quote(token)
        conn = http.client.HTTPSConnection(parts.hostname, parts.port, timeout=5)
        try:
            conn.request("GET", target, headers={"Accept": "application/json"})
            resp = conn.getresponse()
            body = resp.read(4096)
            if resp.status != 200:
                return ""
            value = json.loads(body)
        finally:
            conn.close()
    except (OSError, ValueError, json.JSONDecodeError, http.client.HTTPException):
        return ""
    return value.strip() if isinstance(value, str) else ""


# ── what was asked for ─────────────────────────────────────────────────────────────────

def incoming_path(raw_path: str, headers=None) -> str:
    """The path *and query* the browser asked for, out of what the rewrite left behind.

    `vercel.json` rewrites every path to `/api/relay?__path=$1` and Vercel merges the
    original query string alongside it, so the request arrives as
    `/api/relay?__path=b/demo/api/docs&q=impeller`. `__path` is lifted out and the rest of
    the query is put back exactly as it came, in its original order — a relay that
    reassembled the query from a parsed dict would drop repeated keys, and repeated keys
    are how a multi-select filter is spelt.
    """
    path, _, query = str(raw_path or "/").partition("?")
    kept, found = [], ""
    for chunk in query.split("&"):
        if not chunk:
            continue
        name, sep, value = chunk.partition("=")
        if name == "__path":
            found = urllib.parse.unquote(value) if sep else ""
        else:
            kept.append(chunk)

    if not found and headers is not None:
        for name in PATH_HEADERS:
            value = _header(headers, name)
            if value:
                found = value.partition("?")[0]
                break

    if not found:
        # A direct hit on the function itself rather than a rewritten one. `/api/relay` is
        # not a route on the gate, so it becomes the root rather than a 404 nobody can act
        # on — and health checks are answered before this is ever reached.
        found = "" if path in ("/api/relay", "/api/relay.py") else path

    if not found.startswith("/"):
        found = "/" + found
    return found + ("?" + "&".join(kept) if kept else "")


def _header(headers, name: str) -> str:
    try:
        got = headers.get(name)
    except AttributeError:
        got = (headers or {}).get(name)
    return str(got).strip() if got else ""


# ── the forward ────────────────────────────────────────────────────────────────────────

def forward(origin: str, method: str, target: str, headers, body: bytes | None):
    """Send one request to the gate. Returns (status, [(name, value)…], body).

    Raises `OSError` or `http.client.HTTPException` when the tunnel does not answer, which
    is a different thing from the gate answering with an error and is reported differently.
    """
    parts = urllib.parse.urlsplit(origin)
    cls = (http.client.HTTPConnection if parts.scheme == "http"
           else http.client.HTTPSConnection)
    conn = cls(parts.hostname, parts.port, timeout=TIMEOUT)
    try:
        conn.request(method, target, body=body, headers=headers)
        resp = conn.getresponse()
        payload = resp.read(MAX_RESPONSE)
        out = [(name, resp.getheader(name)) for name in COPY_BACK
               if name != "Content-Length" and resp.getheader(name) is not None]
        # Every Set-Cookie, not the last one. `getheader` folds repeats into one comma-
        # joined string, and a cookie value may legitimately contain a comma, so the folded
        # form cannot be split back apart correctly.
        for cookie in (resp.msg.get_all("Set-Cookie") or []):
            out.append(("Set-Cookie", cookie))
        return resp.status, out, payload
    finally:
        conn.close()


def request_headers(incoming, host: str, secret: str, client_ip: str) -> dict:
    """What the gate is told about this request: the browser's few headers, plus who we are."""
    sending = {}
    for name in FORWARD:
        value = _header(incoming, name)
        if value:
            sending[name] = value
    if secret:
        sending["X-OpenBoat-Gate"] = secret
    if host:
        sending["X-Forwarded-Host"] = host
    sending["X-Forwarded-Proto"] = "https"
    if client_ip:
        sending["X-Forwarded-For"] = client_ip
    return sending


# ── the pages this function serves itself ──────────────────────────────────────────────

def _page(title: str, message: str) -> bytes:
    return (f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>body{{margin:0;min-height:100vh;display:grid;place-items:center;
 font:400 16px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
 background:#f6f7f8;color:#2b3330}}
 main{{max-width:30rem;padding:2rem;text-align:center}}
 h1{{font-size:1.1rem;font-weight:600;margin:0 0 .5rem}}
 p{{margin:0;color:#5d6b66}}
 @media(prefers-color-scheme:dark){{body{{background:#15181a;color:#c2c8c4}}
 p{{color:#8b9691}}}}</style></head>
<body><main><h1>{title}</h1><p>{message}</p></main></body></html>""").encode()


NOT_CONNECTED = _page(
    "The boat is not connected",
    "Nothing has told this address where the boat's computer is. It reappears on its own "
    "when the boat's machine is switched on and its tunnel comes up.")

NOT_ANSWERING = _page(
    "The boat is not answering",
    "The address is known but nothing came back. That is usually the boat's machine asleep "
    "or off the network — try again in a moment.")


# ── the function Vercel runs ───────────────────────────────────────────────────────────

class handler(BaseHTTPRequestHandler):                                   # noqa: N801
    """Vercel's Python runtime instantiates this per request and calls `do_<METHOD>`."""

    def do_GET(self):
        self._relay()

    def do_HEAD(self):
        self._relay()

    def do_POST(self):
        self._relay()

    def do_OPTIONS(self):
        self._relay()

    def _send(self, status: int, kind: str, body: bytes, extra=()):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        for name, value in extra:
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _relay(self):
        target = incoming_path(self.path, self.headers)
        route = target.partition("?")[0]
        origin = read_origin()

        if route == "/_relay/health":
            body = json.dumps({"ok": True, "origin_known": bool(origin)}).encode()
            # Deliberately does not say *what* the origin is. The point of a health check
            # here is "is the boat reachable", and publishing the tunnel hostname would
            # hand out the one address the gate's shared secret exists to protect.
            return self._send(200, "application/json", body,
                              (("Cache-Control", "no-store"),))

        if not origin:
            return self._send(503, "text/html; charset=utf-8", NOT_CONNECTED,
                              (("Cache-Control", "no-store"),))

        body = None
        if self.command == "POST":
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
            except ValueError:
                length = 0
            if length > MAX_BODY:
                return self._send(413, "text/plain; charset=utf-8", b"too much data")
            body = self.rfile.read(length) if length > 0 else b""

        sending = request_headers(
            self.headers,
            _header(self.headers, "X-Forwarded-Host") or _header(self.headers, "Host"),
            os.environ.get("OPENBOAT_GATE_SECRET", "").strip(),
            _header(self.headers, "X-Forwarded-For")
            or _header(self.headers, "X-Real-IP"))
        if body is not None:
            sending["Content-Length"] = str(len(body))

        try:
            # Never `follow_redirects`. The gate redirects to `/login` and to `/b/<key>/`
            # as part of how it works, and those are answers for the browser to act on —
            # following one here would turn a 303 the browser needed to see into a page it
            # gets at the wrong address, with the wrong cookies attached.
            status, headers, payload = forward(
                origin, "GET" if self.command == "HEAD" else self.command,
                target, sending, body)
        except (OSError, http.client.HTTPException):
            return self._send(502, "text/html; charset=utf-8", NOT_ANSWERING,
                              (("Cache-Control", "no-store"),))

        kind = "application/octet-stream"
        extra = []
        for name, value in headers:
            if name == "Content-Type":
                kind = value
            elif name != "Content-Length":
                extra.append((name, value))
        return self._send(status, kind, payload, extra)
