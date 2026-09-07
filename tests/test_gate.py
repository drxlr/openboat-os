#!/usr/bin/env python3
"""The front door: who gets in, what they can reach, and what the relay rewrites.

    python3 tests/test_gate.py

`openboat.gate` is the only component in this project that is meant to be reachable from
the internet, so the checks here are about refusal rather than about features. Four of them
carry most of the weight.

**A boat somebody may not see is a 404, not a 403.** The set of boats one gate serves is
not a stranger's business, and a status code that tells "no such boat" apart from "not your
boat" hands that set to anybody with a keyboard.

**`boat=` and `who=` are forced from the session, in the query and in the body.** They are
the two fields a logged-in browser could otherwise choose for itself, and they are exactly
the two that decide *whose file gets written* and *whose name goes on it*.

**Revocation is immediate.** The users file is read on every request, so a removed user's
perfectly valid cookie stops working on their next click rather than at the next restart.

**With `OPENBOAT_GATE_SECRET` set, nothing without the header gets an answer at all.** The
tunnel is a public hostname; that header is the whole reason it is not a public service.

The Vercel relay is tested here too, in the same file, because it is the other half of the
same path: a request the relay mangles is a request the gate never sees correctly.

No network beyond loopback. Same `check()` idiom as the rest of the suite.
"""

from __future__ import annotations

import contextlib
import http.cookies
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS, FAIL = "  ok  ", "  FAIL"
results: list[tuple[bool, str]] = []


def check(ok: bool, what: str) -> None:
    results.append((bool(ok), what))
    print(f"{PASS if ok else FAIL}  {what}")


@contextlib.contextmanager
def quiet():
    """`invite` prints a link, which is its whole job and not something to read here."""
    with contextlib.redirect_stdout(io.StringIO()):
        yield


SECRET = "a-test-session-secret-of-more-than-32-characters"
PASSWORD = "correct horse battery"


# ── the two services the gate stands in front of ───────────────────────────────────────

class FakeBoat(BaseHTTPRequestHandler):
    """A stand-in for `openboat.server`: records what it was asked and answers JSON."""

    seen: list[tuple[str, str, dict]] = []

    def log_message(self, *a):
        pass

    def _answer(self, payload, status=200, extra=()):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")   # must be stripped by the gate
        self.send_header("X-Boat-Saw", self.path)
        for name, value in extra:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        FakeBoat.seen.append(("GET", self.path, dict(self.headers)))
        self._answer({"route": self.path, "vessel": {"name": "Test Boat"}})

    def do_POST(self):
        FakeBoat.seen.append(("POST", self.path, dict(self.headers)))
        self._answer({"wrote": True})


class FakeSnag(BaseHTTPRequestHandler):
    """A stand-in for `openboat.snag`: echoes the query and the body back."""

    seen: list[dict] = []

    def log_message(self, *a):
        pass

    def _answer(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "keep-alive")           # hop-by-hop; must not survive
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        route, _, query = self.path.partition("?")
        params = urllib.parse.parse_qs(query)
        FakeSnag.seen.append({"method": "GET", "route": route, "params": params})
        if route == "/api/boats":
            return self._answer({"boats": [{"key": "alpha", "name": "Alpha Boat"},
                                           {"key": "beta", "name": "Beta Boat"}]})
        return self._answer({"route": route, "params": params})

    def do_POST(self):
        route, _, query = self.path.partition("?")
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {}
        FakeSnag.seen.append({"method": "POST", "route": route,
                              "params": urllib.parse.parse_qs(query), "body": body})
        return self._answer({"ok": True, "echo": body})

    def do_OPTIONS(self):
        FakeSnag.seen.append({"method": "OPTIONS", "route": self.path.partition("?")[0],
                              "params": {}})
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()


