#!/usr/bin/env python3
"""The snag list: photograph a fault from your phone, on the pontoon, before you forget it.

    python3 -m openboat.snag                 # → http://<this-machine>:8752 on the LAN
    OPENBOAT_BOATS=~/…/boats python3 -m openboat.snag

Every fault on a boat is noticed at a bad moment — halfway through something else, hands
full, in the rain. The note gets made later or not at all, and "the locker by the heads
doesn't shut properly" becomes a thing somebody remembers wrongly in March. This serves one
page to a phone on the same network: pick the boat, photograph the thing, say what is wrong,
send. Ten seconds, standing where the fault is.

**It is a separate service from the dashboard, and that is deliberate.** `openboat.server` is
read-only by construction and accepts exactly one POST, on one exact path, because a boat
dashboard that grows a second write route by accident is how a read-only thing stops being
one. This is the write surface, it runs on its own port, and it can be switched off without
touching anything else.

Three rules shape what it does with what it is given:

**It appends and never rewrites.** Each snag is added to the end of the boat's `SNAGS.md`.
Nothing here edits an existing entry, marks one fixed, or reorders the file. Closing a snag
is done by hand in the file, which is a deliberate act by a person rather than a tap on a
phone in a pocket.

**What arrives is what the owner said.** The note is stored verbatim, stamped with the time
and marked as unverified — recorded from a phone at the moment of noticing, not confirmed by
anybody since. This is the good kind of input for a boat's corpus: the owner writing down
what they saw. It carries its provenance so that later nobody has to guess.

**Photos are resized in the browser before they are sent.** A phone photograph is four
megabytes and a boat's wifi is bad; 1600 px on the long edge is more than enough to see a
cracked fitting, and it makes the difference between a page that works at the end of a
pontoon and one that spins. The resizing happens client-side so the server stays stdlib-only.

The snag file is markdown with one heading per fault, so the boat's own library answers
"what needs fixing" from it with no extra wiring — see `openboat.knowledge`.
"""

from __future__ import annotations

import base64
import json
import os
import re
import socket
import sys
import urllib.parse
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

from .profile import ProfileError, load
from .qr import ascii_art, encode, svg

__all__ = ["boats", "record", "read_snags", "main"]

WEB = Path(__file__).parent / "web"
PORT = 8752
#: Bound to every interface on purpose: the entire point is to reach it from a phone. That
#: also means it is reachable by anything else on the network, so it belongs on a home or
#: boat LAN and not on café wifi. It holds no credentials and serves no read API.
BIND = "0.0.0.0"

#: A phone photo re-encoded at 1600 px is around 300 kB. Twelve of them is a generous snag.
MAX_BODY = 24 * 1024 * 1024

SAFE = re.compile(r"[^a-z0-9]+")


def people() -> dict[str, str]:
    """Who may file, from `$OPENBOAT_SNAG_PEOPLE` — `"skipper:tokenA,crew:tokenB"`.

    Two things at once, and deliberately so. It is the gate, so this can be put on a tunnel
    and reached from somewhere that is not the boat's wifi. And it is the *name*: the link a
    person holds says who they are, so attribution costs them nothing and cannot be filled in
    wrongly by somebody in a hurry.

    Empty when unset, and then the page is open to whoever is on the network and asks for a
    name instead. That is the right default for a machine on a home LAN and the wrong one for
    anything reachable from outside — which is why `main()` says so out loud at startup.
    """
    raw = os.environ.get("OPENBOAT_SNAG_PEOPLE", "")
    out = {}
    for chunk in raw.split(","):
        name, _, token = chunk.partition(":")
        if name.strip() and len(token.strip()) >= 12:
            out[token.strip()] = name.strip()
    return out


