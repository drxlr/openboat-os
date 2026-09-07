#!/usr/bin/env python3
"""The inbox an AI may put things into, and a person takes things out of.

    python3 -m openboat.intake                    # what is waiting to be looked at

An assistant reaching this boat over MCP can now bring paper to it: the manual it found on
the manufacturer's site, the service bulletin somebody linked in a forum, the datasheet for
the part in the photograph. That is genuinely useful and it is also the most dangerous thing
in this project, because of one sentence in `docs/COMPANION.md`:

> A corpus a model both reads and writes is a prompt-injection amplifier.

`openboat.knowledge` answers questions out of the boat's own papers and quotes them with a
file and a line. If a model could put a document into that library, it could put a sentence
into that library, and the next answer would quote the sentence back with a real citation
under a false claim. The citation would be true. The fact would not be.

So the rule this module exists to enforce:

**An assistant is a submitter, never a librarian.** It can put a link or a PDF into
`intake/`. Nothing in `intake/` is searched, quoted, or answerable from. A person looks at
it, and a person moves it into `documents/`, where the library reads it. There is no code
path from a tool call to the library, and that is the whole feature — the rest of this file
is the safety work that makes the submitting part survivable.

## What the fetch will not do

A URL handed to a boat's own server by a model that read it off a web page is a request to
make this machine open a connection somewhere. That is server-side request forgery with an
LLM holding the pen, so the address is checked rather than the name:

* **https only.** No http, no file, no ftp, no data.
* **Every address the host resolves to is checked** — not the first one — and the check is
  repeated on every redirect, because a public name can redirect to a private address and a
  DNS answer can change between the check and the connection.
* Loopback, RFC1918, link-local, carrier-grade NAT, multicast, reserved, IPv6 unique-local
  and the NAT64 prefix are all refused. A boat's server sits on a LAN full of things with
  no authentication at all; the interesting target is never the internet.
* Three redirects, twenty seconds, twenty-five megabytes, and the body must begin `%PDF-`.
  Anything else is refused with "add it as a link" — v1 takes PDFs, and a fetcher that will
  take *anything* is a downloader, which is a different and much worse thing to own.

A PDF that arrives carrying `/JavaScript`, `/OpenAction`, `/Launch` or an embedded file is
**kept and labelled**, not deleted. Refusing it would hide it; the person deciding whether
to accept it is the one who should be told, and the console shows the flags in red. A
flagged file is never served for download by the writer service either.

## What is written where

    <boat>/intake/LINKS.md        appended, never rewritten — one block per link
    <boat>/intake/<id>.pdf        the bytes, until somebody decides
    <boat>/intake/<id>.toml       what is known about them, including the decision
    <boat>/documents/<slug>.pdf   where an accepted file lands, beside the profile
    <boat>/documents/LINKS.md     where an accepted link lands

`id` is the first 16 hex of the file's sha256, so the same document fetched twice is the
same item rather than two, and a link gets a short random one. The sidecar outlives the
bytes: a rejected file's bytes are deleted and its sidecar stays, so the same URL asked for
a second time is answered with "somebody looked at this and said no" instead of being
quietly fetched again.

Accepted documents are picked up by `openboat.knowledge` because everything under
`documents/` is in the library — the owner's profile is the owner's file and nothing here
edits it. Every passage carries where it came from and who let it in, so a model reading
the library later can tell a surveyor's report from something an assistant found on the
internet last Tuesday.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import secrets
import socket
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

__all__ = ["Refused", "add_link", "fetch_document", "items", "item", "accept", "reject",
           "folder", "check_address", "MAX_BYTES", "PER_DAY", "INBOX_CAP"]


class Refused(ValueError):
    """The request was not made, or the answer was not kept. The message says why.

    A `ValueError` on purpose: the snag service already turns one into a 400 with the
    sentence as the body, and a tool over MCP wants the sentence rather than a traceback.
    """


# --- the shape of the thing ------------------------------------------------------------

#: Twenty-five megabytes. Large enough for a scanned engine manual, small enough that a
#: boat's Pi with a phone tethered to it does not fill its card because a model followed a
#: link to a video.
MAX_BYTES = 25 * 1024 * 1024

#: Read in chunks so the cap is enforced *while* the body arrives rather than after it.
CHUNK = 64 * 1024

TIMEOUT = 20
MAX_REDIRECTS = 3

#: Per submitter, per rolling day. Not a security boundary — a submitter with a token is
#: trusted to that extent — but a runaway loop in an agent is a real failure mode and a
#: boat's disk is small.
PER_DAY = 20

#: The whole inbox. Past this, nothing new is taken until somebody decides on what is there.
INBOX_CAP = 500 * 1024 * 1024

#: A PDF may carry things that run. None of these makes a file unsafe to *keep*; every one
#: of them makes it something the person deciding should be told about, in red.
ACTIVE = ("/JavaScript", "/JS", "/Launch", "/OpenAction", "/AA", "/EmbeddedFile",
          "/RichMedia", "/XFA")

#: Only this. A file: URL reads the disk, an http: URL is a plaintext request from the
#: boat's own network, and neither is something a model should be able to ask for.
SCHEME = "https"

SAFE_SLUG = re.compile(r"[^a-z0-9]+")

#: Addresses this machine must never be talked into reaching. Written out rather than
#: leaning on `ipaddress.is_private`, whose membership has changed between Python releases
#: — 100.64.0.0/10 in particular — and a guard that is a different guard on a different
#: interpreter is not a guard.
BLOCKED_V4 = [ipaddress.ip_network(n) for n in (
    "0.0.0.0/8",          # this network
    "10.0.0.0/8",         # RFC1918
    "100.64.0.0/10",      # carrier-grade NAT
    "127.0.0.0/8",        # loopback
    "169.254.0.0/16",     # link-local, and where a cloud metadata service lives
    "172.16.0.0/12",      # RFC1918
    "192.0.0.0/24",       # IETF protocol assignments
    "192.0.2.0/24",       # TEST-NET-1
    "192.168.0.0/16",     # RFC1918
    "198.18.0.0/15",      # benchmarking
    "198.51.100.0/24",    # TEST-NET-2
    "203.0.113.0/24",     # TEST-NET-3
    "224.0.0.0/4",        # multicast
    "240.0.0.0/4",        # reserved, including 255.255.255.255
)]
BLOCKED_V6 = [ipaddress.ip_network(n) for n in (
    "::/128",             # unspecified
    "::1/128",            # loopback
    "64:ff9b::/96",       # NAT64 — reaches an IPv4 address through a translator
    "100::/64",           # discard-only
    "2001:db8::/32",      # documentation
    "fc00::/7",           # unique-local
    "fe80::/10",          # link-local
    "ff00::/8",           # multicast
)]


# --- where a boat's inbox lives ---------------------------------------------------------

def folder(boat: str = "") -> Path:
    """The directory holding this boat's `boat.toml`, which is where everything goes.

    Boats are keyed by their folder exactly as `openboat.snag` keys them, including the
    single-profile fallback, so a machine serving one boat needs no configuration and a
    machine serving several uses the same key everywhere — the console builds its links
    out of that key and a second naming scheme would be a whole class of 404s.
    """
    from . import snag
    from .profile import ProfileError, load

    known = snag.boats()
    if not known:
        raise Refused("no boat is configured on this machine, so there is nowhere to put "
                      "anything. Set OPENBOAT_PROFILE or OPENBOAT_BOATS.")
    keys = [b["key"] for b in known]
    mine = keys[0]
    try:
        here = load().path
        if here:
            mine = next((b["key"] for b in known
                         if Path(b["profile"]).resolve() == here.resolve()), keys[0])
    except ProfileError:
        pass
    key = (boat or mine).strip()
    if key not in keys:
        raise Refused(f"no boat {key!r} here. Boats this machine knows: {', '.join(keys)}.")
    return Path(next(b["profile"] for b in known if b["key"] == key)).parent


def inbox_dir(base: Path) -> Path:
    return base / "intake"


def documents_dir(base: Path) -> Path:
    return base / "documents"


# --- text that came off the network -----------------------------------------------------

def _flat(text: str, limit: int = 200) -> str:
    """One line, no markup, bounded.

    Everything with a `title` or a `reason` on it arrived from a model that read it off a
    web page, and it is written into markdown that is later parsed back — the same hazard
    `openboat.snag` met when a pasted note forged its own `**Status:**` line. Collapsing to
    one line means nothing can begin a heading or a field, and stripping the characters
    markdown gives meaning to means nothing can dress itself up as emphasis or a link.
    """
    one = " ".join(str(text or "").split())
    return one.replace("`", "'").replace("*", "").replace("_", " ").replace("[", "(") \
              .replace("]", ")").replace("#", "").replace("|", "/").strip()[:limit]


def _slug(text: str, limit: int = 60) -> str:
    """A filename, out of an allow-list and nothing else.

    A title from the network never becomes a path by being cleaned up — it becomes a path by
    being *rebuilt* out of characters this function chose. `../../etc/passwd` and `C:\\x`
    both come out of here as ordinary words, because every character not in [a-z0-9] is a
    hyphen and there is no branch that lets one through.
    """
    return SAFE_SLUG.sub("-", str(text or "").lower()).strip("-")[:limit]


def _toml(value) -> str:
    """One TOML value. JSON's string escaping is a subset of TOML's basic string escaping,
    so `json.dumps` is a correct writer here and a hand-rolled one would be a bug farm."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml(v) for v in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# --- the address guard ------------------------------------------------------------------