class FakeMcp(BaseHTTPRequestHandler):
    """A stand-in for `openboat.mcp_http`, which takes its token in the path or a header."""

    seen: list[tuple[str, dict]] = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        FakeMcp.seen.append((self.path, dict(self.headers)))
        body = json.dumps({"path": self.path, "asked": raw.decode()}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("WWW-Authenticate", 'Bearer realm="openboat"')
        self.end_headers()
        self.wfile.write(body)


class Running:
    """A handler class on an ephemeral loopback port, for the length of a `with`."""

    def __init__(self, handler):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_port
        self.origin = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.thread.join(timeout=5)
        self.httpd.server_close()


class Env:
    def __init__(self, **kv):
        self.kv, self.old = kv, {}

    def __enter__(self):
        for k, v in self.kv.items():
            self.old[k] = os.environ.get(k)
            os.environ[k] = v
        return self

    def __exit__(self, *exc):
        for k, old in self.old.items():
            os.environ.pop(k, None) if old is None else os.environ.__setitem__(k, old)


class Client:
    """A browser: keeps whatever cookie it was given, and never follows a redirect."""

    def __init__(self, base: str, headers: dict | None = None):
        self.base, self.cookie, self.extra = base, "", headers or {}

    def open(self, path: str, data=None, method=None, headers=None, keep_cookie=True):
        """(status, headers, body-bytes). Redirects are answers here, not detours."""
        body = None
        head = dict(self.extra)
        head.update(headers or {})
        if isinstance(data, dict):
            body = urllib.parse.urlencode(data).encode()
            head["Content-Type"] = "application/x-www-form-urlencoded"
        elif isinstance(data, (bytes, str)):
            body = data.encode() if isinstance(data, str) else data
            head.setdefault("Content-Type", "application/json")
        if self.cookie:
            head["Cookie"] = self.cookie
        request = urllib.request.Request(self.base + path, data=body, headers=head,
                                         method=method or ("POST" if body else "GET"))
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(request, timeout=10) as resp:
                status, got, payload = resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as exc:
            status, got, payload = exc.code, exc.headers, exc.read()
        if keep_cookie:
            for raw in got.get_all("Set-Cookie") or []:
                jar = http.cookies.SimpleCookie()
                jar.load(raw)
                for name, morsel in jar.items():
                    self.cookie = f"{name}={morsel.value}" if morsel.value else ""
        return status, got, payload

    def json(self, path, **kw):
        status, _, body = self.open(path, **kw)
        try:
            return status, json.loads(body or b"null")
        except ValueError:
            return status, None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


def a_gate(tmp: Path, boat_origin: str, snag: str, **extra):
    """The environment one gate runs in, with two boats pointed at the same fake server."""
    return Env(OPENBOAT_USERS=str(tmp / "users.json"),
               OPENBOAT_SESSION_SECRET=SECRET,
               OPENBOAT_GATE_BOATS=f"alpha={boat_origin},beta={boat_origin}",
               OPENBOAT_SNAG_ORIGIN=snag,
               OPENBOAT_PUBLIC_URL="http://127.0.0.1:8749",
               OPENBOAT_GATE_SECRET=extra.pop("secret", ""),
               **extra)


def invite_and_set(gate_module, base: str, email: str, name: str, boats: list[str],
                   role: str = "crew", password: str = PASSWORD) -> Client:
    """Run the real invite flow end to end and hand back a logged-in client."""
    with quiet():
        gate_module.cmd_invite([email, "--name", name, "--role", role]
                               + [x for key in boats for x in ("--boat", key)])
    token = gate_module.sign("invite", {"email": email}, gate_module.INVITE_DAYS)
    client = Client(base)
    client.open(f"/invite/{token}", data={"password": password, "again": password})
    return client


# --------------------------------------------------------------------------------------
def test_invite_login_and_revoke() -> None:
    from openboat import gate

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        with Running(FakeBoat) as boat, Running(FakeSnag) as snags, \
             a_gate(tmp, boat.origin, snags.origin), Running(gate.Gate) as served:
            base = served.origin

            with quiet():
                gate.cmd_invite(["crew@example.org", "--name", "A Crew", "--boat", "alpha"])
            record = gate.find_user("crew@example.org")
            check(record is not None and record["hash"] == "",
                  "invite creates the user with no password of its own")
            check(record and record["boats"] == ["alpha"] and record["role"] == "crew",
                  "with the boat and the role it was given")

            token = gate.sign("invite", {"email": "crew@example.org"}, gate.INVITE_DAYS)
            client = Client(base)
            status, _, _ = client.open(f"/invite/{token}", method="GET")
            check(status == 200, f"the invite link opens a page to choose a password ({status})")

            status, headers, _ = client.open(f"/invite/{token}",
                                             data={"password": PASSWORD, "again": PASSWORD})
            check(status == 303, f"setting it signs the person in ({status})")
            check(headers.get("Location", "").startswith("/b/alpha/"),
                  f"and lands them on their boat ({headers.get('Location')})")
            check(bool(gate.find_user("crew@example.org")["hash"]),
                  "the password is stored as a hash")
            check(gate.find_user("crew@example.org")["hash"].startswith("pbkdf2_sha256$600000$"),
                  "pbkdf2-sha256 at 600,000 iterations")
            check(PASSWORD not in (tmp / "users.json").read_text(),
                  "and the password itself is nowhere in the file")

            status, _, _ = client.open("/b/alpha/me", method="GET")
            check(status == 200, f"the session cookie reaches a boat page ({status})")

            # An invite token that has run out is a 404, not a form.
            expired = gate.sign("invite", {"email": "crew@example.org"}, -1)
            status, _, _ = Client(base).open(f"/invite/{expired}", method="GET")
            check(status == 404, f"an expired invite is refused ({status})")

            forged = gate.sign("session", {"email": "crew@example.org"}, gate.INVITE_DAYS)
            status, _, _ = Client(base).open(f"/invite/{forged}", method="GET")
            check(status == 404,
                  f"and a session cookie cannot be presented as an invite ({status})")

            # Wrong password and unknown email must be indistinguishable.
            _, _, wrong = Client(base).open("/login", data={"email": "crew@example.org",
                                                            "password": "not it"})
            _, _, nobody = Client(base).open("/login", data={"email": "ghost@example.org",
                                                             "password": "not it"})
            check(b"do not match an account" in wrong and b"do not match an account" in nobody,
                  "a wrong password and an unknown email give the same message")
            # The email is echoed back into the form, which is a courtesy to whoever
            # mistyped it. Everything else on the two pages must be identical.
            check(wrong.replace(b"crew@example.org", b"") == nobody.replace(b"ghost@example.org", b""),
                  "and the pages are otherwise byte for byte the same")

            fresh = Client(base)
            status, headers, _ = fresh.open("/login", data={"email": "CREW@example.org",
                                                            "password": PASSWORD})
            check(status == 303 and bool(fresh.cookie),
                  f"the right password signs in, and the email is case-insensitive ({status})")

            # Revocation, and the whole point of re-reading the file every request.
            with quiet():
                gate.cmd_revoke(["crew@example.org"])
            status, _ = fresh.json("/b/alpha/api/profile")
            check(status == 401,
                  f"a revoked user's still-valid cookie stops working at once ({status})")


def test_the_session_cookie_cannot_be_edited() -> None:
    from openboat import gate

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        with Running(FakeBoat) as boat, Running(FakeSnag) as snags, \
             a_gate(tmp, boat.origin, snags.origin), Running(gate.Gate) as served:
            base = served.origin
            client = invite_and_set(gate, base, "owner@example.org", "An Owner", ["alpha"],
                                    role="owner")
            check(bool(client.cookie), "the invite flow leaves a session cookie")

            good = client.cookie.split("=", 1)[1]
            blob, _, mac = good.partition(".")

            tampered = Client(base)
            tampered.cookie = f"{gate.SESSION_COOKIE}={blob}.{'0' * len(mac)}"
            status, _ = tampered.json("/me")
            check(status == 401, f"a re-signed cookie is not a session ({status})")

            unsigned = Client(base)
            unsigned.cookie = f"{gate.SESSION_COOKIE}={blob}"
            status, _ = unsigned.json("/me")
            check(status == 401, f"nor is one with the signature taken off ({status})")

            # A different email in the payload changes the payload, so the MAC no longer fits.
            other = gate.sign("session", {"email": "owner@example.org"}, -1)
            expired = Client(base)
            expired.cookie = f"{gate.SESSION_COOKIE}={other}"
            status, _ = expired.json("/me")
            check(status == 401, f"and an expired one is over ({status})")

            check(gate.unsign("session", good) is not None
                  and gate.unsign("invite", good) is None,
                  "a session and an invite are signed as different kinds of thing")


def test_who_sees_which_boat() -> None:
    from openboat import gate

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        with Running(FakeBoat) as boat, Running(FakeSnag) as snags, \
             a_gate(tmp, boat.origin, snags.origin), Running(gate.Gate) as served:
            base = served.origin
            crew = invite_and_set(gate, base, "crew@example.org", "A Crew", ["alpha"])
            boss = invite_and_set(gate, base, "boss@example.org", "The Admin", [],
                                  role="admin")

            status, _, _ = crew.open("/b/alpha/", method="GET")
            check(status == 200, f"crew reaches the boat they were given ({status})")

            status, _, _ = crew.open("/b/beta/", method="GET")
            check(status == 404, f"and gets a 404, not a 403, on the one they were not ({status})")

            status, _ = crew.json("/b/beta/api/profile")
            check(status == 404, f"the same for its API ({status})")

            status, _ = crew.json("/b/nosuchboat/api/profile")
            check(status == 404,
                  "a boat that does not exist and one they may not see are indistinguishable")

            status, me = crew.json("/b/alpha/me")
            check(status == 200 and me["name"] == "A Crew" and me["role"] == "crew",
                  "/me says who they are")
            check(me and me["boat"] == "alpha" and [b["key"] for b in me["boats"]] == ["alpha"],
                  "and names only the boat they may see")
            check(me and me["boats"][0]["name"] == "Alpha Boat",
                  f"with the name read off the snag service ({me['boats'][0]['name']!r})")

            status, seen = boss.json("/me")
            check(status == 200 and [b["key"] for b in seen["boats"]] == ["alpha", "beta"],
                  "an admin sees every boat this gate is configured with")
            status, _, _ = boss.open("/b/beta/", method="GET")
            check(status == 200, f"and can open one that is in nobody's record ({status})")

            # Signed out: a page redirects to the login, an API answers JSON.
            out = Client(base)
            status, headers, _ = out.open("/b/alpha/", method="GET")
            check(status == 303 and headers.get("Location", "").startswith("/login?next="),
                  f"a signed-out browser is sent to the login carrying where it was going "
                  f"({headers.get('Location')})")
            status, body = out.json("/b/alpha/api/profile")
            check(status == 401 and (body or {}).get("error"),
                  f"and a fetch gets JSON rather than a login page ({status})")

            status, headers, _ = Client(base).open("/", method="GET")
            check(status == 303 and headers.get("Location") == "/login",
                  "the root sends a stranger to the login")
            status, headers, _ = crew.open("/", method="GET")
            check(status == 303 and headers.get("Location") == "/b/alpha/",
                  f"and a person to their boat ({headers.get('Location')})")


def test_the_relay_forces_the_boat_and_the_name() -> None:
    from openboat import gate

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        FakeBoat.seen.clear()
        FakeSnag.seen.clear()
        with Running(FakeBoat) as boat, Running(FakeSnag) as snags, \
             a_gate(tmp, boat.origin, snags.origin), Running(gate.Gate) as served:
            base = served.origin
            crew = invite_and_set(gate, base, "crew@example.org", "A Crew", ["alpha"])

            status, body = crew.json("/b/alpha/api/profile?days=3")
            check(status == 200 and body["route"] == "/api/profile?days=3",
                  f"the /b/<key> prefix is stripped on the way to the boat ({body!r})")

            status, headers, _ = crew.open("/b/alpha/api/profile", method="GET")
            check(headers.get("Access-Control-Allow-Origin") is None,
                  "the boat's CORS header does not survive the relay")
            check(headers.get("Content-Security-Policy", "").startswith("default-src 'self'")
                  and headers.get("X-Content-Type-Options") == "nosniff"
                  and headers.get("Referrer-Policy") == "same-origin",
                  "and every answer carries the security headers")

            # POST to a boat is an allow-list of one route, not a rule. `/api/logbook` is
            # the single write `openboat.server` itself matches, and the gate spells out
            # the same one so the two cannot drift apart.
            status, _ = crew.json("/b/alpha/api/profile", data=b"{}")
            check(status == 405,
                  f"no POST reaches a boat's own server except the one it already takes "
                  f"({status})")

            # The snag service: the boat comes from the path, whatever was asked for.
            status, body = crew.json("/b/alpha/snag/api/snags?boat=beta")
            wrote = FakeSnag.seen[-1]
            check(status == 200 and wrote["params"]["boat"] == ["alpha"],
                  f"a boat named in the query is replaced by the one in the path "
                  f"({wrote['params'].get('boat')})")
            check(wrote["route"] == "/api/snags", "and the prefix is stripped")

            status, body = crew.json(
                "/b/alpha/snag/api/snag?who=Somebody%20Else&boat=beta",
                data=json.dumps({"boat": "beta", "note": "the locker will not shut",
                                 "by": "Somebody Else"}).encode())
            wrote = FakeSnag.seen[-1]
            check(status == 200 and wrote["params"]["who"] == ["A Crew"],
                  f"the sender's name is forced from the session ({wrote['params'].get('who')})")
            check(wrote["body"]["boat"] == "alpha",
                  f"the boat is forced in the body too ({wrote['body'].get('boat')})")
            check(wrote["body"]["by"] == "A Crew",
                  f"and a name typed into the body is overwritten with it, because that "
                  f"is where the snag service reads it ({wrote['body'].get('by')!r})")
            check(wrote["body"]["note"] == "the locker will not shut",
                  "everything else in the note arrives untouched")

            status, _, headers_seen = crew.open("/b/alpha/snag/api/snags", method="GET")
            check(True, "a GET on the snag service is relayed")

            status, _, _ = crew.open("/b/alpha/snag/api/snag", method="OPTIONS")
            check(FakeSnag.seen[-1]["method"] == "OPTIONS",
                  "so is the browser's preflight")

            status, _ = crew.json("/b/alpha/snag/api/snag", data=b"not json at all")
            check(status == 400, f"a body that is not a snag is refused here ({status})")

            # Hop-by-hop headers belong to one hop.
            status, headers, _ = crew.open("/b/alpha/snag/api/snags", method="GET")
            check(headers.get("Keep-Alive") is None,
                  "hop-by-hop headers are not copied back")

            # Nothing the browser sent that could be a credential is passed downstream.
            _, path, sent = FakeBoat.seen[0]
            check("Cookie" not in sent, "the session cookie is never forwarded to a boat")


def test_the_gate_secret_is_the_whole_door() -> None:
    from openboat import gate

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        with Running(FakeBoat) as boat, Running(FakeSnag) as snags, \
             a_gate(tmp, boat.origin, snags.origin, secret="the-relay-knows-this"), \
             Running(gate.Gate) as served:
            base = served.origin

            status, _, body = Client(base).open("/login", method="GET")
            check(status == 401 and body == b"",
                  f"without the header there is no answer and no body ({status}, {body!r})")

            status, _, body = Client(base, {"X-OpenBoat-Gate": "guessing"}).open(
                "/login", method="GET")
            check(status == 401, f"and a wrong one is the same ({status})")

            relay = Client(base, {"X-OpenBoat-Gate": "the-relay-knows-this"})
            status, _, body = relay.open("/login", method="GET")
            check(status == 200 and b"Sign in" in body,
                  f"with it, the login page is served ({status})")


def test_static_files_and_traversal() -> None:
    from openboat import gate

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        with Running(FakeBoat) as boat, Running(FakeSnag) as snags, \
             a_gate(tmp, boat.origin, snags.origin), Running(gate.Gate) as served:
            base = served.origin

            status, headers, body = Client(base).open("/vendor/bootstrap.min.css",
                                                      method="GET")
            check(status == 200 and headers.get("Content-Type") == "text/css",
                  f"the vendored CSS is public — the login page needs it ({status})")

            crew = invite_and_set(gate, base, "crew@example.org", "A Crew", ["alpha"])
            status, headers, body = crew.open("/b/alpha/console.html", method="GET")
            check(status == 200 and b"OpenBoat" in body, "the console itself is served")
            status, headers, body = crew.open("/b/alpha/console/tasks.js", method="GET")
            check(status == 200 and headers.get("Content-Type") == "text/javascript",
                  f"and its view scripts ({status})")

            status, headers, _ = crew.open("/b/alpha", method="GET")
            check(status == 303 and headers.get("Location") == "/b/alpha/",
                  "a missing trailing slash is corrected, or every relative URL misses")

            for attack in ("/vendor/../../gate.py", "/vendor/%2e%2e/server.py"):
                status, _, _ = Client(base).open(attack, method="GET")
                check(status == 404, f"a traversal out of the web directory gets nothing "
                                     f"({attack} → {status})")


def test_the_dashboards_frame_works_behind_the_gate() -> None:
    """The Dashboards view holds the other pages of this project in a frame.

    Under the gate a framed page is at `/b/<key>/jobs.html`, and everything it asks for
    has to come back through the same session — otherwise the console loads, the frame
    loads, and every panel inside it is empty with nothing on screen saying why.
    """
    from openboat import gate

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        FakeBoat.seen.clear()
        with Running(FakeBoat) as boat, Running(FakeSnag) as snags, \
             a_gate(tmp, boat.origin, snags.origin), Running(gate.Gate) as served:
            base = served.origin
            crew = invite_and_set(gate, base, "crew@example.org", "A Crew", ["alpha"])

            for page in gate.FRAMED:
                status, _, body = crew.open(f"/b/alpha/{page}", method="GET")
                check(status == 200 and b"<script" in body,
                      f"{page} is served inside the session ({status})")
                status, _, _ = Client(base).open(f"/b/alpha/{page}", method="GET")
                check(status == 303, f"and not to a stranger ({page} → {status})")

            check(all(b"ROOT" in (ROOT / "openboat" / "web" / page).read_bytes()
                      for page in gate.FRAMED),
                  "every framed page works out where it is mounted")

            # A report is generated HTML with no fetches of its own; it only has to arrive.
            status, body = crew.json("/b/alpha/reports/season.html")
            check(status == 200, f"a generated report is relayed ({status})")

            # The one write any of these pages makes. The boat's own server matches this
            # route exactly and calls it a notebook; the gate mirrors that single entry.
            status, body = crew.json("/b/alpha/api/logbook",
                                     data=b'{"what":"seacocks","verdict":"noted"}')
            check(status == 200 and FakeBoat.seen[-1][0] == "POST",
                  f"the check log is written through the gate ({status})")
            check(FakeBoat.seen[-1][1] == "/api/logbook", "at that exact route")
            status, _ = crew.json("/b/alpha/api/state", data=b"{}")
            check(status == 405, f"and no other route on the boat accepts one ({status})")

            # The phone page inside the frame must see one boat, not the machine's list.
            status, body = crew.json("/b/alpha/snag/api/boats")
            check(status == 200 and [b["key"] for b in body["boats"]] == ["alpha"],
                  f"the boat list a framed page sees is its own boat only ({body})")
            check(body["you"] == "A Crew", "with the reader's name on it")
            check(all(entry["route"] != "/api/boats"
                      for entry in FakeSnag.seen if entry["method"] == "GET"
                      and entry.get("params", {}).get("boat") == ["alpha"]),
                  "answered by the gate rather than relayed to a service that knows them all")


def test_the_mcp_route_is_a_wire_not_a_second_opinion() -> None:
    """One permanent address for an assistant, in front of a tunnel that keeps changing.

    Outside the session on purpose: an assistant holds a token, not a cookie, and
    `openboat.mcp_http` checks that token itself. The gate's job here is to carry the
    request through unread — and, on the path-token route, unlogged.
    """
    from openboat import gate

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        FakeMcp.seen.clear()
        with Running(FakeBoat) as boat, Running(FakeSnag) as snags, \
             Running(FakeMcp) as mcp, \
             a_gate(tmp, boat.origin, snags.origin, OPENBOAT_MCP_ORIGIN=mcp.origin), \
             Running(gate.Gate) as served:
            base = served.origin
            token = "a-token-of-at-least-sixteen-characters"

            # No cookie anywhere: this client has never signed in and never will.
            nobody = Client(base)
            status, body = nobody.json(f"/mcp/{token}/mcp",
                                       data=b'{"method":"tools/list","id":1}')
            check(status == 200 and not nobody.cookie,
                  f"an assistant with no session is answered ({status})")
            check(body and body["path"] == f"/{token}/mcp",
                  f"and the token segment reaches the server intact ({body})")
            check(json.loads(body["asked"])["method"] == "tools/list",
                  "with the body it sent")

            status, _ = nobody.json("/mcp/mcp", data=b"{}",
                                    headers={"Authorization": f"Bearer {token}"})
            check(FakeMcp.seen[-1][1].get("Authorization") == f"Bearer {token}",
                  "a Bearer header is carried through as well")

            status, headers, body = nobody.open(f"/mcp/{token}/mcp", method="GET")
            check(status == 405 and headers.get("Allow") == "POST",
                  f"GET on this prefix is refused ({status})")
            check(b"streamable HTTP" in body,
                  "and says which transport to use instead")
            status, _, _ = nobody.open(f"/mcp/{token}/mcp", method="DELETE")
            check(status == 405, f"so is DELETE ({status})")

            # The gate secret still stands in front of all of it.
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        with Running(FakeBoat) as boat, Running(FakeSnag) as snags, \
             Running(FakeMcp) as mcp, \
             a_gate(tmp, boat.origin, snags.origin, secret="the-relay-knows-this",
                    OPENBOAT_MCP_ORIGIN=mcp.origin), \
             Running(gate.Gate) as served:
            status, _, body = Client(served.origin).open("/mcp/x/mcp", data=b"{}")
            check(status == 401 and body == b"",
                  f"the shared secret applies here like everywhere else ({status})")


def test_the_password_gate_slows_a_guesser_down() -> None:
    from openboat import gate

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        with Running(FakeBoat) as boat, Running(FakeSnag) as snags, \
             a_gate(tmp, boat.origin, snags.origin), Running(gate.Gate) as served:
            base = served.origin
            gate._failures.clear()
            invite_and_set(gate, base, "crew@example.org", "A Crew", ["alpha"])

            seen = []
            for _ in range(gate.MAX_FAILURES + 2):
                status, _, _ = Client(base).open(
                    "/login", data={"email": "crew@example.org", "password": "wrong"})
                seen.append(status)
            check(seen[0] == 401 and seen[-1] == 429,
                  f"ten wrong passwords and that email stops being answered ({seen[-1]})")
            check(seen.count(429) >= 2, "and it stays refused")

            # A different email is a different bucket: one person guessing must not lock
            # the crew out, which is what rate-limiting by address would do behind a relay.
            with quiet():
                gate.cmd_invite(["other@example.org", "--name", "Another", "--boat", "alpha"])
            status, _, _ = Client(base).open(
                "/login", data={"email": "other@example.org", "password": "wrong"})
            check(status == 401, f"another account is unaffected ({status})")
            gate._failures.clear()


def test_the_users_file_is_written_whole() -> None:
    from openboat import gate

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        with a_gate(tmp, "http://127.0.0.1:1", "http://127.0.0.1:1"):
            with quiet():
                gate.cmd_invite(["a@example.org", "--name", "A", "--boat", "alpha"])
            with quiet():
                gate.cmd_invite(["b@example.org", "--name", "B", "--role", "admin"])
            check(len(gate.load_users()) == 2, "two users on the file")

            with quiet():
                gate.cmd_boats(["a@example.org", "--add", "beta", "--remove", "alpha"])
            check(gate.find_user("a@example.org")["boats"] == ["beta"],
                  "boats --add and --remove edit one record")

            # Re-inviting is how a link is re-issued, and it must not reset the password.
            with quiet():
                gate.cmd_invite(["a@example.org", "--name", "A"])
            check(gate.find_user("a@example.org")["boats"] == ["beta"],
                  "re-inviting keeps the boats they had")

            with Env(OPENBOAT_GATE_BOATS=""):
                check(gate.boats_for({"role": "admin"}) == [],
                      "an admin of a gate with no boats configured sees none")
            with Env(OPENBOAT_GATE_BOATS="alpha=http://x,beta=http://y"):
                check(gate.boats_for({"role": "admin"}) == ["alpha", "beta"],
                      "and every one when there are some")
                check(gate.boats_for({"role": "crew", "boats": ["beta", "gone"]}) == ["beta"],
                      "a key left in a record after the boat was removed disappears")

            check(gate.verify_password("x", "nonsense") is False,
                  "a malformed hash verifies as False rather than raising")
            stored = gate.hash_password(PASSWORD)
            check(gate.verify_password(PASSWORD, stored)
                  and not gate.verify_password(PASSWORD + " ", stored),
                  "and a real one round-trips")


# ── the Vercel relay ───────────────────────────────────────────────────────────────────

def _load_relay():
    """Imported by path: `deploy/vercel/` is a Vercel project, not part of the package."""
    path = ROOT / "deploy" / "vercel" / "api" / "relay.py"
    spec = importlib.util.spec_from_file_location("openboat_relay_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeGate(BaseHTTPRequestHandler):
    """A stand-in for the gate: echoes what it was asked and sets two cookies."""

    seen: list[tuple[str, str, dict]] = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        FakeGate.seen.append(("GET", self.path, dict(self.headers)))
        body = json.dumps({"route": self.path}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Set-Cookie", "ob_session=abc; Path=/; HttpOnly")
        self.send_header("Set-Cookie", "ob_other=def; Path=/")
        self.end_headers()
        self.wfile.write(body)

    do_POST = do_GET


def test_the_relay_reconstructs_the_path() -> None:
    relay = _load_relay()

    check(relay.incoming_path("/api/relay?__path=b/alpha/api/docs") == "/b/alpha/api/docs",
          "the rewritten path is put back with its leading slash")
    check(relay.incoming_path("/api/relay?__path=b/alpha/api/ask&q=impeller&limit=5")
          == "/b/alpha/api/ask?q=impeller&limit=5",
          "and the original query survives beside it, in order")
    check(relay.incoming_path("/api/relay?__path=x&f=a&f=b") == "/x?f=a&f=b",
          "including a key given more than once")
    check(relay.incoming_path("/api/relay?__path=") == "/",
          "an empty path is the root")
    check(relay.incoming_path("/api/relay") == "/",
          "so is a direct hit on the function with no rewrite at all")
    check(relay.incoming_path("/api/relay", {"x-vercel-original-path": "/b/alpha/"})
          == "/b/alpha/",
          "and a header is read when the rewrite left nothing")
    check(relay.incoming_path("/b/alpha/api/docs?q=1") == "/b/alpha/api/docs?q=1",
          "a path that arrived unrewritten is used as it stands")


def test_the_relay_carries_every_cookie() -> None:
    relay = _load_relay()
    FakeGate.seen.clear()

    with Running(FakeGate) as fake:
        headers = relay.request_headers({"Cookie": "ob_session=x", "Accept": "text/html",
                                         "Authorization": "Bearer nope"},
                                        "openboat.example", "shared", "203.0.113.9")
        check(headers.get("X-OpenBoat-Gate") == "shared",
              "the relay proves it is the relay")
        check(headers.get("Cookie") == "ob_session=x" and "Authorization" not in headers,
              "it carries the cookie and nothing it was not asked to")
        check(headers.get("X-Forwarded-Proto") == "https"
              and headers.get("X-Forwarded-Host") == "openboat.example"
              and headers.get("X-Forwarded-For") == "203.0.113.9",
              "and says where the request really came from")

        status, out, body = relay.forward(fake.origin, "GET", "/b/alpha/api/docs?q=1",
                                          headers, None)
        cookies = [v for name, v in out if name == "Set-Cookie"]
        check(status == 200, f"the fake gate answers ({status})")
        check(len(cookies) == 2 and cookies[0].startswith("ob_session="),
              f"every Set-Cookie is carried back, not just the last ({len(cookies)})")
        check(json.loads(body)["route"] == "/b/alpha/api/docs?q=1",
              "the path and query arrive at the gate exactly as reconstructed")
        _, path, sent = FakeGate.seen[-1]
        check(sent.get("X-OpenBoat-Gate") == "shared",
              "and the shared secret arrives with them")


def test_the_relay_knows_when_the_boat_is_not_connected() -> None:
    relay = _load_relay()
    relay._origin_cache = (0.0, "")

    with Env(EDGE_CONFIG="", OPENBOAT_ORIGIN=""):
        check(relay.read_origin() == "", "with nothing configured there is no origin")
    relay._origin_cache = (0.0, "")
    with Env(EDGE_CONFIG="", OPENBOAT_ORIGIN="https://example.trycloudflare.com/"):
        check(relay.read_origin() == "https://example.trycloudflare.com",
              "the environment fallback is used, with its trailing slash trimmed")
        check(relay.read_origin() == "https://example.trycloudflare.com",
              "and remembered for the length of the cache")
    relay._origin_cache = (0.0, "")

    check(b"not connected" in relay.NOT_CONNECTED.lower()
          and b"trycloudflare" not in relay.NOT_CONNECTED,
          "the 503 page says the boat is not connected and names no address")
    check(relay.NOT_ANSWERING.startswith(b"<!doctype")
          and b"not answering" in relay.NOT_ANSWERING.lower(),
          "and the 502 page is a page saying the boat is not answering")


def test_the_gate_and_the_relay_hold_no_boat_facts() -> None:
    """Both live in a public repository. Neither may name a boat, a person or a place."""
    from openboat.profile import load

    demo = load(ROOT / "profiles" / "demo-boat.toml")
    for path in (ROOT / "openboat" / "gate.py",
                 ROOT / "deploy" / "vercel" / "api" / "relay.py",
                 ROOT / "scripts" / "gate_tunnel.py"):
        text = path.read_text(encoding="utf-8")
        check(demo.vessel.name not in text,
              f"{path.name} names no vessel, not even the demo boat's")
        check("photo_port" not in text or path.name == "gate.py",
              f"{path.name} holds nothing about the console's internals")


if __name__ == "__main__":
    print(__doc__.splitlines()[0])
    print("-" * 78)
    for case in (test_invite_login_and_revoke,
                 test_the_session_cookie_cannot_be_edited,
                 test_who_sees_which_boat,
                 test_the_relay_forces_the_boat_and_the_name,
                 test_the_gate_secret_is_the_whole_door,
                 test_the_dashboards_frame_works_behind_the_gate,
                 test_the_mcp_route_is_a_wire_not_a_second_opinion,
                 test_static_files_and_traversal,
                 test_the_password_gate_slows_a_guesser_down,
                 test_the_users_file_is_written_whole,
                 test_the_relay_reconstructs_the_path,
                 test_the_relay_carries_every_cookie,
                 test_the_relay_knows_when_the_boat_is_not_connected,
                 test_the_gate_and_the_relay_hold_no_boat_facts):
        case()
    print("-" * 78)
    failed = [what for ok, what in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} checks pass"
          + (f" — FAILED: {failed}" if failed else ""))
    sys.exit(1 if failed else 0)


def test_the_share_mounts_are_installed_when_run_as_a_module(tmp_path):
    """`python3 -m openboat.gate` is `__main__`, and share.py importing `openboat.gate` used to
    get a second copy with an empty MOUNTS — so `/s/…` fell through to the plain 404 and
    the share page never existed on the running server, only under the tests. The share
    mount's own refusal page is distinguishable from the gate's, which is what is checked."""
    import socket, subprocess, sys, time, urllib.request, urllib.error
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
    env = dict(os.environ, OPENBOAT_USERS=str(tmp_path / "users.json"),
               OPENBOAT_SESSION_SECRET="x" * 40, OPENBOAT_GATE_BOATS="demo=http://127.0.0.1:1",
               OPENBOAT_BOATS=str(tmp_path / "boats"), PYTHONPATH=str(ROOT))
    env.pop("OPENBOAT_GATE_SECRET", None)
    proc = subprocess.Popen([sys.executable, "-m", "openboat.gate", str(port)], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        body = b""
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/s/not-a-token", timeout=2)
            except urllib.error.HTTPError as exc:
                body = exc.read(); break
            except OSError:
                time.sleep(0.1)
        check(b"no longer valid" in body,
              "the share mount answers /s/ on a gate started with -m, not the gate's own 404")
    finally:
        proc.kill(); proc.wait()


def test_an_assistant_address_is_per_boat_and_only_for_its_owner() -> None:
    """One MCP process per boat is what scopes a token to a boat, and the gate reaches
    the right one from the first path segment. The address itself — the token — is handed
    out by `/b/<key>/mcp-connect` to an owner or admin of that boat and to nobody else."""
    from openboat import gate

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        FakeMcp.seen.clear()
        with Running(FakeBoat) as boat, Running(FakeSnag) as snags, \
             Running(FakeMcp) as shared, Running(FakeMcp) as beta_only, \
             a_gate(tmp, boat.origin, snags.origin,
                    OPENBOAT_MCP_ORIGIN=shared.origin,
                    OPENBOAT_MCP_ORIGINS=f"beta={beta_only.origin}",
                    OPENBOAT_MCP_CONNECT="alpha=https://x.example/mcp/T1/mcp,"
                                         "beta=https://x.example/mcp/beta/T2/mcp"), \
             Running(gate.Gate) as served:
            base = served.origin
            nobody = Client(base)
            status, body = nobody.json("/mcp/beta/T2/mcp", data=b'{"id":1}')
            check(status == 200 and body["path"] == "/T2/mcp",
                  f"/mcp/<key>/… reaches that boat's own MCP with the key stripped ({body})")
            check(FakeMcp.seen[-1][0] == "/T2/mcp", "and the token segment is intact")
            status, body = nobody.json("/mcp/T1/mcp", data=b'{"id":2}')
            check(status == 200 and body["path"] == "/T1/mcp",
                  "a path without a boat key still goes to the default MCP")

            owner = invite_and_set(gate, base, "own@example.org", "Own", ["beta"], role="owner")
            status, body = owner.json("/b/beta/mcp-connect")
            check(status == 200 and body["url"] == "https://x.example/mcp/beta/T2/mcp",
                  f"an owner gets their boat's address ({status} {body})")
            status, _ = owner.json("/b/alpha/mcp-connect")
            check(status == 404, "and not another boat's, even though one is configured")

            crew = invite_and_set(gate, base, "crew@example.org", "Crew", ["beta"], role="crew")
            status, body = crew.json("/b/beta/mcp-connect")
            check(status == 404 and "url" not in (body or {}),
                  f"crew get a 404 with no address in it ({status} {body})")
