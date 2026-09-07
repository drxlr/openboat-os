#!/usr/bin/env python3
"""One fault, one link, one person: handing a job to somebody who has no login.

The owner shows a friend the faults found on the friend's boat. The friend wants one of them
fixed and the person who is going to fix it is at a yard, on a phone, and is never going to
hold an account on anybody's boat computer. Giving them one would be worse than useless: an
account is a thing that has to be revoked later by somebody who has forgotten it exists.

So this mints a link to **one fault**, good for a set number of days, which opens a page
showing that fault and its photographs and offers exactly two answers back — *I have looked
at it* and *it is fixed* — with a note and a name. Nothing else on the boat is reachable
through it, and no other write is possible with it.

## The token, and why it is still revocable

    urlsafe-b64( {"boat", "when", "exp", "label", "id"} ) . hmac-sha256

Signed with the gate's own `$OPENBOAT_SESSION_SECRET`, so it stands or falls with everything
else that gate issues, and verified in constant time. A stateless token needs no table to be
checked against, which is the whole reason to use one — but a link you cannot take back is
not a link anybody should hand out.

So the grant is **written into the boat's own file**, as a follow-up on the fault:

    **Share:**   3f8a91c2 2026-10-07T18:00:00+03:00 Jo from the yard
    **Unshare:** 3f8a91c2

`snag.read_snags()` rolls those up onto the fault as `shares`, and a token is refused unless
its `id` is listed there and not revoked. Revocation is therefore the same act as everything
else in this project — one more line appended to a markdown file — and *who was given access*
is readable by a person, in the file the fault is in, rather than being a row in a database
beside it that nobody will ever look at.

## What it hangs off

Two mounts on `openboat.gate` (see `gate.MOUNTS`), installed by importing this module:

    POST /b/<key>/snag/api/share    mint one. `owner` and `admin` only; `crew` gets a 404
    GET  /s/<token>                 the page, with no login at all
    GET  /s/<token>/photo?name=     one photograph, relayed, with the boat forced
    POST /s/<token>/update          one follow-up, and nothing else

**This module reads and appends to the boat's own files in this process**, so the gate must
be started with `$OPENBOAT_BOATS` (or `$OPENBOAT_PROFILE`) pointing at them, exactly as the
snag service is. Without it a share link is minted against nothing and every one of these
routes is an honest 404. Photographs are the exception: they are relayed to the snag
service, because that route already knows how to hand a browser a picture.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

from . import gate, snag

__all__ = ["mint", "verify", "grant", "resolve", "install",
           "MAX_DAYS", "DEFAULT_DAYS", "MAX_POSTS_A_DAY"]

#: Long enough for a job at a yard, short enough that a forgotten link expires on its own.
DEFAULT_DAYS = 30
MAX_DAYS = 90

#: Somebody with the link can append follow-ups. Thirty in a day is far more than a person
#: fixing one fault will ever write, and it is the difference between a link somebody was
#: careless with and a way to fill a boat's file with rubbish.
MAX_POSTS_A_DAY = 30

MAX_LABEL = 60
MAX_NAME = 30
MAX_NOTE = 4000

#: What a share link may report back. `open` is not here on purpose: this is a page for
#: somebody doing the work, and "I am putting it back to open" is a conversation, not a tap.
ANSWERS = (("review", "I have looked at it"), ("fixed", "It is fixed"))

#: id → the times it posted, most recent last. Per process and lost on restart, which is the
#: right lifetime for a limit whose only job is to stop a runaway.
_posts: dict[str, list[float]] = {}


# ── the token ──────────────────────────────────────────────────────────────────────────

def _key(secret) -> bytes:
    return secret if isinstance(secret, bytes) else str(secret or "").encode()


def _mac(secret, blob: str) -> str:
    # "share." is mixed in the way `gate.sign` mixes its own kind, so a session cookie and a
    # share token can never be presented as one another however alike the payloads look.
    return hmac.new(_key(secret), f"share.{blob}".encode(), hashlib.sha256).hexdigest()


def _sign(secret, payload: dict) -> str:
    blob = base64.urlsafe_b64encode(
        json.dumps(payload, sort_keys=True).encode()).decode().rstrip("=")
    return f"{blob}.{_mac(secret, blob)}"


def days(asked) -> int:
    """Whatever was asked for, brought inside 1…90. A link with no end is not a link."""
    try:
        return max(1, min(MAX_DAYS, int(asked)))
    except (TypeError, ValueError):
        return DEFAULT_DAYS


def clean(text, limit: int) -> str:
    """One line, no runs of space, capped. Everything that reaches a file goes through here."""
    return " ".join(str(text if text is not None else "").split())[:limit]


def mint(secret, boat: str, when: str, days_asked=DEFAULT_DAYS, label: str = "") -> str:
    """A token for one fault on one boat, good for `days_asked` days."""
    return _sign(secret, {
        "boat": str(boat), "when": str(when),
        "exp": int(time.time()) + days(days_asked) * 86400,
        "label": clean(label, MAX_LABEL),
        # Not the whole credential — the signature is — but the handle the file writes down
        # and revocation names. Random so that two links to the same fault are two links.
        "id": secrets.token_hex(4),
    })


def verify(secret, token) -> dict | None:
    """The payload, or None for anything tampered with, expired or of another kind."""
    blob, _, mac = str(token or "").partition(".")
    if not blob or not mac or not hmac.compare_digest(mac, _mac(secret, blob)):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(blob + "=" * (-len(blob) % 4)))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not payload.get("boat") or not payload.get("id"):
        return None
    try:
        if int(payload["exp"]) < time.time():
            return None
    except (KeyError, TypeError, ValueError):
        return None
    return payload


# ── minting one, and writing it down ───────────────────────────────────────────────────

def grant(secret, boat: str, when: str, label: str, days_asked, by: str) -> dict:
    """Mint a link and record it as a follow-up on the fault. Returns token, id and expiry.

    The record is the point. A token nobody wrote down is a key cut in the dark: it works
    until it expires and there is no way to know it exists, let alone take it back.
    """
    label = clean(label, MAX_LABEL) or "somebody"
    token = mint(secret, boat, when, days_asked, label)
    payload = verify(secret, token) or {}
    until = datetime.fromtimestamp(payload["exp"]).astimezone()
    stamp = until.isoformat(timespec="seconds")
    snag.record(boat, f"Shared with {label} until {until:%Y-%m-%d} "
                      f"(link {payload['id']}).", "", [],
                by=clean(by, 60), follow_up_to=when,
                share=f"{payload['id']} {stamp} {label}")
    return {"token": token, "id": payload["id"], "until": stamp, "label": label}


def fault(boat: str, when: str) -> dict | None:
    """One fault of one boat, read back out of its file. None when there is no such thing."""
    for entry in snag.read_snags(boat):
        if entry["when"] == when:
            return entry
    return None


def resolve(token) -> tuple[dict, dict] | None:
    """(payload, the fault) for a link that still opens, or None.

    Three things have to hold and each is a different way a link dies: the signature, the
    expiry inside it, and the file still listing that id un-revoked. The last is the one
    that makes a stateless token revocable.
    """
    try:
        secret = gate.session_secret()
    except gate.GateError:
        return None
    payload = verify(secret, token)
    if not payload:
        return None
    entry = fault(payload["boat"], payload["when"])
    if not entry:
        return None
    for given in entry.get("shares", []):
        if given["id"] == payload["id"]:
            return None if given["revoked"] else (payload, entry)
    return None


def photos_of(entry: dict) -> set[str]:
    """Every photograph filed against this fault, by leaf name, follow-ups included."""
    names = set()
    for step in [entry] + list(entry.get("updates") or []):
        for name in step.get("photos") or []:
            names.add(Path(name).name)
    return names


def _allow(ident: str) -> bool:
    """True if this link may write again today."""
    now = time.time()
    recent = [t for t in _posts.get(ident, []) if now - t < 86400]
    _posts[ident] = recent
    if len(recent) >= MAX_POSTS_A_DAY:
        return False
    recent.append(now)
    return True


# ── the page ───────────────────────────────────────────────────────────────────────────

def _esc(text) -> str:
    return gate._esc(text)


def _shell(title: str, inner: str) -> bytes:
    """A page of its own, not the console: this is read on a phone by somebody with no
    account, and the console's frame is a set of doors they cannot open."""
    return (f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{_esc(title)}</title>
<link rel="stylesheet" href="/vendor/bootstrap.min.css">
<link rel="stylesheet" href="/vendor/bootstrap-icons.css">
<style>
  body {{ font-size: 1.0625rem; }}
  main {{ max-width: 44rem; }}
  .ob-shot {{ width: 100%; height: auto; border-radius: .5rem; }}
  .form-control, .form-select, .btn {{ font-size: 1.0625rem; }}
</style>
</head><body class="bg-body-tertiary">
<main class="container py-4 px-3">
{inner}
</main>
</body></html>""").encode()


def gone_page() -> bytes:
    """What a dead link shows. It names no boat, no fault and no person — somebody holding
    an expired link is, as far as this page knows, a stranger."""
    return _shell("Not available", """
  <div class="py-5 text-center">
    <h1 class="h4 fw-semibold mb-3">This link is no longer valid</h1>
    <p class="text-body-secondary mb-0">It may have run out, or been taken back by
       whoever sent it. Ask them for a new one.</p>
  </div>""")


BADGE = {"fixed": "text-bg-success", "review": "text-bg-info", "open": "text-bg-warning"}


def _badge(status: str) -> str:
    word = str(status or "").strip().lower().split()[0] if str(status or "").strip() else ""
    if not word:
        return ""
    kind = BADGE.get(word, "text-bg-secondary")
    return f'<span class="badge rounded-pill {kind}">{_esc(word)}</span>'


def _prose(text: str) -> str:
    """A note as paragraphs. Escaped first and never anything else: this is what somebody
    typed into a phone, and it is displayed to somebody else on theirs.

    A blank line is a new thought and a single newline is where a thumb hit return, so the
    first makes a paragraph and the second keeps its break.
    """
    body = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not body:
        return '<p class="text-body-secondary mb-0">No note was written.</p>'
    out = []
    for chunk in body.split("\n\n"):
        if chunk.strip():
            out.append('<p class="mb-2">' + _esc(chunk.strip()).replace("\n", "<br>") + "</p>")
    return "".join(out)


def _shots(token: str, names, entry: dict) -> str:
    allowed = photos_of(entry)
    out = []
    for name in names or []:
        leaf = Path(name).name
        if leaf not in allowed:
            continue
        url = f"/s/{_esc(token)}/photo?name={_esc(urllib.parse.quote(leaf))}"
        out.append(f'<img class="ob-shot mb-3" src="{url}" alt="a photograph of the fault" '
                   f'loading="lazy">')
    return "".join(out)


def _card(title: str, inner: str, flush: bool = False) -> str:
    body = (f'<div class="list-group list-group-flush">{inner}</div>' if flush
            else f'<div class="card-body">{inner}</div>')
    return (f'<div class="card mb-3">'
            f'<div class="card-header fw-semibold">{_esc(title)}</div>{body}</div>')


def _history(token: str, entry: dict) -> str:
    """The follow-ups in the order they happened, each with whatever it changed."""
    out = []
    for step in sorted(entry.get("updates") or [], key=lambda u: str(u.get("when", ""))):
        head = " · ".join(_esc(x) for x in [step.get("when"), step.get("by")] if x)
        event = ""
        if step.get("assigned") == "-":
            event = '<div class="small text-body-secondary">handed back to nobody</div>'
        elif step.get("assigned"):
            event = ('<div class="small text-body-secondary">assigned to '
                     + _esc(step["assigned"]) + "</div>")
        out.append('<div class="list-group-item py-3">'
                   f'<div class="small text-body-secondary mb-2">{head} '
                   f'{_badge(step.get("status"))}</div>'
                   f'{_prose(step.get("body"))}{event}'
                   f'{_shots(token, step.get("photos"), entry)}</div>')
    return "".join(out)


def _form(token: str) -> str:
    answers = "".join(f'<option value="{word}">{_esc(label)}</option>'
                      for word, label in ANSWERS)
    return f"""
      <form method="post" action="/s/{_esc(token)}/update">
        <div class="mb-3">
          <label class="form-label" for="name">Your name</label>
          <input class="form-control form-control-lg" id="name" name="name" type="text"
                 autocomplete="name" maxlength="{MAX_NAME}" required>
        </div>
        <div class="mb-3">
          <label class="form-label" for="status">Where it stands</label>
          <select class="form-select form-select-lg" id="status"
                  name="status">{answers}</select>
        </div>
        <div class="mb-3">
          <label class="form-label" for="note">What you did or found</label>
          <textarea class="form-control" id="note" name="note" rows="4" required
                    placeholder="what you looked at, or what you replaced"></textarea>
        </div>
        <button class="btn btn-primary btn-lg w-100" type="submit">Send it back</button>
      </form>
      <p class="small text-body-secondary mt-3 mb-0">This is added to the boat's own file
         with your name on it. Nothing already written is changed, and this link reaches
         nothing else on the boat.</p>"""


def page(token: str, entry: dict, payload: dict, said: str = "", bad: str = "") -> bytes:
    """The whole thing one person sees: the fault, its photographs, and two answers back."""
    boat = snag_names().get(payload["boat"], payload["boat"])
    title = entry.get("title") or "a fault"

    alerts = ""
    if said:
        alerts += f'<div class="alert alert-success" role="status">{_esc(said)}</div>'
    if bad:
        alerts += f'<div class="alert alert-danger" role="alert">{_esc(bad)}</div>'

    meta = " · ".join(_esc(x) for x in [
        entry.get("when"), entry.get("where"),
        f"reported by {entry['by']}" if entry.get("by") else ""] if x)

    held = entry.get("assigned") or ""
    with_whom = (f'<p class="mb-3"><span class="text-body-secondary">With </span>'
                 f'{_esc(held)}</p>' if held else "")

    shots = _shots(token, entry.get("photos"), entry)
    ups = _history(token, entry)

    inner = (f'<p class="text-body-secondary small mb-1">{_esc(boat)}</p>'
             f'<h1 class="h4 fw-semibold mb-2">{_esc(title)}</h1>'
             f'<p class="mb-3">{_badge(entry.get("status"))}'
             f'<span class="text-body-secondary small ms-1">{meta}</span></p>'
             f"{alerts}{with_whom}"
             + _card("What was written", _prose(entry.get("body")))
             + (_card("Photographs", shots) if shots else "")
             + (_card("Since it was filed", ups, flush=True) if ups else "")
             + _card("Say what you found", _form(token)))
    return _shell(f"{title} — {boat}", inner)


def snag_names() -> dict[str, str]:
    """`{key: name}` for the boats this process can see. Empty when it can see none."""
    try:
        return {b["key"]: b["name"] for b in snag.boats()}
    except Exception:                                                   # noqa: BLE001
        return {}


# ── the two mounts ─────────────────────────────────────────────────────────────────────

def mount_boat(handler, user, rest: str):
    """`POST /b/<key>/snag/api/share` — mint a link. Everything else falls through.

    Falsy for every other path under `/b/`, which is nearly all of them: this mount is
    consulted before the gate's own router and must hand back everything it did not come
    for, or it silently becomes the router.
    """
    key, _, tail = rest.partition("/")
    if tail != "snag/api/share" or handler.command != "POST":
        return False

    # A 404 rather than a 403, for the reason `gate.not_found` gives: which boats exist,
    # and who may share one, is not a stranger's business. Crew are refused here — handing
    # somebody a way into the boat's file is an owner's decision.
    role = (user or {}).get("role") or ""
    if not user or role not in ("owner", "admin") or key not in gate.boats_for(user):
        handler.not_found(True)
        return True

    try:
        body = json.loads(handler.body() or b"{}")
        if not isinstance(body, dict):
            raise ValueError("expected an object")
    except (ValueError, json.JSONDecodeError):
        handler.send_json({"error": "that is not a share"}, status=400)
        return True

    when = clean(body.get("when"), 32)
    if not fault(key, when):
        handler.not_found(True)
        return True
    who = str(user.get("name") or user.get("email") or "")
    try:
        made = grant(gate.session_secret(), key, when, str(body.get("label", "")),
                     body.get("days", DEFAULT_DAYS), who)
    except (ValueError, OSError) as exc:
        handler.send_json({"error": str(exc)}, status=400)
        return True
    handler.send_json({"ok": True, "url": gate.public_url() + "/s/" + made["token"],
                       "id": made["id"], "until": made["until"], "label": made["label"]})
    return True


def mount_link(handler, user, rest: str):
    """`/s/<token>` and the two routes under it. No login anywhere in here."""
    token, _, tail = rest.partition("/")
    found = resolve(token) if token else None
    if not found:
        handler.send_page(gone_page(), status=404)
        return True
    payload, entry = found

    if tail == "photo" and handler.command in ("GET", "HEAD"):
        return _serve_photo(handler, payload, entry)
    if tail == "update" and handler.command == "POST":
        return _take_update(handler, payload, entry, token)
    if tail or handler.command not in ("GET", "HEAD"):
        handler.send_page(gone_page(), status=404)
        return True
    handler.send_page(page(token, entry, payload))
    return True


def _serve_photo(handler, payload: dict, entry: dict):
    """One photograph, relayed. The boat comes from the token and the name is checked twice.

    Once against the same allow-list the snag service checks it with, and once against the
    photographs actually filed on *this* fault — a link to one fault is not a way to read
    every picture on the boat.
    """
    params = urllib.parse.parse_qs(handler.path.partition("?")[2])
    leaf = Path((params.get("name") or [""])[0]).name
    if not snag.PHOTO_NAME.fullmatch(leaf) or leaf not in photos_of(entry):
        handler.not_found(True)
        return True
    query = urllib.parse.urlencode({"boat": payload["boat"], "name": leaf})
    handler.relay(f"{gate.snag_origin()}/photo?{query}", None)
    return True


def _take_update(handler, payload: dict, entry: dict, token: str):
    """The one write a share link can make: a follow-up on the fault it was minted for."""
    form = urllib.parse.parse_qs(handler.body().decode("utf-8", "replace"))
    name = clean((form.get("name") or [""])[0], MAX_NAME)
    status = clean((form.get("status") or [""])[0], 16).lower()
    note = (form.get("note") or [""])[0].strip()[:MAX_NOTE]

    bad = ""
    if not name:
        bad = "Please say who you are — this goes into the boat's file with a name on it."
    elif status not in [word for word, _ in ANSWERS]:
        bad = "Choose whether you have looked at it or fixed it."
    elif not note:
        bad = "Please say what you did or found."
    elif not _allow(payload["id"]):
        bad = "This link has sent a great many updates today. Try again tomorrow."
    if bad:
        handler.send_page(page(token, entry, payload, bad=bad), status=400)
        return True

    try:
        # The name is what they typed and the rest is what the link says, so the file
        # records both what somebody claimed and which link they claimed it through.
        snag.record(payload["boat"], note, "", [],
                    by=f"{name} (via share link {payload['id']})",
                    follow_up_to=payload["when"], status=status)
    except (ValueError, OSError) as exc:
        handler.send_page(page(token, entry, payload, bad=str(exc)), status=400)
        return True

    fresh = fault(payload["boat"], payload["when"]) or entry
    handler.send_page(page(token, fresh, payload,
                           said="Thank you — that is now in the boat's file."))
    return True


# ── installing it ──────────────────────────────────────────────────────────────────────

def install() -> None:
    """Hang both mounts on the gate. Called on import, and safe to call again."""
    for prefix, mount in (("/b/", mount_boat), ("/s/", mount_link)):
        if not any(existing is mount for _, existing in gate.MOUNTS):
            gate.MOUNTS.append((prefix, mount))


install()