def _blocked(addr) -> str:
    """Why this address may not be reached, or "" if it may be."""
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        # `::ffff:10.0.0.1` is 10.0.0.1 wearing a hat. Unwrap before judging it.
        return _blocked(addr.ipv4_mapped)
    nets = BLOCKED_V4 if addr.version == 4 else BLOCKED_V6
    for net in nets:
        if addr in net:
            return f"{addr} is in {net}, which is not on the public internet"
    return ""


def check_address(url: str, resolve=None) -> None:
    """Refuse a URL this machine must not open. Raises `Refused`; returns None when clear.

    The check is on the *addresses*, never on the name. A hostname is an attacker-controlled
    string that means whatever its owner's DNS says it means this second, so `getaddrinfo`
    is asked and **every** answer must be acceptable — not the first, because a name with an
    A record for a public address and another for `169.254.169.254` would otherwise pass the
    check and then connect to whichever the resolver felt like.

    It cannot close the window between this answer and the connection the socket makes from
    its own lookup; nothing short of connecting to a pinned address can. What it does close
    is the ordinary case, which is the one that actually happens: a link to a private
    address, and a public host redirecting to one.
    """
    resolve = resolve or socket.getaddrinfo
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError as exc:
        raise Refused(f"that is not a URL this can read: {exc}") from None
    if parts.scheme.lower() != SCHEME:
        raise Refused(f"only {SCHEME}: URLs are fetched, and that one is "
                      f"{parts.scheme or 'no'}:. Add it as a link instead.")
    host = (parts.hostname or "").strip("[]")
    if not host:
        raise Refused("that URL has no host in it.")

    literal = None
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        pass
    if literal is not None:
        why = _blocked(literal)
        if why:
            raise Refused(f"refusing to fetch from {host}: {why}.")
        return

    try:
        infos = resolve(host, parts.port or 443, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise Refused(f"{host} does not resolve: {exc}") from None
    if not infos:
        raise Refused(f"{host} resolves to nothing.")
    for info in infos:
        raw = info[4][0]
        try:
            addr = ipaddress.ip_address(str(raw).split("%")[0])
        except ValueError:
            raise Refused(f"{host} resolves to something that is not an address: {raw!r}")
        why = _blocked(addr)
        if why:
            raise Refused(f"refusing to fetch from {host}: it resolves to {why}.")


# --- the transport ----------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Redirects are followed by this module, one at a time, with the guard in between.

    Letting urllib follow them would mean the second and third connections were made to
    addresses nothing ever checked — which is the entire trick, and the reason a
    same-origin-looking URL is not evidence of anything.
    """

    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)

USER_AGENT = "openboat-intake/1 (+https://github.com/drxlr/openboat-os)"


def _request(url: str, timeout: int = TIMEOUT):
    """One HTTP request, no redirect following. Injected in tests, which is why it is here
    rather than inline: a test server on this machine is by definition at an address the
    guard above refuses, so the two have to be separable."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "application/pdf,*/*;q=0.5"})
    try:
        return _OPENER.open(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        return exc                      # HTTPError is a response: status, headers, read()
    except urllib.error.URLError as exc:
        raise Refused(f"could not reach it: {exc.reason}") from None
    except OSError as exc:
        raise Refused(f"could not reach it: {exc}") from None


REDIRECTS = {301, 302, 303, 307, 308}


def _get_pdf(url: str, transport=None, safe=None) -> tuple[str, bytes, str]:
    """Follow up to three redirects, checking the address before every hop, and return
    `(final_url, body, content_type)`. Everything that can go wrong raises `Refused`."""
    transport = transport or _request
    safe = safe if safe is not None else check_address

    seen = url
    for hop in range(MAX_REDIRECTS + 1):
        safe(seen)
        response = transport(seen, TIMEOUT)
        status = getattr(response, "status", None) or getattr(response, "code", 0)
        if status in REDIRECTS:
            where = response.headers.get("Location", "")
            try:
                response.close()
            except Exception:                                          # noqa: BLE001
                pass
            if not where:
                raise Refused(f"{seen} redirected without saying where to.")
            if hop >= MAX_REDIRECTS:
                raise Refused(f"more than {MAX_REDIRECTS} redirects; giving up at {seen}.")
            seen = urllib.parse.urljoin(seen, where)
            continue
        if status != 200:
            try:
                response.close()
            except Exception:                                          # noqa: BLE001
                pass
            raise Refused(f"the server answered {status} for that URL.")

        kind = (response.headers.get("Content-Type", "") or "").split(";")[0].strip()
        body = bytearray()
        try:
            while True:
                block = response.read(CHUNK)
                if not block:
                    break
                body += block
                if len(body) > MAX_BYTES:
                    raise Refused(
                        f"that file is larger than {MAX_BYTES // (1024 * 1024)} MB and the "
                        f"download was stopped part way. Add it as a link instead.")
                if len(body) >= 5 and not bytes(body[:5]).startswith(b"%PDF-"):
                    raise Refused(
                        "that is not a PDF — the body does not begin `%PDF-`. This takes "
                        "PDFs only; add it as a link instead.")
        finally:
            try:
                response.close()
            except Exception:                                          # noqa: BLE001
                pass
        if not bytes(body).startswith(b"%PDF-"):
            raise Refused("that is not a PDF — the body does not begin `%PDF-`. This takes "
                          "PDFs only; add it as a link instead.")
        return seen, bytes(body), kind
    raise Refused("too many redirects.")


def flags_in(blob: bytes) -> list[str]:
    """The parts of a PDF that can do something. Reported, never removed."""
    return [name.lstrip("/") for name in ACTIVE if name.encode("ascii") in blob]


# --- the sidecars -----------------------------------------------------------------------

ID = re.compile(r"[0-9a-f]{16}")


def _sidecar(inbox: Path, ident: str) -> Path:
    if not ID.fullmatch(ident or ""):
        raise Refused(f"{ident!r} is not an inbox id.")
    return inbox / f"{ident}.toml"


def _read_sidecar(path: Path) -> dict:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except (tomllib.TOMLDecodeError, OSError):
        return {}
    data["id"] = path.stem
    data["kind"] = "file"
    data.setdefault("flags", [])
    data.setdefault("status", "inbox")
    return data


def _write_sidecar(path: Path, data: dict) -> None:
    order = ("url", "title", "reason", "by", "fetched", "sha256", "bytes", "content_type",
             "flags", "status", "decided_by", "decided_at", "decided_why", "document")
    lines = [
        "# Written by openboat/intake.py. This describes something an assistant put into",
        "# this boat's inbox. It is NOT one of the boat's documents until somebody accepts",
        "# it, and nothing here is searched or quoted while it sits in the inbox.",
        "",
    ]
    for key in order:
        if key in data:
            lines.append(f"{key} = {_toml(data[key])}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _sidecars(inbox: Path) -> list[dict]:
    if not inbox.is_dir():
        return []
    out = []
    for path in sorted(inbox.glob("*.toml")):
        if not ID.fullmatch(path.stem):
            continue
        data = _read_sidecar(path)
        if data:
            out.append(data)
    return out


# --- the links file ---------------------------------------------------------------------

LINKS_HEADER = """# Links waiting to be looked at

Put here by an assistant over MCP. **Nothing in this file is one of the boat's documents.**
It is not searched, not quoted and not answered from — a link here is a suggestion that
somebody go and read something, and it has not been opened by anything on this machine.

Appended and never rewritten. A decision on a link is another block further down naming the
same id, so the history of who said what stays intact; the newest decision is the one that
counts.
"""

ACCEPTED_HEADER = """# Links a person accepted

Each of these was suggested by an assistant and then read and accepted by a named person.
The line under each says who brought it, who let it in, and when. **Nobody has opened these
from this machine** — a link is an address, not a document, and its contents are not here.
"""

BLOCK = re.compile(r"^##\s+(?P<head>.+?)\s*$")
BULLET = re.compile(r"^-\s+\*\*(?P<key>[a-z ]+)\*\*:\s*(?P<value>.*)$")


def _links_path(base: Path) -> Path:
    return inbox_dir(base) / "LINKS.md"


def _append_block(path: Path, header: str, head: str, fields: list[tuple[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(header, encoding="utf-8")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"\n## {head}\n\n")
        for key, value in fields:
            if value:
                fh.write(f"- **{key}**: {value}\n")


def _read_links(base: Path) -> list[dict]:
    """Parse the links file back. Later blocks naming the same id update the earlier one.

    A parser over the file rather than a database beside it, for the same reason
    `openboat.snag` is one: a person may edit this by hand, and a copy that disagreed with
    the file would show a link as waiting after somebody had written down that it was not.
    """
    path = _links_path(base)
    if not path.exists():
        return []
    found: dict[str, dict] = {}
    order: list[str] = []
    current: dict = {}
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if BLOCK.match(line):
            current = {}
            continue
        bullet = BULLET.match(line)
        if not bullet or current is None:
            continue
        key, value = bullet["key"].strip().replace(" ", "_"), bullet["value"].strip()
        current[key] = value.strip("`")
        ident = current.get("id", "")
        if not ident:
            continue
        if ident not in found:
            found[ident] = {"kind": "link", "id": ident, "status": "inbox", "flags": [],
                            "bytes": 0, "url": "", "title": "", "reason": "", "by": "",
                            "when": "", "decided_by": "", "decided_at": "",
                            "decided_why": ""}
            order.append(ident)
        entry = found[ident]
        for name in ("url", "title", "reason", "by", "when"):
            if name in current and not entry[name]:
                entry[name] = current[name]
        if "decided" in current:
            entry["status"] = current["decided"]
            entry["decided_by"] = current.get("by", "")
            entry["decided_at"] = current.get("when", "")
            entry["decided_why"] = current.get("why", "")
    return [found[i] for i in order]


# --- quota ------------------------------------------------------------------------------

def _quota(base: Path, by: str) -> None:
    """Refuse in a plain sentence rather than filling a boat's disk quietly."""
    inbox = inbox_dir(base)
    size = sum(p.stat().st_size for p in inbox.glob("*.pdf")) if inbox.is_dir() else 0
    if size >= INBOX_CAP:
        raise Refused(
            f"this boat's inbox already holds {size // (1024 * 1024)} MB, which is the "
            f"limit. Nothing more is taken until somebody accepts or rejects what is "
            f"there.")
    cutoff = datetime.now().astimezone() - timedelta(days=1)
    today = 0
    for entry in _sidecars(inbox) + _read_links(base):
        if (entry.get("by") or "") != by:
            continue
        stamp = entry.get("fetched") or entry.get("when") or ""
        try:
            when = datetime.fromisoformat(stamp)
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.astimezone()
        if when >= cutoff:
            today += 1
    if today >= PER_DAY:
        raise Refused(
            f"{by} has put {today} things into this boat's inbox in the last day, which is "
            f"the limit of {PER_DAY}. Ask the owner to look at those first.")


# --- putting something in ---------------------------------------------------------------

def add_link(url: str, title: str = "", reason: str = "", by: str = "assistant",
             boat: str = "") -> dict:
    """Record a link. It is never opened — not now, not later, not by anything here."""
    base = folder(boat)
    url = str(url or "").strip()
    parts = urllib.parse.urlsplit(url)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise Refused("a link needs to be an http or https URL.")
    by = _flat(by, 60) or "assistant"
    _quota(base, by)

    ident = secrets.token_hex(8)
    when = _now()
    entry = {"kind": "link", "id": ident, "url": url, "title": _flat(title, 120),
             "reason": _flat(reason, 400), "by": by, "when": when, "status": "inbox",
             "flags": [], "bytes": 0, "decided_by": "", "decided_at": "",
             "decided_why": ""}
    _append_block(
        _links_path(base), LINKS_HEADER,
        f"{when[:19]} — {entry['title'] or 'untitled link'}",
        [("id", ident), ("url", f"`{_flat(url, 500)}`"), ("title", entry["title"]),
         ("by", by), ("when", when), ("reason", entry["reason"]), ("status", "inbox")])
    return entry


def fetch_document(url: str, title: str = "", reason: str = "", by: str = "assistant",
                   boat: str = "", transport=None, safe=None) -> dict:
    """Fetch one PDF into the inbox. Never into the library.

    `transport` and `safe` exist for the tests. A local `http.server` is at an address the
    guard refuses by design, so exercising the streaming, the cap, the redirect loop and the
    magic-bytes check against a real socket means being able to hand in a different pair —
    and exercising the guard itself means calling it directly, which the tests also do.
    """
    base = folder(boat)
    url = str(url or "").strip()
    by = _flat(by, 60) or "assistant"
    inbox = inbox_dir(base)

    already = next((s for s in _sidecars(inbox) if s.get("url") == url), None)
    if already and already.get("status") == "rejected":
        raise Refused(
            f"{already.get('decided_by') or 'somebody'} looked at that URL on "
            f"{(already.get('decided_at') or '')[:10]} and rejected it"
            + (f": {already['decided_why']}" if already.get("decided_why") else "")
            + ". It is not fetched again. Say so rather than trying another way in.")
    _quota(base, by)

    final, blob, kind = _get_pdf(url, transport=transport, safe=safe)
    digest = hashlib.sha256(blob).hexdigest()
    ident = digest[:16]

    inbox.mkdir(parents=True, exist_ok=True)
    side = _sidecar(inbox, ident)
    if side.exists():
        known = _read_sidecar(side)
        known["duplicate"] = True
        return known

    (inbox / f"{ident}.pdf").write_bytes(blob)
    entry = {"url": final, "title": _flat(title, 120), "reason": _flat(reason, 400),
             "by": by, "fetched": _now(), "sha256": digest, "bytes": len(blob),
             "content_type": _flat(kind, 80) or "unknown", "flags": flags_in(blob),
             "status": "inbox", "decided_by": "", "decided_at": "", "decided_why": "",
             "document": ""}
    _write_sidecar(side, entry)
    return {"kind": "file", "id": ident, "duplicate": False, **entry}


# --- reading it back --------------------------------------------------------------------

def items(boat: str = "") -> list[dict]:
    """Everything in this boat's inbox, newest first — links and files, with their state."""
    base = folder(boat)
    out: list[dict] = []
    for side in _sidecars(inbox_dir(base)):
        out.append({"kind": "file", "id": side["id"], "url": side.get("url", ""),
                    "title": side.get("title", ""), "reason": side.get("reason", ""),
                    "by": side.get("by", ""), "when": side.get("fetched", ""),
                    "bytes": int(side.get("bytes") or 0),
                    "content_type": side.get("content_type", ""),
                    "sha256": side.get("sha256", ""),
                    "flags": list(side.get("flags") or []),
                    "status": side.get("status", "inbox"),
                    "decided_by": side.get("decided_by", ""),
                    "decided_at": side.get("decided_at", ""),
                    "decided_why": side.get("decided_why", ""),
                    "document": side.get("document", "")})
    out += _read_links(base)
    out.sort(key=lambda e: e.get("when") or "", reverse=True)
    return out


def item(boat: str, ident: str) -> dict | None:
    return next((i for i in items(boat) if i["id"] == ident), None)


# --- a person deciding ------------------------------------------------------------------

def _unique(folder_: Path, stem: str, suffix: str) -> Path:
    target = folder_ / f"{stem}{suffix}"
    n = 2
    while target.exists():
        target = folder_ / f"{stem}-{n}{suffix}"
        n += 1
    return target


def provenance_of(entry: dict, who: str, when: str) -> str:
    """The sentence that travels with an accepted document for the rest of its life."""
    kind = "fetched" if entry.get("kind") == "file" else "submitted"
    return (f"{kind} by {entry.get('by') or 'an assistant'} on "
            f"{(entry.get('when') or '')[:10]} from {entry.get('url') or 'an unnamed URL'}, "
            f"accepted by {who} on {when[:10]}. Unverified: nobody has checked it against "
            f"the boat.")


def accept(boat: str, ident: str, who: str) -> dict:
    """Move one inbox item into the boat's documents. The only way anything gets there.

    A person's name is required and is written into the document itself, because the whole
    point of the inbox is that the step from "a model found this" to "this is one of the
    boat's papers" has somebody's name on it.
    """
    who = _flat(who, 60)
    if not who:
        raise Refused("say who is accepting this. A document entering the boat's library "
                      "carries the name of the person who let it in.")
    base = folder(boat)
    entry = item(boat, ident)
    if entry is None:
        raise Refused(f"nothing in this boat's inbox has the id {ident!r}.")
    if entry["status"] != "inbox":
        raise Refused(f"that was already {entry['status']}"
                      + (f" by {entry['decided_by']}" if entry.get("decided_by") else "")
                      + ".")
    when = _now()
    docs = documents_dir(base)
    docs.mkdir(parents=True, exist_ok=True)
    said = provenance_of(entry, who, when)

    if entry["kind"] == "link":
        _append_block(
            docs / "LINKS.md", ACCEPTED_HEADER,
            f"{entry['title'] or 'untitled link'}",
            [("url", f"`{_flat(entry['url'], 500)}`"),
             ("reason", entry.get("reason", "")), ("provenance", said)])
        _append_block(
            _links_path(base), LINKS_HEADER, f"{when[:19]} — decision on {ident}",
            [("id", ident), ("decided", "accepted"), ("by", who), ("when", when)])
        return {"id": ident, "kind": "link", "status": "accepted", "by": who,
                "at": when, "document": "documents/LINKS.md"}

    inbox = inbox_dir(base)
    source = inbox / f"{ident}.pdf"
    if not source.is_file():
        raise Refused(f"the file for {ident} is not on disk any more.")
    stem = _slug(entry.get("title") or "") or f"document-{ident}"
    target = _unique(docs, stem, ".pdf")
    target.write_bytes(source.read_bytes())
    source.unlink()

    extracted, why = "", ""
    try:
        from . import ingest

        found = ingest.extract(target)
        text = target.with_suffix(".md")
        text.write_text(ingest.to_markdown(found, provenance=said), encoding="utf-8")
        extracted = text.name
    except Exception as exc:                                           # noqa: BLE001
        # No extractor on this machine, or a PDF none of them can read. The paper is in the
        # library either way and `openboat.knowledge` answers with the reason it cannot be
        # read and the command that fixes it — which is better than a silent gap.
        why = f"{type(exc).__name__}: {exc}"

    side = _sidecar(inbox, ident)
    known = _read_sidecar(side)
    known.update({"status": "accepted", "decided_by": who, "decided_at": when,
                  "decided_why": "", "document": target.name})
    known.pop("id", None)
    known.pop("kind", None)
    _write_sidecar(side, known)
    return {"id": ident, "kind": "file", "status": "accepted", "by": who, "at": when,
            "document": target.name, "text": extracted, "not_extracted": why,
            "provenance": said}


def reject(boat: str, ident: str, why: str, who: str) -> dict:
    """Say no, in writing. The bytes go; the record of the decision stays.

    Deleting the sidecar too would mean the same URL could be fetched again five minutes
    later and nothing would know it had already been looked at and turned down.
    """
    who = _flat(who, 60)
    why = _flat(why, 400)
    if not who:
        raise Refused("say who is rejecting this.")
    if not why:
        raise Refused("say why. A rejection with no reason cannot be argued with later, "
                      "and this is the record of the decision.")
    base = folder(boat)
    entry = item(boat, ident)
    if entry is None:
        raise Refused(f"nothing in this boat's inbox has the id {ident!r}.")
    if entry["status"] != "inbox":
        raise Refused(f"that was already {entry['status']}.")
    when = _now()

    if entry["kind"] == "link":
        _append_block(
            _links_path(base), LINKS_HEADER, f"{when[:19]} — decision on {ident}",
            [("id", ident), ("decided", "rejected"), ("by", who), ("when", when),
             ("why", why)])
        return {"id": ident, "kind": "link", "status": "rejected", "by": who, "at": when}

    inbox = inbox_dir(base)
    blob = inbox / f"{ident}.pdf"
    if blob.exists():
        blob.unlink()
    side = _sidecar(inbox, ident)
    known = _read_sidecar(side)
    known.update({"status": "rejected", "decided_by": who, "decided_at": when,
                  "decided_why": why, "bytes": 0})
    known.pop("id", None)
    known.pop("kind", None)
    _write_sidecar(side, known)
    return {"id": ident, "kind": "file", "status": "rejected", "by": who, "at": when,
            "why": why}


def file_bytes(boat: str, ident: str) -> tuple[bytes, str]:
    """The PDF of one waiting item, for a person to look at before deciding.

    Only while it is waiting, and only when nothing in it can run. A flagged file is not
    served at all: the point of the download is to let somebody read the thing they are
    about to accept, and handing a browser a PDF carrying `/OpenAction` to make that easier
    would be the exact trade this module exists not to make.
    """
    base = folder(boat)
    entry = item(boat, ident)
    if entry is None or entry["kind"] != "file":
        raise Refused("no such file in this boat's inbox.")
    if entry["status"] != "inbox":
        raise Refused(f"that was {entry['status']}; the inbox no longer serves it.")
    if entry["flags"]:
        raise Refused("that file carries active content ("
                      + ", ".join(entry["flags"]) +
                      ") and is not served for download. Open it somewhere safe, or reject "
                      "it.")
    path = inbox_dir(base) / f"{ident}.pdf"
    if not path.is_file():
        raise Refused("the bytes are not on disk any more.")
    return path.read_bytes(), (entry.get("title") or ident)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    key = argv[0] if argv else ""
    try:
        found = items(key)
    except Refused as exc:
        print(exc, file=sys.stderr)
        return 2
    if not found:
        print("Nothing in the inbox.")
        return 0
    for entry in found:
        flags = ("  ⚠ " + ", ".join(entry["flags"])) if entry["flags"] else ""
        print(f"  [{entry['status']:<8}] {entry['kind']:<4} {entry['id']}  "
              f"{(entry['title'] or entry['url'])[:60]}"
              f"  (by {entry['by'] or '?'}){flags}")
    print("\nNothing above is one of the boat's documents. Accept one in the console.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
