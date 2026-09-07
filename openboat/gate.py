#!/usr/bin/env python3
"""The front door: one login in front of every boat, and the only thing a tunnel exposes.

    python3 -m openboat.gate                      # → http://localhost:8749
    python3 -m openboat.gate invite you@example.org --name "A Name" --boat demo

Everything else in this package assumes a network boundary somewhere else — `openboat.server`
says so in its own docstring, and docs/NETWORK.md says use a private overlay and nothing
else. That is the right answer for one person with one laptop. It is the wrong answer for
the yard, a delivery skipper and a partner who each need one boat on their phone for a
fortnight and none of whom are going to install Tailscale.

So: exactly one process that has a password on it, and the boat services keep having none.
Nothing about `openboat.server` or `openboat.snag` changes; they stay bound to localhost
behind this, and this is the only thing the tunnel reaches. The trust chain is a single
line, and every link in it is checked:

    browser → Vercel relay → cloudflared tunnel → **the gate** → the boat's own services

The relay proves it is the relay with `$OPENBOAT_GATE_SECRET` in a header, because a tunnel
hostname is a public address and a public address with no shared secret in front of it is a
door with a sign on it. The browser proves it is a person with a signed cookie. And the
boats prove nothing at all, which is why they must never be on the tunnel themselves.

## What a logged-in person actually gets

The console, at `/b/<key>/`, for each boat their user record names. `admin` sees every boat
this gate is configured with; `owner` and `crew` see only theirs. A key they may not see is
a **404, never a 403** — "you are not allowed to see *that boat*" tells somebody that boat
exists, which is a fact about a stranger's property leaking out of an error code.

Two things are forced rather than trusted on the way through, and they are the reason this
is a relay and not a port forward:

- **`boat=<key>`** is rewritten onto every snag request from the path, replacing whatever
  was asked for, in the query *and* in the JSON body. A logged-in crew member cannot file a
  fault against a boat they cannot see by editing one field in a request.
- **`who=<their name>`** is rewritten onto every POST from the session. Attribution on a
  snag is the thing that makes a fault followable-up six weeks later, and a name that the
  sender chooses is not attribution.

## The users file

JSON at `$OPENBOAT_USERS`, written atomically and **re-read on every request** — so
`revoke` takes effect on the next click rather than on the next restart, which is the only
useful definition of revocation. Passwords are pbkdf2-sha256 at 600,000 iterations and are
never accepted on the command line: `invite` prints a signed link, and the person sets
their own on the other end of it. Nothing here can print, mail or recover a password.

## Extending it

`MOUNTS` is a module-level list of `(prefix, handler)` pairs, consulted after the gate
secret and the session cookie but *before* any built-in route:

    def share(handler, user, rest):        # rest is the path after the prefix
        ...
        return True                        # truthy: handled. falsy: fall through.

    openboat.gate.MOUNTS.append(("/s/", share))

`handler` is the live request handler, so `handler.send_page`, `handler.send_json`,
`handler.body()`, `handler.relay()` and `handler.boats_for(user)` are all available; `user`
is the user dict or `None` when nobody is logged in. That is the seam a share-link module
hangs off — `/s/<token>` pages and `POST /b/<key>/share` — without editing the router here.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
import mimetypes
import os
import secrets
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

__all__ = ["MOUNTS", "Gate", "main", "load_users", "save_users", "hash_password",
           "verify_password", "sign", "unsign", "boat_origins", "invite_user",
           "invite_link"]

PORT = 8749
WEB = Path(__file__).parent / "web"

#: Bound to localhost only. The gate is the thing with a password on it, and it is reached
#: from the internet through a tunnel that runs on this same machine — so it never needs to
#: listen on a network interface, and binding one would put a login form on the LAN for no
#: reason. Change this and you have widened the attack surface of the only authenticated
#: component in the project.
BIND = "127.0.0.1"

SESSION_COOKIE = "ob_session"
SESSION_DAYS = 30
INVITE_DAYS = 7
PBKDF2_ITERS = 600_000
ROLES = ("admin", "owner", "crew")

#: A phone photograph arrives base64'd inside a JSON body; the snag service caps itself at
#: 24 MB and this sits just under it, because a request that is going to be refused should
#: be refused before it is relayed rather than after.
MAX_BODY = 20 * 1024 * 1024
#: An account change is a name, a password or a list of boat keys. Nothing here is a
#: photograph, so the cap is the size of a mistake rather than the size of an upload.
ACCOUNT_MAX_BODY = 64 * 1024
#: What may come back from a boat. Generous — a scanned survey is a large PDF — but finite,
#: because a relay that reads an unbounded body is a way to run this machine out of memory.
MAX_RESPONSE = 64 * 1024 * 1024
RELAY_TIMEOUT = 20

#: Ten wrong passwords for one email inside a quarter of an hour and that email stops being
#: answered. Per email rather than per address: the address is the relay's, so every request
#: arrives from the same one and rate-limiting by it would lock out the whole crew at once.
MAX_FAILURES = 10
FAILURE_WINDOW = 15 * 60

#: Headers that describe *this* hop and must not be forwarded or copied back. Plus the CORS
#: family: behind the gate the console and the snag service share an origin, so an
#: `Access-Control-Allow-Origin` naming some other port is at best noise and at worst a
#: permission somebody did not mean to grant.
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
              "te", "trailers", "transfer-encoding", "upgrade"}
COPY_BACK = ("Content-Type", "Content-Length", "Cache-Control", "Content-Disposition",
             "Location")

SECURITY_HEADERS = (
    ("Content-Security-Policy",
     "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
     "script-src 'self' 'unsafe-inline'; frame-ancestors 'self'"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "same-origin"),
)

#: Where the MCP server is reached through the gate, so an assistant gets one permanent
#: address instead of a tunnel hostname that changes on every restart. It carries no
#: session: `openboat.mcp_http` has its own token, in a Bearer header or as the first path
#: segment, and the gate is a wire between the two rather than a second opinion about it.
MCP_PREFIX = "/mcp/"
#: A tool call is a few kilobytes of JSON. Nothing here uploads.
MCP_MAX_BODY = 1024 * 1024

#: The pages of this package the console holds in a frame. Served from `openboat/web` at
#: `/b/<key>/<name>`, so that a page inside the frame is inside the session too.
FRAMED = ("index.html", "jobs.html", "windy.html")

#: The only routes on a boat's own server that may be written to through this gate. One
#: entry, and it is the one `openboat.server` itself matches exactly.
WRITABLE = ("/api/logbook",)

#: (path prefix, handler(handler_obj, user_or_None, rest)) → checked before the built-in
#: routes. See the module docstring; this is the documented way to add pages without
#: editing the router.
MOUNTS: list[tuple[str, object]] = []

_failures: dict[str, list[float]] = {}
_names_cache: tuple[float, dict[str, str]] = (0.0, {})


# ── configuration ──────────────────────────────────────────────────────────────────────

class GateError(Exception):
    """Something is missing from the environment. Reported as a sentence, never a traceback."""


def _int(value) -> int:
    """An integer, or 0 for anything that is not one. Used on the session generation,
    which arrives inside a cookie payload and must never be able to raise from there."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def boat_origins() -> dict[str, str]:
    """`{key: origin}` from `$OPENBOAT_GATE_BOATS`, e.g. `"demo=http://127.0.0.1:8747"`.

    The keys are the *folder* keys the snag service uses, and that is not a coincidence: a
    gate that called a boat `demo` while the snag service called it `demo-boat` would serve
    a console whose every photograph is a 404 for a picture that exists.
    """
    out: dict[str, str] = {}
    for chunk in os.environ.get("OPENBOAT_GATE_BOATS", "").split(","):
        key, _, origin = chunk.partition("=")
        if key.strip() and origin.strip():
            out[key.strip()] = origin.strip().rstrip("/")
    return out