def boats() -> list[dict]:
    """Every boat this instance can file against.

    Found by looking for `boat.toml` one level down from `$OPENBOAT_BOATS`, which is how a
    multi-boat setup is laid out on disk. With no such directory it falls back to the single
    profile in `$OPENBOAT_PROFILE`, so a one-boat install needs no configuration at all.
    """
    root = os.environ.get("OPENBOAT_BOATS")
    found = []
    if root:
        for entry in sorted(Path(root).expanduser().glob("*/boat.toml")):
            try:
                boat = load(entry)
            except ProfileError:
                continue
            found.append({"key": entry.parent.name, "name": boat.vessel.name or entry.parent.name,
                          "profile": entry})
    if not found:
        try:
            boat = load()
            found.append({"key": "boat", "name": boat.vessel.name or "This boat",
                          "profile": boat.path})
        except ProfileError:
            pass
    return found


def _slug(text: str, limit: int = 40) -> str:
    return SAFE.sub("-", text.lower()).strip("-")[:limit] or "snag"


def record(boat_key: str, note: str, where: str, images: list[bytes],
           by: str = "", follow_up_to: str = "") -> dict:
    """Append one snag, with its photographs, to the boat's list. Returns what was written.

    `by` is who noticed it. On a boat shared between two people that is not bookkeeping: six
    weeks later the only way to resolve "is this still a problem?" is to ask the person who
    saw it, and an unattributed line cannot be followed up. It is self-declared and therefore
    a claim rather than a proof — which is the same status as everything else in the file.
    """
    known = {b["key"]: b for b in boats()}
    if boat_key not in known:
        raise ValueError(f"unknown boat {boat_key!r}")
    note = note.strip()
    if not note and not images:
        raise ValueError("a snag needs a note or a photograph")

    base = known[boat_key]["profile"].parent
    now = datetime.now().astimezone()
    stamp = now.strftime("%Y-%m-%d-%H%M%S")

    shots = []
    if images:
        shed = base / "photos" / "snags"
        shed.mkdir(parents=True, exist_ok=True)
        for n, blob in enumerate(images, start=1):
            name = f"{stamp}-{_slug(note)}-{n}.jpg"
            (shed / name).write_bytes(blob)
            shots.append(f"photos/snags/{name}")

    who = " ".join(by.split())[:60]
    parent = " ".join(follow_up_to.split())[:32]
    title = (note.splitlines()[0] if note else "photographed, no note")[:70]
    lines = [
        "",
        # Seconds, not minutes. Two snags filed in the same minute produced two entries
        # with the same timestamp, and once entries can point at each other that ambiguity
        # is a bug: a follow-up would attach to whichever of them was parsed first.
        f"## {now:%Y-%m-%d %H:%M:%S} — {title}",
        "",
        f"**Status:** open",
    ]
    if parent:
        # Appending, never rewriting, stays the rule — the fault's history is the sequence
        # of things people wrote about it, in the order they learned them, and rewriting the
        # first entry would lose when the symptom was seen versus when the cause was found.
        # What was missing was only a way to say *these are the same fault*, so that one
        # fault reads as one item and the open count means what it says.
        lines.append(f"**Follow-up to:** {parent}")
    if who:
        lines.append(f"**By:** {who}")
    if where.strip():
        lines.append(f"**Where:** {where.strip()}")
    if shots:
        lines.append("**Photos:** " + ", ".join(f"`{s}`" for s in shots))
    # The note goes in as a blockquote, and that is a correctness measure rather than a
    # typographic one. It is written verbatim into a file that is later parsed back, so a
    # note containing a line `**Status:** fixed` used to close its own snag, and one
    # containing a `## …` line used to forge a whole extra entry that sorted to the top of
    # the list marked fixed. Neither needs malice — pasting a surveyor's markdown into the
    # box does it. Prefixed with "> ", no line inside can begin a heading or a field, and
    # the quotation is also what the text honestly is: what somebody said.
    quoted = "\n".join("> " + line if line.strip() else ">"
                       for line in (note or "_No note — see the photograph._").splitlines())
    lines += [
        "",
        quoted,
        "",
        f"⚠️ Recorded from a phone{' by ' + who if who else ''} at {now:%Y-%m-%d %H:%M %Z} at "
        f"the moment of noticing, and "
        f"not verified by anybody since. Close it by editing **Status** in this file, which "
        f"is a decision a person makes at a desk rather than a tap on a phone.",
    ]

    target = base / "SNAGS.md"
    if not target.exists():
        target.write_text(
            f"# {known[boat_key]['name']} — snag list\n\n"
            "Faults noticed and photographed from a phone, newest at the bottom, appended and\n"
            "never rewritten. One heading per fault so the boat's library answers *what needs\n"
            "fixing* straight out of this file.\n\n"
            "**Every entry is unverified by construction** — it is what somebody saw at the\n"
            "moment they saw it. Close one by changing its **Status** line to `fixed` and\n"
            "saying what was done; nothing automatic will ever edit an entry here.\n",
            encoding="utf-8")
    with open(target, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return {"boat": known[boat_key]["name"], "file": str(target), "photos": shots,
            "title": title, "by": who}


#: A heading line in a snag file: "## 2026-09-05 15:12 — the locker will not shut".
HEADING = re.compile(r"^##\s+(?P<when>\d{4}-\d{2}-\d{2}[^—]*)—\s*(?P<title>.+?)\s*$")
FIELD = re.compile(r"^\*\*(?P<key>Status|Where|Photos|By|Follow-up to):\*\*\s*(?P<value>.*)$")


def read_snags(boat_key: str) -> list[dict]:
    """Parse a boat's SNAGS.md back into entries, newest first.

    Deliberately a *parser over the markdown* rather than a database beside it. The file is
    the truth and a person edits it by hand to close a snag — so anything that read from its
    own copy would show a fault as open after somebody had fixed it and written that down,
    which is the one failure a snag list cannot have.

    Anything it cannot parse is skipped rather than guessed at, and a heading with no Status
    line is reported open: an entry somebody wrote and never marked is not a closed one.
    """
    known = {b["key"]: b for b in boats()}
    if boat_key not in known:
        return []
    target = known[boat_key]["profile"].parent / "SNAGS.md"
    if not target.exists():
        return []

    out: list[dict] = []
    current: dict | None = None
    in_header = False          # fields are only read before the entry's first blank line
    for line in target.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith(">"):
            # Quoted text is somebody's words, never structure. Checked before anything
            # else so that a heading or a field inside a note cannot be seen at all — and
            # it also closes the header block, because the note always follows the fields.
            if current is not None:
                in_header = False
                current["body"].append(line.lstrip("> ").rstrip())
            continue
        head = HEADING.match(line)
        if head:
            if current:
                out.append(current)
            current = {"when": head["when"].strip(), "title": head["title"].strip(),
                       "status": "open", "where": "", "by": "", "follow-up to": "",
                       "photos": [], "body": [], "updates": []}
            in_header = True
            continue
        if current is None:
            continue
        if not line.strip():
            continue                   # blank lines separate the fields from the note
        field = FIELD.match(line) if in_header else None
        if field:
            key, value = field["key"].lower(), field["value"].strip()
            if key == "photos":
                current["photos"] = [s.strip().strip("`") for s in value.split(",") if s.strip()]
            else:
                current[key] = value
            continue
        if line.startswith("⚠️"):
            in_header = False          # the provenance footer; nothing structural follows
            continue
        current["body"].append(line)
    if current:
        out.append(current)

    for entry in out:
        entry["body"] = "\n".join(entry["body"]).strip()
        entry["open"] = not entry["status"].lower().startswith(("fixed", "done", "closed"))
        entry["follow_up_to"] = entry.pop("follow-up to", "")

    # One fault, one item. A follow-up is attached to the entry it names and does not appear
    # in the list in its own right — otherwise the same fault is counted twice and read
    # twice, and "5 open" stops meaning five things are wrong. The status stays the parent's:
    # a follow-up records what somebody learned, it does not close anything.
    # Matched *backwards from the entry's own position*, not through a table keyed by time.
    # A follow-up can only continue something already written, so its parent is the nearest
    # earlier entry bearing that timestamp — which resolves the case of two entries filed
    # inside the same second without inventing a more precise time than actually happened,
    # and without an entry ever adopting itself.
    top = []
    for index, entry in enumerate(out):
        parent = None
        if entry["follow_up_to"]:
            for candidate in reversed(out[:index]):
                if candidate["when"] == entry["follow_up_to"]:
                    parent = candidate
                    break
        if parent is not None:
            parent["updates"].append(entry)
        else:
            top.append(entry)
    for entry in top:
        entry["updates"].sort(key=lambda e: e["when"])
    top.reverse()
    return top


class Snag(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB), **kwargs)

    def log_message(self, fmt, *args):        # quieter: this runs unattended
        sys.stderr.write("  %s\n" % (fmt % args))

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _caller(self, params: dict) -> str | None:
        """The name behind this request, or None when it may not proceed.

        With no people configured everyone is allowed and nobody is named — the LAN case.
        With people configured a valid key is required on every route, including the page
        itself, so a link that is shared is the whole credential and a link that is not
        held gets nothing at all.
        """
        allowed = people()
        if not allowed:
            return ""
        key = (params.get("k") or [""])[0]
        return allowed.get(key)

    def do_GET(self):
        route, _, query = self.path.partition("?")
        params = urllib.parse.parse_qs(query)
        if self._caller(params) is None:
            return self._json({"error": "not found"}, status=404)
        if route == "/api/boats":
            return self._json({"boats": [{"key": b["key"], "name": b["name"]} for b in boats()],
                               "you": self._caller(params)})
        if route == "/api/snags":
            key = (params.get("boat") or [""])[0]
            return self._json(read_snags(key))
        if route == "/qr":
            return self._qr((params.get("k") or [""])[0])
        if route == "/photo":
            return self._photo((params.get("boat") or [""])[0],
                               (params.get("name") or [""])[0])
        if route in ("/", ""):
            # Matched on the parsed route, never on self.path: with a query string attached
            # `/?k=…` missed this branch and fell through to the static handler, which
            # serves this package's *dashboard* out of the same web directory. Following
            # the QR landed you on the wrong application entirely.
            self.path = "/snag.html"
        return super().do_GET()

    def _qr(self, key: str = ""):
        """A page holding one big QR of this server's own LAN address.

        Opened on the machine that runs it, so a phone can be pointed at the screen instead
        of somebody typing an IP address with wet hands. The address is worked out at request
        time rather than at startup because a laptop changes networks.
        """
        # The key travels with the code. Without it the QR sent a phone to a 404, which
        # defeats the only thing the QR is for — and the person scanning it is by definition
        # holding a valid link already, since this page is behind the same gate.
        suffix = f"?k={urllib.parse.quote(key)}" if key else ""
        url = f"http://{_lan_address()}:{self.server.server_address[1]}/{suffix}"
        page = f"""<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Scan to open the snag list</title>
<style>
 html,body{{margin:0;height:100%;display:grid;place-items:center;background:#0d1117;
   color:#e9eef5;font:16px/1.5 -apple-system,system-ui,sans-serif}}
 .card{{text-align:center;padding:32px}}
 svg{{width:min(72vmin,460px);height:auto;border-radius:14px;background:#fff;padding:14px}}
 h1{{font-size:19px;font-weight:600;margin:0 0 22px;letter-spacing:-.01em}}
 code{{display:inline-block;margin-top:22px;font-size:17px;color:#4da3ff;
   font-family:ui-monospace,SFMono-Regular,Menlo,monospace}}
 p{{color:#9aa7b6;font-size:14px;max-width:34em;margin:14px auto 0}}
</style>
<div class=card>
  <h1>Point a phone at this</h1>
  {svg(encode(url), module=8, quiet=3)}
  <div><code>{url}</code></div>
  <p>Both phones need to be on the same network as this machine. Whoever opens it types
     their name once and the page remembers it.</p>
</div>"""
        body = page.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _photo(self, boat_key: str, name: str):
        """Serve one snag photograph, and refuse anything that is not one.

        The name is reduced to its final component and must look like a file this service
        wrote. A path arriving over the network is never joined to a directory as given.
        """
        known = {b["key"]: b for b in boats()}
        leaf = Path(name).name
        if boat_key not in known or not re.fullmatch(r"[A-Za-z0-9._-]{1,120}\.jpg", leaf):
            return self._json({"error": "not found"}, status=404)
        target = known[boat_key]["profile"].parent / "photos" / "snags" / leaf
        if not target.is_file():
            return self._json({"error": "not found"}, status=404)
        blob = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

    def do_POST(self):
        """The only write. Matched exactly, capped, and everything else is a 404."""
        route, _, query = self.path.partition("?")
        params = urllib.parse.parse_qs(query)
        who = self._caller(params)
        if route != "/api/snag" or who is None:
            return self._json({"error": "not found"}, status=404)
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > MAX_BODY:
                return self._json({"error": "too much data"}, status=413)
            body = json.loads(self.rfile.read(length) or b"{}")
            images = [base64.b64decode(s.split(",", 1)[-1]) for s in (body.get("photos") or [])]
            # A configured person's name comes from their link and cannot be typed over;
            # on an open LAN it comes from the page, where it is a courtesy, not a claim.
            written = record(str(body.get("boat", "")), str(body.get("note", "")),
                             str(body.get("where", "")), images,
                             by=who or str(body.get("by", "")),
                             follow_up_to=str(body.get("follow_up_to", "")))
        except ValueError as exc:
            return self._json({"error": str(exc)}, status=400)
        except Exception as exc:                              # noqa: BLE001
            return self._json({"error": f"{type(exc).__name__}: {exc}"}, status=500)
        return self._json({"ok": True, **written})