def snag_origin() -> str:
    return os.environ.get("OPENBOAT_SNAG_ORIGIN", "http://127.0.0.1:8752").rstrip("/")


def mcp_origin() -> str:
    return os.environ.get("OPENBOAT_MCP_ORIGIN", "http://127.0.0.1:8748").rstrip("/")


def _pairs(name: str) -> dict[str, str]:
    """`{key: value}` from a `key=value,key=value` environment variable."""
    out: dict[str, str] = {}
    for chunk in os.environ.get(name, "").split(","):
        key, _, value = chunk.partition("=")
        if key.strip() and value.strip():
            out[key.strip()] = value.strip()
    return out


def mcp_origins() -> dict[str, str]:
    """`{key: origin}` from `$OPENBOAT_MCP_ORIGINS` — one MCP process per boat.

    A token opens whatever the MCP process behind it can read, and one process that reads
    every boat means one token for every boat. Running a second process on one boat's
    profile alone is what scopes a token to that boat, with no scoping code at all: the
    process cannot name what it was never pointed at. `/mcp/<key>/…` reaches that one.
    """
    return {k: v.rstrip("/") for k, v in _pairs("OPENBOAT_MCP_ORIGINS").items()}


def mcp_connect() -> dict[str, str]:
    """`{key: url}` from `$OPENBOAT_MCP_CONNECT`: the address an owner pastes into an
    assistant to reach that boat. The URL carries the token, so this is handed out only
    to an `owner` or `admin` of that boat, never to crew, and never written to a log."""
    return _pairs("OPENBOAT_MCP_CONNECT")


def users_path() -> Path:
    raw = os.environ.get("OPENBOAT_USERS", "").strip()
    if not raw:
        raise GateError("set OPENBOAT_USERS to the path of the users file")
    return Path(raw).expanduser()


def session_secret() -> bytes:
    raw = os.environ.get("OPENBOAT_SESSION_SECRET", "")
    if len(raw) < 32:
        raise GateError("set OPENBOAT_SESSION_SECRET to 32+ characters — "
                        "`python3 -m openboat.gate secret` prints one")
    return raw.encode()


def gate_secret() -> str:
    return os.environ.get("OPENBOAT_GATE_SECRET", "")


def public_url() -> str:
    return os.environ.get("OPENBOAT_PUBLIC_URL", f"http://localhost:{PORT}").rstrip("/")


# ── users ──────────────────────────────────────────────────────────────────────────────

def load_users() -> list[dict]:
    """Every user, read fresh. Called on every request, and that is the design.

    Caching this would mean a revoked user staying logged in until somebody restarted the
    process, and the moment you want to revoke somebody is never the moment you want to
    restart the thing they are logged into. The file is a few hundred bytes.
    """
    try:
        path = users_path()
    except GateError:
        return []
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        return []
    found = raw.get("users") if isinstance(raw, dict) else None
    return [u for u in (found or []) if isinstance(u, dict) and u.get("email")]


def save_users(users: list[dict]) -> None:
    """Written to a temporary file beside the real one and renamed over it.

    A users file half-written because the disk filled is a users file that locks everybody
    out, including whoever would fix it. `os.replace` is atomic on the same filesystem.
    """
    path = users_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps({"users": users}, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def find_user(email: str) -> dict | None:
    wanted = (email or "").strip().lower()
    for user in load_users():
        if str(user.get("email", "")).strip().lower() == wanted:
            return user
    return None


def hash_password(password: str) -> str:
    """`pbkdf2_sha256$iterations$salt$hash`, all base64. Deliberately slow."""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERS)
    return (f"pbkdf2_sha256${PBKDF2_ITERS}${base64.b64encode(salt).decode()}"
            f"${base64.b64encode(dk).decode()}")


def verify_password(password: str, stored: str) -> bool:
    """Constant-time, and False rather than an exception for anything malformed."""
    try:
        algo, iters, salt_b64, hash_b64 = str(stored or "").split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(),
                                 base64.b64decode(salt_b64), int(iters))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk, base64.b64decode(hash_b64))


# ── signed tokens ──────────────────────────────────────────────────────────────────────

def sign(kind: str, payload: dict, days: int) -> str:
    """`<urlsafe-b64 json>.<hmac>` — the session cookie and the invite link, same shape.

    `kind` is mixed into the MAC rather than only into the payload, so an invite token can
    never be presented as a session cookie even if the payload were somehow interchangeable.
    Two different things that look alike is how a seven-day link becomes a thirty-day one.
    """
    body = dict(payload)
    body["exp"] = int(time.time()) + days * 86400
    blob = base64.urlsafe_b64encode(json.dumps(body, sort_keys=True).encode()).decode()
    return f"{blob}.{_mac(kind, blob)}"


def unsign(kind: str, token: str) -> dict | None:
    """The payload, or None for anything tampered with, expired or the wrong kind."""
    blob, _, mac = str(token or "").partition(".")
    if not blob or not mac or not hmac.compare_digest(mac, _mac(kind, blob)):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(blob + "=" * (-len(blob) % 4)))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or int(payload.get("exp", 0)) < time.time():
        return None
    return payload


def _mac(kind: str, blob: str) -> str:
    return hmac.new(session_secret(), f"{kind}.{blob}".encode(), hashlib.sha256).hexdigest()


# ── inviting somebody ──────────────────────────────────────────────────────────────────

def invite_user(email: str, name: str = "", role: str = "crew",
                boats: list[str] | None = None, by: str = "") -> dict:
    """Add or update a user with **no password of their own**, and hand the record back.

    The one place a user record is created, called by the `invite` command and by the
    People page behind the login. Two places that each built their own record would drift,
    and the first sign of the drift would be a field one of them checks and the other
    never wrote.
    """
    email = str(email).strip().lower()
    users = load_users()
    existing = next((u for u in users if str(u.get("email", "")).lower() == email), None)
    if existing is None:
        existing = {"email": email, "name": name or email, "role": role,
                    "boats": list(boats or []), "hash": "", "session_gen": 0,
                    "created": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    "invited_by": by}
        users.append(existing)
    else:
        if name:
            existing["name"] = name
        existing["role"] = role
        if boats:
            existing["boats"] = sorted(set(existing.get("boats") or []) | set(boats))
    existing["updated_by"] = by
    existing["updated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    save_users(users)
    return existing


def invite_link(email: str) -> str:
    """The link `invite` prints, and the same one the People page shows.

    One function so the two are the same link. A second way of building it is a second
    thing that can be seven days here and thirty there, or signed as the wrong kind.
    """
    token = sign("invite", {"email": str(email).strip().lower()}, INVITE_DAYS)
    return f"{public_url()}/invite/{token}"


# ── the boats a person may see ─────────────────────────────────────────────────────────

def boats_for(user: dict | None) -> list[str]:
    """The keys this user may reach, in the order the gate was configured with them.

    Ordered by configuration rather than by the user record so that two people looking at
    the same install see the same first boat, and so that a key left in a user record after
    the boat was removed from the gate quietly disappears instead of 404ing on login.
    """
    configured = boat_origins()
    if not user:
        return []
    if user.get("role") == "admin":
        return list(configured)
    theirs = {str(k) for k in (user.get("boats") or [])}
    return [key for key in configured if key in theirs]


def boat_names() -> dict[str, str]:
    """`{key: name}` from the snag service, cached for a minute.

    The gate holds no boat facts — it is in a public repository — so the name on the switcher
    comes from the one service that already knows every boat. When it is not answering the
    key is used, which is ugly and honest; inventing a name would be neither.
    """
    global _names_cache
    when, cached = _names_cache
    if time.time() - when < 60:
        return cached
    names: dict[str, str] = {}
    try:
        status, _, body = _request("GET", snag_origin() + "/api/boats", {}, None)
        if status == 200:
            for entry in (json.loads(body) or {}).get("boats", []):
                if entry.get("key"):
                    names[str(entry["key"])] = str(entry.get("name") or entry["key"])
    except (OSError, ValueError, json.JSONDecodeError):
        names = {}
    _names_cache = (time.time(), names)
    return names


# ── the relay itself ───────────────────────────────────────────────────────────────────

def _request(method: str, url: str, headers: dict, body: bytes | None):
    """One upstream call. Returns (status, headers, body); raises OSError when it cannot."""
    parts = urllib.parse.urlsplit(url)
    cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    conn = cls(parts.hostname or "127.0.0.1", parts.port, timeout=RELAY_TIMEOUT)
    try:
        target = parts.path or "/"
        if parts.query:
            target += "?" + parts.query
        conn.request(method, target, body=body, headers=headers)
        resp = conn.getresponse()
        # Read the whole body rather than streaming it. Everything behind this gate is a
        # page, a JSON answer or a photograph, and reading it whole is what lets the gate
        # send a Content-Length it computed itself — so a chunked upstream answer never
        # becomes a chunked answer the relay has to re-frame correctly under HTTP/1.1.
        return resp.status, dict(resp.getheaders()), resp.read(MAX_RESPONSE)
    finally:
        conn.close()


# ── the pages ──────────────────────────────────────────────────────────────────────────

def _page(title: str, inner: str) -> bytes:
    """Every page this module serves. Bootstrap from the vendored copy, no network."""
    return (f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<link rel="stylesheet" href="/vendor/bootstrap.min.css">
</head><body class="bg-body-tertiary">
<main class="container" style="max-width:26rem">
  <div class="py-5">
    <h1 class="h4 fw-semibold mb-1">OpenBoat</h1>
    {inner}
  </div>
</main>
</body></html>""").encode()


def login_page(email: str = "", message: str = "", nxt: str = "") -> bytes:
    note = (f'<div class="alert alert-warning py-2 px-3 small" role="alert">{_esc(message)}'
            "</div>") if message else ""
    return _page("Sign in", f"""
    <p class="text-body-secondary small mb-4">Sign in to reach your boat.</p>
    {note}
    <form method="post" action="/login">
      <input type="hidden" name="next" value="{_esc(nxt)}">
      <div class="mb-3">
        <label class="form-label" for="email">Email</label>
        <input class="form-control" id="email" name="email" type="email"
               autocomplete="username" required autofocus value="{_esc(email)}">
      </div>
      <div class="mb-4">
        <label class="form-label" for="password">Password</label>
        <input class="form-control" id="password" name="password" type="password"
               autocomplete="current-password" required>
      </div>
      <button class="btn btn-primary w-100" type="submit">Sign in</button>
    </form>""")


def invite_page(token: str, name: str, message: str = "") -> bytes:
    note = (f'<div class="alert alert-warning py-2 px-3 small" role="alert">{_esc(message)}'
            "</div>") if message else ""
    return _page("Choose a password", f"""
    <p class="text-body-secondary small mb-4">Welcome{', ' + _esc(name) if name else ''}.
       Choose a password and you are in. Ten characters or more.</p>
    {note}
    <form method="post" action="/invite/{_esc(token)}">
      <div class="mb-3">
        <label class="form-label" for="password">Password</label>
        <input class="form-control" id="password" name="password" type="password"
               autocomplete="new-password" minlength="10" required autofocus>
      </div>
      <div class="mb-4">
        <label class="form-label" for="again">And again</label>
        <input class="form-control" id="again" name="again" type="password"
               autocomplete="new-password" minlength="10" required>
      </div>
      <button class="btn btn-primary w-100" type="submit">Set it and sign in</button>
    </form>""")


def _esc(text) -> str:
    return (str(text if text is not None else "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;"))


def _scrub(text: str) -> str:
    """A log line with the query string and any cookie taken off it.

    Invite tokens and session cookies travel in exactly those two places, and a log file is
    a place people copy out of into an issue. Nothing here is worth logging that a route and
    a status code do not already say.
    """
    out = []
    for word in str(text).split():
        out.append(word.partition("?")[0] if "/" in word else word)
    return " ".join(out)


# ── the handler ────────────────────────────────────────────────────────────────────────

class Gate(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "OpenBoatGate"
    sys_version = ""

    # ── logging: a method, a route and a status. Never a query string, never a cookie.
    def log_request(self, code="-", size="-"):
        route = str(self.path).partition("?")[0]
        # On the MCP route the path *is* the credential — `openboat.mcp_http` accepts the
        # token as its first segment, because a hosted assistant cannot always be made to
        # send a header. So the prefix is logged and never what follows it.
        if route.startswith(MCP_PREFIX):
            route = MCP_PREFIX + "…"
        sys.stderr.write(f"  {self.command} {route} {code}\n")

    def log_message(self, fmt, *args):
        sys.stderr.write("  " + _scrub(fmt % args) + "\n")

    # ── sending ───────────────────────────────────────────────────────────────────────
    def _head(self, status: int, kind: str, length: int, extra=()):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(length))
        for header, value in SECURITY_HEADERS:
            self.send_header(header, value)
        for header, value in extra:
            self.send_header(header, value)
        self.end_headers()

    def send_page(self, body: bytes, status: int = 200, extra=()):
        self._head(status, "text/html; charset=utf-8", len(body), extra)
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, payload, status: int = 200, extra=()):
        body = json.dumps(payload, default=str).encode()
        self._head(status, "application/json; charset=utf-8", len(body),
                   tuple(extra) + (("Cache-Control", "no-store"),))
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_bytes(self, body: bytes, kind: str, status: int = 200, extra=()):
        self._head(status, kind, len(body), extra)
        if self.command != "HEAD":
            self.wfile.write(body)

    def redirect(self, where: str, extra=()):
        self._head(303, "text/html; charset=utf-8", 0, tuple(extra) + (("Location", where),))

    def not_found(self, api: bool = False):
        """404 for everything unknown — *and* for a boat somebody may not see.

        Deliberately not 403. The set of boats this gate serves is not a person's business
        unless they are on one of them, and a status code that distinguishes "no such boat"
        from "not your boat" hands out that set to anybody who asks for keys.
        """
        if api:
            return self.send_json({"error": "not found"}, status=404)
        return self.send_page(_page("Not found",
            '<p class="text-body-secondary">There is nothing here.</p>'
            '<a class="btn btn-outline-secondary btn-sm mt-3" href="/">Back</a>'), status=404)

    # ── reading the request ───────────────────────────────────────────────────────────
    def body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return b""
        return self.rfile.read(min(length, MAX_BODY)) if length > 0 else b""

    def cookies(self) -> dict[str, str]:
        found = {}
        for chunk in (self.headers.get("Cookie", "") or "").split(";"):
            name, _, value = chunk.partition("=")
            if name.strip():
                found[name.strip()] = value.strip()
        return found

    def user(self) -> dict | None:
        """Whoever this request's cookie says it is, if that person still exists.

        Three checks, and the first is the one that does the least: a valid signature only
        proves the cookie was issued here. The lookup proves the account is still on the
        file, so a revoked user holds a perfectly valid cookie and gets nothing. And the
        generation proves this particular cookie has not been signed out from somewhere
        else — `account/logout-all` bumps the number on the record, and every cookie
        carrying the old one stops being a session on its next click.

        A record with no `session_gen` and a cookie with no `gen` are both 0, so every
        cookie issued before this existed keeps working.
        """
        payload = unsign("session", self.cookies().get(SESSION_COOKIE, ""))
        if not payload:
            return None
        record = find_user(str(payload.get("email", "")))
        if not record:
            return None
        if _int(payload.get("gen")) != _int(record.get("session_gen")):
            return None
        return record

    def boats_for(self, user):
        return boats_for(user)

    def _cookie(self, value: str, days: int) -> str:
        bits = [f"{SESSION_COOKIE}={value}", "HttpOnly", "SameSite=Lax", "Path=/",
                f"Max-Age={days * 86400}"]
        if public_url().startswith("https://"):
            bits.append("Secure")
        return "; ".join(bits)

    # ── the router ────────────────────────────────────────────────────────────────────
    def do_GET(self):
        self._dispatch()

    def do_HEAD(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch()

    def do_OPTIONS(self):
        self._dispatch()

    def do_DELETE(self):
        # Only so that the MCP prefix can answer 405 with a sentence rather than the
        # stdlib's 501: a client that tries to close a stream should be told which
        # transport to use, not that this is an unimplemented method.
        self._dispatch()

    def _dispatch(self):
        """Every request, with a floor under it.

        An unhandled exception here would close the connection with no response at all,
        which a browser shows as a page that never loaded and gives nobody anything to act
        on. The exception's own text is not sent on: it is a stack trace from a machine in
        somebody's locker, and this is the one component on a public address.
        """
        try:
            self._route()
        except (BrokenPipeError, ConnectionResetError):
            pass                                   # the browser went away mid-answer
        except Exception as exc:                                        # noqa: BLE001
            self.log_message("unhandled %s", type(exc).__name__)
            try:
                self.send_json({"error": "something here went wrong"}, status=500)
            except OSError:
                pass

    def _route(self):
        route, _, query = self.path.partition("?")
        # Whether a refusal should be a JSON object or a page. A fetch that gets a login
        # page back renders it into the console as a parse error; a browser that gets JSON
        # back where it asked for a page shows the reader a brace.
        api = ("/api/" in route or "/snag/" in route or "/account/" in route
               or route.endswith("/me"))

        wanted = gate_secret()
        if wanted and not hmac.compare_digest(self.headers.get("X-OpenBoat-Gate", ""), wanted):
            # Empty body on purpose. Anything reaching this that is not the relay is not a
            # person who mistyped something, and a helpful error is a description of what
            # this is for whoever is scanning the tunnel hostname.
            self._head(401, "text/plain; charset=utf-8", 0)
            return

        user = self.user()

        for prefix, handler in MOUNTS:
            if route == prefix.rstrip("/") or route.startswith(prefix):
                rest = route[len(prefix):] if route.startswith(prefix) else ""
                try:
                    if handler(self, user, rest):
                        return
                except Exception as exc:                                    # noqa: BLE001
                    self.log_message("mount %s failed: %s", prefix, type(exc).__name__)
                    return self.not_found(api)

        if route == MCP_PREFIX.rstrip("/") or route.startswith(MCP_PREFIX):
            return self.mcp_relay(route[len(MCP_PREFIX.rstrip("/")):])

        if route.startswith("/vendor/"):
            return self.static(route[len("/vendor/"):], WEB / "vendor")

        if route == "/login":
            return self.login(query)
        if route == "/logout":
            return self.redirect("/login", (("Set-Cookie", self._cookie("", 0)),))
        if route.startswith("/invite/"):
            return self.invite(route[len("/invite/"):])

        if route in ("/", ""):
            if not user:
                return self.redirect("/login")
            mine = boats_for(user)
            return self.redirect(f"/b/{mine[0]}/" if mine else "/me")

        if route == "/me":
            return self.me(user, None)

        if route.startswith("/b/"):
            return self.boat_route(user, route, query, api)

        return self.not_found(api)

    # ── login, logout, invite ─────────────────────────────────────────────────────────
    def login(self, query: str):
        params = urllib.parse.parse_qs(query)
        if self.command in ("GET", "HEAD"):
            return self.send_page(login_page(nxt=(params.get("next") or [""])[0]))
        if self.command != "POST":
            return self.not_found()

        form = urllib.parse.parse_qs(self.body().decode("utf-8", "replace"))
        email = (form.get("email") or [""])[0].strip().lower()
        password = (form.get("password") or [""])[0]
        nxt = (form.get("next") or [""])[0]

        # One message for every failure. "No such account" and "wrong password" are two
        # different sentences and the difference is a list of who holds an account here.
        wrong = "That email and password do not match an account."

        recent = [t for t in _failures.get(email, []) if time.time() - t < FAILURE_WINDOW]
        _failures[email] = recent
        if len(recent) >= MAX_FAILURES:
            return self.send_page(login_page(email, "Too many attempts. Try again shortly.",
                                             nxt), status=429)

        user = find_user(email)
        if not user or not user.get("hash") or not verify_password(password, user["hash"]):
            _failures.setdefault(email, []).append(time.time())
            return self.send_page(login_page(email, wrong, nxt), status=401)

        _failures.pop(email, None)
        return self._log_in(user, nxt)

    def _log_in(self, user: dict, nxt: str = ""):
        token = sign("session", {"email": str(user["email"]).lower(),
                                 "gen": _int(user.get("session_gen"))}, SESSION_DAYS)
        # Only a path on this gate is ever followed. `next` arrives from a query string,
        # and a redirect that follows one to wherever it points is an open redirect with a
        # login form in front of it — a phishing page that genuinely lives on your domain.
        where = nxt if nxt.startswith("/") and not nxt.startswith("//") else ""
        if not where:
            mine = boats_for(user)
            where = f"/b/{mine[0]}/" if mine else "/me"
        return self.redirect(where, (("Set-Cookie", self._cookie(token, SESSION_DAYS)),))

    def invite(self, token: str):
        token = token.strip("/")
        payload = unsign("invite", token)
        user = find_user(str((payload or {}).get("email", ""))) if payload else None
        if not user:
            return self.not_found()
        if self.command in ("GET", "HEAD"):
            return self.send_page(invite_page(token, str(user.get("name") or "")))
        if self.command != "POST":
            return self.not_found()

        form = urllib.parse.parse_qs(self.body().decode("utf-8", "replace"))
        password = (form.get("password") or [""])[0]
        again = (form.get("again") or [""])[0]
        if len(password) < 10:
            return self.send_page(invite_page(token, str(user.get("name") or ""),
                                              "Ten characters or more, please."), status=400)
        if password != again:
            return self.send_page(invite_page(token, str(user.get("name") or ""),
                                              "The two did not match."), status=400)

        users = load_users()
        for record in users:
            if str(record.get("email", "")).lower() == str(user["email"]).lower():
                record["hash"] = hash_password(password)
                record["password_set"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        save_users(users)
        return self._log_in(find_user(str(user["email"])) or user)

    # ── who am I ──────────────────────────────────────────────────────────────────────
    def me(self, user, key):
        if not user:
            return self.send_json({"error": "not signed in"}, status=401)
        names = boat_names()
        mine = boats_for(user)
        return self.send_json({
            "name": user.get("name") or user.get("email"),
            "email": user.get("email"),
            "role": user.get("role") or "crew",
            "boat": key if key in mine else None,
            "boats": [{"key": k, "name": names.get(k, k)} for k in mine],
        })

    # ── everything under /b/<key>/ ────────────────────────────────────────────────────
    def boat_route(self, user, route: str, query: str, api: bool):
        rest = route[len("/b/"):]
        key, _, tail = rest.partition("/")
        if not key:
            return self.not_found(api)
        if not tail and not route.endswith("/"):
            # Relative URLs inside console.html resolve against the directory, so the
            # trailing slash is not cosmetic: without it `console/tasks.js` would be asked
            # for at `/b/console/tasks.js` and the console would load with no views.
            return self.redirect(f"/b/{urllib.parse.quote(key)}/")

        if not user:
            if api:
                return self.send_json({"error": "not signed in"}, status=401)
            return self.redirect("/login?next=" + urllib.parse.quote(route))
        if key not in boats_for(user):
            return self.not_found(api)

        if tail in ("", "console.html"):
            return self.static("console.html", WEB)
        if tail in FRAMED:
            # The console's Dashboards view holds these in a frame, and they are files in
            # this package rather than anything a boat generates — so they are served from
            # here, not relayed. Their own `/api/…` calls come back through the gate as
            # `/b/<key>/api/…` like everything else, which is what makes one login enough
            # for a page inside a frame inside the console.
            return self.static(tail, WEB)
        if tail == "me":
            return self.me(user, key)
        if tail == "mcp-connect":
            # The one place the assistant address is readable, and only by somebody who
            # may hand the boat to an assistant: crew get the same 404 as for a boat that
            # is not theirs, because "there is an address and you may not have it" is
            # already more than they need to know.
            if (user.get("role") or "crew") not in ("owner", "admin"):
                return self.not_found(True)
            names = boat_names()
            return self.send_json({"boat": key, "name": names.get(key, key),
                                   "url": mcp_connect().get(key) or None})
        if tail == "account" or tail.startswith("account/"):
            return self.account(user, key, tail[len("account"):])
        if tail.startswith("console/"):
            return self.static(tail[len("console/"):], WEB / "console")
        if tail.startswith("vendor/"):
            return self.static(tail[len("vendor/"):], WEB / "vendor")
        if tail == "snag/api/boats":
            # Answered here rather than relayed. The snag service knows every boat on the
            # machine and would name them all; the phone page inside the frame must see
            # exactly the one boat whose page it is, or a signed-in stranger reads the
            # names of boats they were never given.
            names = boat_names()
            return self.send_json({"boats": [{"key": key, "name": names.get(key, key)}],
                                   "you": user.get("name") or user.get("email") or ""})
        if tail.startswith("snag/"):
            return self.snag_relay(user, key, "/" + tail[len("snag/"):], query)
        if tail.startswith("api/") or tail.startswith("paper") or tail.startswith("reports/"):
            return self.boat_relay(key, "/" + tail, query)
        return self.not_found(api)


    # ── the account ───────────────────────────────────────────────────────────────────
    def account(self, user, key: str, what: str):
        """`/b/<key>/account/…` — a person's own record, and the people page for an admin.

        The boat key in the address is only where the console lives; nothing under here is
        about that boat. Reaching it still needs a boat this person may see, exactly like
        every other path under `/b/<key>/`, because that is the check the session is for.

        **Every write is a JSON POST and has to say so.** `SameSite=Lax` already keeps this
        cookie off a cross-site POST, so the content type is a second lock rather than the
        only one: a cross-site form can send `application/x-www-form-urlencoded`,
        `multipart/form-data` or `text/plain` and nothing else, so a route that accepts
        only `application/json` cannot be driven by one — which still holds on a browser
        old enough, or configured loosely enough, not to enforce Lax. It is a 415 and not
        a 400 because the content type is the thing that is wrong.

        The body is read before the type is checked. An unread body on a keep-alive
        connection is the next request as far as the parser is concerned.
        """
        what = what.strip("/")
        if self.command in ("GET", "HEAD"):
            if what == "users":
                return self.account_users(user)
            return self.not_found(True)
        if self.command != "POST":
            return self.send_json({"error": "method not allowed"}, status=405)

        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = -1
        if length < 0 or length > ACCOUNT_MAX_BODY:
            return self.send_json({"error": "too much data"}, status=413)
        raw = self.rfile.read(length) if length > 0 else b""

        kind = (self.headers.get("Content-Type") or "").partition(";")[0].strip().lower()
        if kind != "application/json":
            return self.send_json({"error": "send this as application/json"}, status=415)

        try:
            payload = json.loads(raw or b"{}")
        except (ValueError, json.JSONDecodeError):
            payload = None
        if not isinstance(payload, dict):
            return self.send_json({"error": "that is not an account change"}, status=400)

        routes = {"name": self.account_name, "password": self.account_password,
                  "invite": self.account_invite, "reissue": self.account_reissue,
                  "revoke": self.account_revoke, "boats": self.account_boats}
        if what == "users":
            # The one route here that is a read. Answering an admin's POST to it with the
            # plain 404 would say it does not exist, and it does — but only for an admin:
            # everybody else keeps the 404, which is the whole point of that answer.
            if not self._admin(user):
                return self.not_found(True)
            return self.send_json({"error": "method not allowed"}, status=405,
                                  extra=(("Allow", "GET"),))
        if what == "logout-all":
            return self.account_logout_all(user)
        if what in routes:
            return routes[what](user, payload)
        return self.not_found(True)

    def _admin(self, user) -> bool:
        """The people page is an admin's. An owner and a crew member get the same 404 as
        for a boat that is not theirs — a 403 would tell them the page is there."""
        return (user.get("role") or "crew") == "admin"

    def _boat_keys(self, raw):
        """`(keys, bad)` — the configured keys in `raw`, in configured order, and whether
        it held one this gate has never heard of. A key that is not configured is refused
        rather than dropped: silently saving four of five boats is a worse answer than no."""
        if raw is None:
            return [], False
        if not isinstance(raw, list):
            return [], True
        configured = boat_origins()
        wanted = {str(k) for k in raw}
        return ([k for k in configured if k in wanted],
                any(k not in configured for k in wanted))

    def _save_user(self, email: str, change, by: str) -> dict | None:
        """Re-read the file, change one record, stamp it, write the whole thing back.

        Re-read rather than edited in place because the record this request is holding was
        loaded at the top of it and the file is shared with the command line: `revoke` in
        a terminal between the two would otherwise be undone by saving a stale list.
        """
        wanted = str(email).strip().lower()
        users = load_users()
        found = None
        for record in users:
            if str(record.get("email", "")).strip().lower() == wanted:
                change(record)
                record["updated_by"] = by
                record["updated_at"] = (datetime.now(timezone.utc)
                                        .replace(microsecond=0).isoformat())
                found = record
        if found is None:
            return None
        save_users(users)
        return found

    def _signed_in(self, email: str, gen: int):
        """A fresh cookie for this browser, on an answer that is not a redirect."""
        token = sign("session", {"email": str(email).lower(), "gen": gen}, SESSION_DAYS)
        return (("Set-Cookie", self._cookie(token, SESSION_DAYS)),)

    # ── your own record ───────────────────────────────────────────────────────────────
    def account_name(self, user, payload):
        name = str(payload.get("name") or "").strip()
        if not 1 <= len(name) <= 60:
            return self.send_json({"error": "a name is between 1 and 60 characters"},
                                  status=400)
        me = str(user.get("email") or "")
        record = self._save_user(me, lambda r: r.__setitem__("name", name), me)
        if not record:
            return self.not_found(True)
        return self.send_json({"ok": True, "name": name})

    def account_password(self, user, payload):
        """The current one, verified; the new one, hashed; a fresh cookie for this browser.

        It does **not** sign the other devices out — "Sign out everywhere else" is its own
        button, because the two are different intentions and a person changing a password
        for tidiness should not be surprised by a phone that has forgotten them.
        """
        email = str(user.get("email") or "").lower()
        current = str(payload.get("current") or "")
        fresh = str(payload.get("password") or "")
        # Checked before the current one, and deliberately not counted as a failure: it is
        # a person mistyping their own new password, not somebody guessing the old one.
        if len(fresh) < 10:
            return self.send_json({"error": "ten characters or more, please"}, status=400)

        # The same count in the same window as the login form, and for the same reason:
        # this route verifies a password, so it is a place to guess one.
        bucket = "password:" + email
        recent = [t for t in _failures.get(bucket, []) if time.time() - t < FAILURE_WINDOW]
        _failures[bucket] = recent
        if len(recent) >= MAX_FAILURES:
            return self.send_json({"error": "too many attempts — try again shortly"},
                                  status=429)
        if not user.get("hash") or not verify_password(current, user["hash"]):
            _failures.setdefault(bucket, []).append(time.time())
            return self.send_json({"error": "that is not the current password"}, status=400)
        _failures.pop(bucket, None)

        stored = hash_password(fresh)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        record = self._save_user(email, lambda r: r.update({"hash": stored,
                                                            "password_set": today}), email)
        if not record:
            return self.not_found(True)
        return self.send_json({"ok": True},
                              extra=self._signed_in(email, _int(record.get("session_gen"))))

    def account_logout_all(self, user):
        """One number up, and every cookie carrying the old one stops being a session.

        No table of sessions to keep, nothing to expire, and it works on a phone that is
        switched off in a locker: the cookie is checked against the record on the next
        click, and the next click is whenever it happens.
        """
        email = str(user.get("email") or "").lower()
        record = self._save_user(email, lambda r: r.__setitem__(
            "session_gen", _int(r.get("session_gen")) + 1), email)
        if not record:
            return self.not_found(True)
        gen = _int(record.get("session_gen"))
        return self.send_json({"ok": True, "session_gen": gen},
                              extra=self._signed_in(email, gen))

    # ── the people page, for an admin ─────────────────────────────────────────────────
    def account_users(self, user):
        if not self._admin(user):
            return self.not_found(True)
        names = boat_names()
        people = [{
            "email": record.get("email"),
            "name": record.get("name") or record.get("email"),
            "role": record.get("role") or "crew",
            "boats": [str(k) for k in (record.get("boats") or [])],
            # Whether a password exists, never anything about what it is. The hash does
            # not leave this process, not even to an admin's own browser.
            "password": bool(record.get("hash")),
            "created": record.get("created") or "",
            "updated_at": record.get("updated_at") or "",
        } for record in sorted(load_users(), key=lambda u: str(u.get("email", "")))]
        return self.send_json({"you": str(user.get("email") or "").lower(),
                               "roles": list(ROLES),
                               "boats": [{"key": k, "name": names.get(k, k)}
                                         for k in boat_origins()],
                               "users": people})

    def account_invite(self, user, payload):
        if not self._admin(user):
            return self.not_found(True)
        email = str(payload.get("email") or "").strip().lower()
        name = str(payload.get("name") or "").strip()
        role = str(payload.get("role") or "crew").strip()
        keys, bad = self._boat_keys(payload.get("boats"))
        if "@" not in email or len(email) > 254 or " " in email:
            return self.send_json({"error": "that does not look like an email address"},
                                  status=400)
        if len(name) > 60:
            return self.send_json({"error": "a name is 60 characters at most"}, status=400)
        if role not in ROLES:
            return self.send_json({"error": "a role is one of " + ", ".join(ROLES)},
                                  status=400)
        if bad:
            return self.send_json({"error": "this gate serves no such boat"}, status=400)
        record = invite_user(email, name, role, keys, by=str(user.get("email") or ""))
        return self.send_json({"ok": True, "email": email,
                               "name": record.get("name") or email,
                               "url": invite_link(email), "days": INVITE_DAYS})

    def account_reissue(self, user, payload):
        if not self._admin(user):
            return self.not_found(True)
        email = str(payload.get("email") or "").strip().lower()
        record = find_user(email)
        if not record:
            return self.send_json({"error": "no such person here"}, status=404)
        if record.get("hash"):
            # A fresh link for somebody who already has a password is a password reset one
            # admin can perform on another person's account from a browser. The command
            # line can still do it, standing at the machine.
            return self.send_json({"error": "that account already has a password"},
                                  status=400)
        return self.send_json({"ok": True, "email": email, "url": invite_link(email),
                               "days": INVITE_DAYS})

    def account_revoke(self, user, payload):
        if not self._admin(user):
            return self.not_found(True)
        email = str(payload.get("email") or "").strip().lower()
        if email == str(user.get("email") or "").lower():
            # An admin who removes their own account has locked the last door with the key
            # on the inside, and the fix is a shell on the machine.
            return self.send_json({"error": "you cannot revoke your own account here"},
                                  status=400)
        users = load_users()
        left = [u for u in users if str(u.get("email", "")).strip().lower() != email]
        if len(left) == len(users):
            return self.send_json({"error": "no such person here"}, status=404)
        save_users(left)
        return self.send_json({"ok": True, "email": email})

    def account_boats(self, user, payload):
        if not self._admin(user):
            return self.not_found(True)
        email = str(payload.get("email") or "").strip().lower()
        keys, bad = self._boat_keys(payload.get("boats"))
        if bad:
            return self.send_json({"error": "this gate serves no such boat"}, status=400)
        record = self._save_user(email, lambda r: r.__setitem__("boats", keys),
                                 str(user.get("email") or ""))
        if not record:
            return self.send_json({"error": "no such person here"}, status=404)
        return self.send_json({"ok": True, "email": email, "boats": keys})

    # ── static files ──────────────────────────────────────────────────────────────────
    def static(self, name: str, root: Path):
        """A file from `openboat/web`, and only from there.

        Resolved and then checked to be inside the directory it was asked of. The gate is
        the one component on a public address; "join a path off the network to a directory"
        without that check is how a web server becomes a file server.
        """
        target = (root / name).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError:
            return self.not_found()
        if not target.is_file():
            return self.not_found()
        kind, _ = mimetypes.guess_type(target.name)
        if target.suffix == ".js":
            kind = "text/javascript"
        elif target.suffix == ".css":
            kind = "text/css"
        return self.send_bytes(target.read_bytes(), kind or "application/octet-stream",
                               extra=(("Cache-Control", "no-cache"),))

    # ── relaying ──────────────────────────────────────────────────────────────────────
    def boat_relay(self, key: str, path: str, query: str):
        """A read from one boat's own dashboard, plus the one write it has ever accepted.

        `openboat.server` matches exactly one POST route, `/api/logbook`, and calls it a
        notebook rather than a control: it appends a line saying somebody looked at
        something. The jobs page inside the Dashboards frame is what writes it. So the
        allow-list here is that same single route spelled out again — not a rule that POST
        is relayed, which is how a read-only thing stops being one, but the one exception
        the server behind it already makes, mirrored so the two cannot drift apart.
        """
        writable = path.partition("?")[0] in WRITABLE
        if self.command not in ("GET", "HEAD") and not (self.command == "POST" and writable):
            return self.send_json({"error": "method not allowed"}, status=405)
        origin = boat_origins().get(key)
        if not origin:
            return self.not_found(True)
        body = self.body() if self.command == "POST" else None
        return self.relay(origin + path + (("?" + query) if query else ""), body)

    def snag_relay(self, user, key: str, path: str, query: str):
        """The write surface, with the boat and the person forced on the way through.

        Everything a logged-in browser could otherwise choose for itself is overwritten
        from things it cannot choose: the boat from the path it was allowed to reach, the
        name from the session cookie. Both in the query *and* in the JSON body, because the
        two services behind this read them from different places — the boat comes out of
        the body, the name out of `by` in the body or `who` in the query depending on the
        route — and forcing one spelling would leave the other as the way round it.

        This is also why the snag service must run with **no** `OPENBOAT_SNAG_PEOPLE`
        behind the gate: with people configured it demands its own `?k=` on every route and
        names the sender from that instead, and the gate holds no such key. Authentication
        happens once, here.
        """
        params = urllib.parse.parse_qs(query, keep_blank_values=True)
        params["boat"] = [key]
        body = None
        if self.command == "POST":
            name = str(user.get("name") or user.get("email") or "")
            params["who"] = [name]
            raw = self.body()
            try:
                parsed = json.loads(raw or b"{}")
                if not isinstance(parsed, dict):
                    raise ValueError("a snag is an object")
            except (ValueError, json.JSONDecodeError):
                return self.send_json({"error": "that is not a snag"}, status=400)
            parsed["boat"] = key
            parsed["by"] = name             # overwritten, never merely trusted
            body = json.dumps(parsed).encode()
        url = snag_origin() + path + "?" + urllib.parse.urlencode(params, doseq=True)
        return self.relay(url, body)

    def mcp_relay(self, rest: str):
        """The MCP server, at one address that outlives the tunnel behind it.

        Deliberately outside the session. An assistant is not a browser and holds no
        cookie; it holds a token, which `openboat.mcp_http` checks for itself, in a Bearer
        header or as the first segment of the path. The gate adds the shared secret with
        the relay and otherwise carries the request through unread. What it must not do is
        *log* it: on the path-token route the URL is the credential.

        POST only. The streamable-HTTP transport answers a tool call with plain JSON and
        needs nothing else; the server-sent-event stream a client may also ask for cannot
        work here, because the thing in front of this gate is a serverless function that
        ends when it answers and cannot hold a connection open for minutes.
        """
        if self.command != "POST":
            return self.send_json(
                {"error": "post to this address; the event-stream transport is not offered "
                          "here, so use streamable HTTP with plain JSON answers"},
                status=405, extra=(("Allow", "POST"),))
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        if length > MCP_MAX_BODY:
            return self.send_json({"error": "too much data"}, status=413)
        body = self.rfile.read(length) if length > 0 else b""

        headers = {"Content-Length": str(len(body))}
        for name in ("Authorization", "Content-Type", "Accept"):
            if self.headers.get(name):
                headers[name] = self.headers[name]

        origin = mcp_origin()
        first = rest.lstrip("/").split("/", 1)[0]
        scoped = mcp_origins()
        if first and first in scoped:
            origin = scoped[first]
            rest = rest.lstrip("/")[len(first):]
        url = origin + (rest or "/")
        try:
            status, upstream, payload = _request("POST", url, headers, body)
        except (OSError, http.client.HTTPException):
            return self.send_json({"error": "the boat's computer is not answering"},
                                  status=502)
        got = {k.lower(): v for k, v in upstream.items()}
        extra = [("WWW-Authenticate", got["www-authenticate"])] if "www-authenticate" in got \
                else []
        return self.send_bytes(payload, got.get("content-type", "application/json"),
                               status=status, extra=extra)

    def relay(self, url: str, body: bytes | None):
        headers = {}
        for name in ("Content-Type", "Accept"):
            if self.headers.get(name):
                headers[name] = self.headers[name]
        if body is not None:
            headers["Content-Type"] = headers.get("Content-Type", "application/json")
            headers["Content-Length"] = str(len(body))
        # No Cookie, no Authorization, no X-OpenBoat-Gate. The session was already spent
        # proving who this is; the boat services have no accounts and nothing downstream
        # should ever see a credential it cannot use and might log.
        headers["Host"] = urllib.parse.urlsplit(url).netloc

        try:
            status, upstream, payload = _request(self.command if self.command != "HEAD"
                                                 else "GET", url, headers, body)
        except (OSError, http.client.HTTPException):
            return self.send_json({"error": "the boat's computer is not answering"},
                                  status=502)

        got = {k.lower(): v for k, v in upstream.items() if k.lower() not in HOP_BY_HOP
               and not k.lower().startswith("access-control-")}
        # Content-Type and Content-Length are set from what actually came back and what is
        # actually being sent on; everything else in COPY_BACK is carried through as given.
        kind = got.get("content-type", "application/octet-stream")
        extra = [(name, got[name.lower()]) for name in COPY_BACK
                 if name not in ("Content-Type", "Content-Length") and name.lower() in got]
        return self.send_bytes(payload, kind, status=status, extra=extra)


# ── the command line ───────────────────────────────────────────────────────────────────

def _flag_values(argv: list[str], flag: str) -> list[str]:
    out = []
    for i, word in enumerate(argv):
        if word == flag and i + 1 < len(argv):
            out.append(argv[i + 1])
    return out


def _flag(argv: list[str], flag: str, default: str = "") -> str:
    found = _flag_values(argv, flag)
    return found[-1] if found else default


def cmd_secret() -> int:
    print(secrets.token_urlsafe(48))
    return 0


def cmd_invite(argv: list[str]) -> int:
    """Add or update a user, then print the link on which they set their own password.

    No password is accepted here and none is generated. A password typed on a command line
    is in the shell history of whoever typed it and in the process list of everybody on the
    machine while it runs, and a password mailed to somebody is a password in a mailbox.
    """
    if not argv:
        print("usage: invite EMAIL --name NAME [--boat KEY]… [--role owner|crew|admin]",
              file=sys.stderr)
        return 2
    email = argv[0].strip().lower()
    name = _flag(argv, "--name")
    role = _flag(argv, "--role", "crew")
    keys = _flag_values(argv, "--boat")
    if "@" not in email:
        print(f"{email!r} does not look like an email address", file=sys.stderr)
        return 2
    if role not in ROLES:
        print(f"--role must be one of {', '.join(ROLES)}", file=sys.stderr)
        return 2

    existing = invite_user(email, name, role, keys, by=os.environ.get("USER", ""))
    link = invite_link(email)
    print(f"{existing['name']} <{email}> — {existing['role']}, "
          f"boats: {', '.join(existing.get('boats') or []) or '(none)'}")
    print(f"\n  {link}\n")
    print(f"Valid {INVITE_DAYS} days, and re-issuable: run this again for a fresh one.",
          file=sys.stderr)
    return 0


def cmd_users() -> int:
    users = load_users()
    if not users:
        print("no users yet — `invite` makes the first one")
        return 0
    width = max(len(str(u.get("email", ""))) for u in users)
    for user in sorted(users, key=lambda u: str(u.get("email", ""))):
        state = "set" if user.get("hash") else "INVITED, no password yet"
        print(f"{str(user.get('email','')):<{width}}  {user.get('role','crew'):<6}  "
              f"{', '.join(user.get('boats') or []) or '(none)':<24}  {state}")
    return 0


def cmd_revoke(argv: list[str]) -> int:
    if not argv:
        print("usage: revoke EMAIL", file=sys.stderr)
        return 2
    email = argv[0].strip().lower()
    users = load_users()
    left = [u for u in users if str(u.get("email", "")).lower() != email]
    if len(left) == len(users):
        print(f"no user {email}", file=sys.stderr)
        return 1
    save_users(left)
    print(f"{email} removed. Their session stops working on their next click — the users "
          f"file is read on every request.")
    return 0


def cmd_boats(argv: list[str]) -> int:
    if not argv:
        print("usage: boats EMAIL [--add KEY]… [--remove KEY]…", file=sys.stderr)
        return 2
    email = argv[0].strip().lower()
    users = load_users()
    user = next((u for u in users if str(u.get("email", "")).lower() == email), None)
    if user is None:
        print(f"no user {email}", file=sys.stderr)
        return 1
    keys = set(user.get("boats") or [])
    keys |= set(_flag_values(argv, "--add"))
    keys -= set(_flag_values(argv, "--remove"))
    user["boats"] = sorted(keys)
    save_users(users)
    print(f"{email}: {', '.join(user['boats']) or '(none)'}")
    return 0


def serve(port: int) -> None:
    configured = boat_origins()
    print(f"OpenBoat gate on http://{BIND}:{port}  (Ctrl-C to stop)", file=sys.stderr)
    print(f"  public URL: {public_url()}", file=sys.stderr)
    print(f"  users:      {users_path()}  ({len(load_users())} known)", file=sys.stderr)
    print(f"  snags:      {snag_origin()}", file=sys.stderr)
    for key, origin in configured.items():
        print(f"  boat {key:<14} → {origin}", file=sys.stderr)
    if not configured:
        print("  no boats — set OPENBOAT_GATE_BOATS to \"key=http://127.0.0.1:8747,…\"",
              file=sys.stderr)
    if not gate_secret():
        print("  OPENBOAT_GATE_SECRET is unset: anything that can reach this port may ask "
              "it for\n  the login page. Set it before putting a tunnel in front.",
              file=sys.stderr)
    try:
        ThreadingHTTPServer((BIND, port), Gate).serve_forever()
    except OSError:
        print(f"Port {port} is in use. Try: python3 -m openboat.gate {port + 1}",
              file=sys.stderr)
        raise SystemExit(2) from None


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    command = argv[0] if argv else ""

    try:
        if command == "secret":
            return cmd_secret()
        if command == "invite":
            session_secret()                      # fail here rather than after writing
            return cmd_invite(argv[1:])
        if command == "users":
            return cmd_users()
        if command == "revoke":
            return cmd_revoke(argv[1:])
        if command == "boats":
            return cmd_boats(argv[1:])
        port = int(argv[0]) if argv and argv[0].isdigit() else PORT
        if argv and not argv[0].isdigit():
            print(f"unknown command {argv[0]!r} — try: invite, users, revoke, boats, secret",
                  file=sys.stderr)
            return 2
        users_path()
        session_secret()
        serve(port)
        return 0
    except GateError as exc:
        print(f"OpenBoat gate cannot start: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0


# Run as `python3 -m openboat.gate` this file is the module `__main__`, and a plain import of
# `openboat.gate` from share.py would execute it a SECOND time as a separate module with its
# own, empty MOUNTS — the share mounts would hang on a copy the server never consults, and
# every share route would 404 while every test that imports the module normally passed.
# Registering this module under its package name first makes the two one and the same.
if __name__ == "__main__":
    sys.modules.setdefault("openboat.gate", sys.modules[__name__])

from . import share as _share            # noqa: E402,F401  — hangs its mounts on MOUNTS

if __name__ == "__main__":
    raise SystemExit(main())