def _lan_address() -> str:
    """This machine's address on the LAN, which is what has to be typed into the phone."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 80))          # TEST-NET-1: routed nowhere, never sends
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    port = int(argv[0]) if argv else PORT
    known = boats()
    if not known:
        print("No boats found. Set OPENBOAT_BOATS to a directory of boat folders, or "
              "OPENBOAT_PROFILE to one boat.toml.", file=sys.stderr)
        raise SystemExit(2)
    allowed = people()
    url = f"http://{_lan_address()}:{port}/"
    print(file=sys.stderr)
    print(ascii_art(encode(url)), file=sys.stderr)
    print(f"\nSnag list on {url}  — scan the square above, or open {url}qr",
          file=sys.stderr)
    if allowed:
        print(f"  {len(allowed)} people configured; every request needs ?k=<their key>:",
              file=sys.stderr)
        for token, name in allowed.items():
            print(f"    {name:<16} http://{_lan_address()}:{port}/?k={token}", file=sys.stderr)
    else:
        print("  OPEN — anyone who can reach this port may read and file snags. That is fine",
              file=sys.stderr)
        print("  on a home or boat LAN. Before putting it on a tunnel, set OPENBOAT_SNAG_PEOPLE",
              file=sys.stderr)
        print('  to "name:key,name:key" (keys of 12+ characters).', file=sys.stderr)
    for b in known:
        print(f"  {b['name']}  ({b['profile'].parent})", file=sys.stderr)
    try:
        HTTPServer((BIND, port), Snag).serve_forever()
    except OSError as exc:
        print(f"Port {port} is in use. Try: python3 -m openboat.snag {port + 1}",
              file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
