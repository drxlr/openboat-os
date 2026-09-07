#!/usr/bin/env python3
"""The share link: one fault, one person, no account, and a way to take it back.

    python3 tests/test_share.py

`openboat.share` hands somebody who has no login a link to exactly one fault. That makes it
the second thing in this project reachable without a password, so the checks here are about
what it refuses.

**A stateless token is still revocable, because the file is the record.** The grant is a
follow-up in the boat's own `SNAGS.md`; a token whose id is not listed there, or is listed
as taken back, opens nothing — however good its signature is.

**Only an owner may hand one out.** Crew get a 404 rather than a 403, for the same reason
the gate 404s a boat somebody may not see: which faults exist, and who was given a way to
one, is not their business to learn from a status code.

**The link reaches one fault and nothing else.** The boat is taken from the token and never
from the request, a photograph must be one filed against *this* entry, and the only write
it can make is a follow-up on the fault it was minted for.

No network beyond loopback. Same `check()` idiom as the rest of the suite — see
tests/test_gate.py, whose fake services and browser this file reuses.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_gate import Client, Env, FakeSnag, Running, SECRET, invite_and_set   # noqa: E402

PASS, FAIL = "  ok  ", "  FAIL"
results: list[tuple[bool, str]] = []


def check(ok: bool, what: str) -> None:
    results.append((bool(ok), what))
    print(f"{PASS if ok else FAIL}  {what}")


#: Invented, like every name in this suite. The keys are the folder names, which is what the
#: gate, the snag service and the console must all agree a boat is called.
BOATS = (("first-boat", "A Test Boat"), ("second-boat", "Another Test Boat"))


def a_boats_dir(tmp: Path) -> Path:
    home = tmp / "boats"
    for key, name in BOATS:
        (home / key).mkdir(parents=True)
        (home / key / "boat.toml").write_text(f'[vessel]\nname = "{name}"\n')
    return home


def a_gate(tmp: Path, boats: Path, snag_origin: str) -> Env:
    """A gate whose boat keys are the folder keys, and which can read those folders.

    The share module appends to the boat's own file in this process, so the gate is started
    with `OPENBOAT_BOATS` exactly as the snag service is. Without it every share route is an
    honest 404, which is its own check below.
    """
    return Env(OPENBOAT_USERS=str(tmp / "users.json"),
               OPENBOAT_SESSION_SECRET=SECRET,
               OPENBOAT_GATE_BOATS=",".join(f"{key}=http://127.0.0.1:1" for key, _ in BOATS),
               OPENBOAT_SNAG_ORIGIN=snag_origin,
               OPENBOAT_PUBLIC_URL="https://boat.example",
               OPENBOAT_GATE_SECRET="",
               OPENBOAT_BOATS=str(boats))


def a_fault(key: str, note: str = "The locker will not latch", photo: bool = False) -> str:
    """File one fault and hand back its timestamp, which is how everything names it."""
    from openboat import snag

    snag.record(key, note, "saloon", [b"\xff\xd8\xffnotreallyajpeg"] if photo else [],
                by="An Owner")
    return snag.read_snags(key)[0]["when"]


# --------------------------------------------------------------------------------------
def test_the_token_stands_or_falls_on_its_signature() -> None:
    from openboat import gate, share

    boat, when = "second-boat", "2026-09-07 11:22:33"
    token = share.mint(SECRET, boat, when, 30, "Jo at the yard")
    payload = share.verify(SECRET, token)
    check(payload is not None and payload["boat"] == boat and payload["when"] == when,
          "a token round-trips the boat and the fault it names")
    check(payload and payload["label"] == "Jo at the yard" and len(payload["id"]) == 8,
          f"with the label and an id of its own ({payload and payload.get('id')})")
    check(payload and abs(payload["exp"] - (time.time() + 30 * 86400)) < 5,
          "and the expiry it was asked for")

    blob, _, mac = token.partition(".")
    check(share.verify(SECRET, f"{blob}.{'0' * len(mac)}") is None,
          "a re-signed token is not a token")
    check(share.verify(SECRET, blob) is None, "nor is one with the signature taken off")
    # One byte of the payload changed: same length, same shape, a different MAC.
    edited = blob[:-4] + ("A" if blob[-4] != "A" else "B") + blob[-3:]
    check(share.verify(SECRET, f"{edited}.{mac}") is None,
          "and an edited payload does not fit the signature it arrived with")
    check(share.verify(SECRET + "x", token) is None,
          "a token signed with another secret is refused")

    expired = share._sign(SECRET, {"boat": boat, "when": when, "id": "abcd1234",
                                   "label": "", "exp": int(time.time()) - 10})
    check(share.verify(SECRET, expired) is None, "an expired token is over")

    check(share.days(1000) == share.MAX_DAYS and share.days(0) == 1
          and share.days("nonsense") == share.DEFAULT_DAYS,
          "the number of days is brought inside 1…90, whatever was asked for")

    with Env(OPENBOAT_SESSION_SECRET=SECRET):
        cookie = gate.sign("session", {"email": "owner@example.org"}, 30)
    check(share.verify(SECRET, cookie) is None,
          "a session cookie cannot be presented as a share link")


def test_the_file_is_the_record_of_who_was_given_a_way_in() -> None:
    from openboat import share, snag

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        with Env(OPENBOAT_BOATS=str(a_boats_dir(tmp)), OPENBOAT_SESSION_SECRET=SECRET):
            when = a_fault("second-boat")

            first = share.grant(SECRET, "second-boat", when, "Jo at the yard", 14, "An Owner")
            second = share.grant(SECRET, "second-boat", when, "A surveyor", 7, "An Owner")
            entry = share.fault("second-boat", when)
            check(len(entry["shares"]) == 2,
                  f"both links are listed on the fault ({len(entry['shares'])})")
            check([g["label"] for g in entry["shares"]] == ["Jo at the yard", "A surveyor"],
                  "in the order they were given, with the labels they were given under")
            check(not any(g["revoked"] for g in entry["shares"]),
                  "and neither is revoked to begin with")

            text = (tmp / "boats" / "second-boat" / "SNAGS.md").read_text()
            check(f"**Share:** {first['id']}" in text,
                  "the grant is a line in the boat's own file")
            check("Shared with Jo at the yard" in text and first["id"] in text,
                  "with a sentence a person can read beside it")
            check(len(entry["updates"]) == 2 and all(u["status"] == "" for u in entry["updates"]),
                  "it is a follow-up, and it changes nothing about the fault's status")
            check(entry["open"], "which is still open")

            check(share.resolve(first["token"]) is not None
                  and share.resolve(second["token"]) is not None,
                  "both links open the fault")

            snag.record("second-boat", "Took it back.", "", [], by="An Owner",
                        follow_up_to=when, unshare=first["id"])
            entry = share.fault("second-boat", when)
            gone = [g for g in entry["shares"] if g["id"] == first["id"]][0]
            still = [g for g in entry["shares"] if g["id"] == second["id"]][0]
            check(gone["revoked"] and not still["revoked"],
                  "revoking one names one link and leaves the other alone")
            check(share.resolve(first["token"]) is None,
                  "and the revoked token stops opening anything, signature and all")
            check(share.resolve(second["token"]) is not None, "while the other still does")

            # A token for a fault that is not in the file at all.
            nowhere = share.mint(SECRET, "second-boat", "2019-01-01 00:00:00", 30, "x")
            check(share.resolve(nowhere) is None,
                  "a token naming a fault this boat has never had opens nothing")
            # ...and one whose id was never written down, however well signed.
            unwritten = share.mint(SECRET, "second-boat", when, 30, "never recorded")
            check(share.resolve(unwritten) is None,
                  "nor does one whose id the file does not list")


def test_only_an_owner_may_hand_out_a_link() -> None:
    from openboat import gate, share

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        boats = a_boats_dir(tmp)
        with Running(FakeSnag) as snags, a_gate(tmp, boats, snags.origin), \
             Running(gate.Gate) as served:
            base = served.origin
            when = a_fault("second-boat")

            crew = invite_and_set(gate, base, "crew@example.org", "A Crew", ["second-boat"])
            owner = invite_and_set(gate, base, "owner@example.org", "An Owner",
                                   ["second-boat"], role="owner")
            other = invite_and_set(gate, base, "other@example.org", "Another Owner",
                                   ["first-boat"], role="owner")

            ask = json.dumps({"when": when, "label": "Jo at the yard", "days": 14}).encode()

            status, body = crew.json("/b/second-boat/snag/api/share", data=ask)
            check(status == 404, f"crew cannot hand out a link, and are not told why ({status})")

            status, body = other.json("/b/second-boat/snag/api/share", data=ask)
            check(status == 404,
                  f"nor can an owner of another boat reach this one ({status})")

            status, body = Client(base).json("/b/second-boat/snag/api/share", data=ask)
            check(status == 404, f"nor can somebody with no session at all ({status})")

            status, body = owner.json("/b/second-boat/snag/api/share", data=ask)
            check(status == 200 and (body or {}).get("url", "").startswith(
                      "https://boat.example/s/"),
                  f"an owner gets a link on the gate's public address ({status})")
            check(body and body.get("id") and body.get("until", "").startswith("20"),
                  f"with the id it is revoked by and the day it stops ({body})")

            text = (boats / "second-boat" / "SNAGS.md").read_text()
            check(f"**Share:** {body['id']}" in text and "Jo at the yard" in text,
                  "and the grant is in the boat's file, not only in the answer")
            check("**By:** An Owner" in text,
                  "with the name of whoever handed it out, from their session")

            status, _ = owner.json("/b/second-boat/snag/api/share",
                                   data=json.dumps({"when": "2019-01-01 00:00:00"}).encode())
            check(status == 404, f"a fault that is not filed cannot be shared ({status})")

            status, _ = owner.json("/b/second-boat/snag/api/share", data=b"not json")
            check(status == 400, f"a body that is not a share is refused ({status})")

            # Every other path under /b/ must fall straight through to the gate's own
            # router: a mount that answers more than it came for silently becomes the router.
            status, _, _ = owner.open("/b/second-boat/console/tasks.js", method="GET")
            check(status == 200, f"the console's own files still load ({status})")
            status, seen = owner.json("/b/second-boat/snag/api/share", method="GET")
            check(not (seen or {}).get("url"),
                  "and a GET on the share route mints nothing — it is not the mint route")


def test_the_link_opens_one_fault_and_nothing_else() -> None:
    from openboat import gate, share, snag

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        boats = a_boats_dir(tmp)
        FakeSnag.seen.clear()
        share._posts.clear()
        with Running(FakeSnag) as snags, a_gate(tmp, boats, snags.origin), \
             Running(gate.Gate) as served:
            base = served.origin
            when = a_fault("second-boat", "The locker will not latch", photo=True)
            snag.record("second-boat", "Handing this to the yard.", "", [], by="An Owner",
                        follow_up_to=when, assigned="Jo at the yard")
            made = share.grant(SECRET, "second-boat", when, "Jo at the yard", 30, "An Owner")
            token = made["token"]

            visitor = Client(base)                       # no cookie, no account, no invite
            status, headers, body = visitor.open(f"/s/{token}", method="GET")
            page = body.decode("utf-8", "replace")
            check(status == 200, f"the page opens with no login at all ({status})")
            check("The locker will not latch" in page and "Another Test Boat" in page,
                  "and shows the fault and the boat it is on")
            check("Jo at the yard" in page, "and who it is with")
            check('name="status"' in page and 'action="/s/' in page,
                  "with a form to answer on")
            check(headers.get("Content-Security-Policy", "").startswith("default-src 'self'")
                  and headers.get("X-Content-Type-Options") == "nosniff",
                  "the gate's security headers are on it like everything else")

            # The photograph: relayed, with the boat taken from the token.
            leaf = Path(share.fault("second-boat", when)["photos"][0]).name
            status, _, _ = visitor.open(
                f"/s/{token}/photo?name={urllib.parse.quote(leaf)}&boat=first-boat",
                method="GET")
            asked = FakeSnag.seen[-1]
            check(status == 200 and asked["route"] == "/photo",
                  f"a photograph is relayed to the snag service ({status})")
            check(asked["params"]["boat"] == ["second-boat"],
                  f"with the boat forced from the token, not the query "
                  f"({asked['params'].get('boat')})")

            status, _, _ = visitor.open(f"/s/{token}/photo?name=nothere.jpg", method="GET")
            check(status == 404,
                  f"a photograph not filed against this fault is not served ({status})")
            status, _, _ = visitor.open(
                f"/s/{token}/photo?name={urllib.parse.quote('../../boat.toml')}", method="GET")
            check(status == 404, f"and a traversal gets nothing ({status})")

            # The one write it can make.
            status, _, body = visitor.open(f"/s/{token}/update", data={
                "name": "Jo", "status": "review", "note": "Looked at it, needs a new catch."})
            check(status == 200 and b"in the boat" in body,
                  f"an answer is taken and confirmed on the page ({status})")
            entry = share.fault("second-boat", when)
            latest = entry["updates"][-1]
            check(latest["by"] == f"Jo (via share link {made['id']})",
                  f"and lands in the file saying which link it came through ({latest['by']!r})")
            check(entry["status"] == "review" and entry["open"],
                  f"the fault moves to review and stays open ({entry['status']!r})")
            check("Looked at it" in latest["body"], "with what they wrote")

            for missing in ({"name": "", "status": "review", "note": "x"},
                            {"name": "Jo", "status": "open", "note": "x"},
                            {"name": "Jo", "status": "review", "note": ""}):
                status, _, _ = visitor.open(f"/s/{token}/update", data=missing)
                check(status == 400, f"an incomplete answer is refused ({missing} → {status})")
            check(len(share.fault("second-boat", when)["updates"]) == 3,
                  "and none of those refusals wrote anything")

            # Nothing else is reachable through it.
            for path in ("/s/{t}/api/snags", "/s/{t}/photo/../../boat.toml", "/s/{t}/anything"):
                status, _, _ = visitor.open(path.format(t=token), method="GET")
                check(status == 404, f"{path} is not a route on a share link ({status})")

            expired = share._sign(SECRET, {"boat": "second-boat", "when": when,
                                           "id": made["id"], "label": "",
                                           "exp": int(time.time()) - 10})
            status, _, body = visitor.open(f"/s/{expired}", method="GET")
            check(status == 404 and b"no longer valid" in body,
                  f"an expired link says so and no more ({status})")
            check(b"Another Test Boat" not in body and b"locker" not in body,
                  "and names no boat and no fault to whoever is holding it")

            snag.record("second-boat", "Took it back.", "", [], by="An Owner",
                        follow_up_to=when, unshare=made["id"])
            status, _, body = visitor.open(f"/s/{token}", method="GET")
            check(status == 404 and b"no longer valid" in body,
                  f"and a revoked link stops opening, on the next click ({status})")
            status, _, _ = visitor.open(f"/s/{token}/update", data={
                "name": "Jo", "status": "fixed", "note": "sneaking one in"})
            check(status == 404, "including its one write")
            # The fault carries four follow-ups: the assignment, the grant, the one
            # answer that got through, and the revocation. Nothing refused wrote a line.
            check(len(share.fault("second-boat", when)["updates"]) == 4,
                  "which wrote nothing — the follow-ups are the ones a person made")


def test_a_link_cannot_be_used_to_fill_the_file() -> None:
    from openboat import gate, share

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        boats = a_boats_dir(tmp)
        share._posts.clear()
        with Running(FakeSnag) as snags, a_gate(tmp, boats, snags.origin), \
             Running(gate.Gate) as served:
            when = a_fault("second-boat")
            made = share.grant(SECRET, "second-boat", when, "Jo at the yard", 30, "An Owner")
            visitor = Client(served.origin)

            seen = []
            for n in range(share.MAX_POSTS_A_DAY + 2):
                status, _, _ = visitor.open(f"/s/{made['token']}/update", data={
                    "name": "Jo", "status": "review", "note": f"note {n}"})
                seen.append(status)
            check(seen[0] == 200 and seen.count(200) == share.MAX_POSTS_A_DAY,
                  f"thirty answers go through ({seen.count(200)})")
            check(seen[-1] == 400 and seen[-2] == 400,
                  f"and the ones after that do not ({seen[-2:]})")
            check(len(share.fault("second-boat", when)["updates"])
                  == share.MAX_POSTS_A_DAY + 1,
                  "so the file has those thirty on it, beside the grant, and not thirty-two")


def test_without_the_boats_configured_every_share_route_is_a_404() -> None:
    """The gate reads and appends to the boat's own files for this. Started without them —
    which is how the gate ran before this existed — it must refuse, not half-work."""
    from openboat import gate, share

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        boats = a_boats_dir(tmp)
        with Running(FakeSnag) as snags, a_gate(tmp, boats, snags.origin), \
             Running(gate.Gate) as served:
            base = served.origin
            when = a_fault("second-boat")
            made = share.grant(SECRET, "second-boat", when, "Jo", 30, "An Owner")
            owner = invite_and_set(gate, base, "owner@example.org", "An Owner",
                                   ["second-boat"], role="owner")

            with Env(OPENBOAT_BOATS=str(tmp / "nothing-here")):
                status, _, body = Client(base).open(f"/s/{made['token']}", method="GET")
                check(status == 404 and b"no longer valid" in body,
                      f"the page refuses rather than half-drawing ({status})")
                status, _ = owner.json(
                    "/b/second-boat/snag/api/share",
                    data=json.dumps({"when": when, "label": "x"}).encode())
                check(status == 404, f"and no new link can be minted ({status})")


def test_the_share_module_holds_no_boat_facts() -> None:
    """It ships in a public repository, like everything else here."""
    from openboat.profile import load

    demo = load(ROOT / "profiles" / "demo-boat.toml")
    text = (ROOT / "openboat" / "share.py").read_text(encoding="utf-8")
    check(demo.vessel.name not in text, "share.py names no vessel, not even the demo boat's")
    check("wa.me" not in text,
          "and holds no address of its own — the WhatsApp hand-off is the console's")


if __name__ == "__main__":
    print(__doc__.splitlines()[0])
    print("-" * 78)
    for case in (test_the_token_stands_or_falls_on_its_signature,
                 test_the_file_is_the_record_of_who_was_given_a_way_in,
                 test_only_an_owner_may_hand_out_a_link,
                 test_the_link_opens_one_fault_and_nothing_else,
                 test_a_link_cannot_be_used_to_fill_the_file,
                 test_without_the_boats_configured_every_share_route_is_a_404,
                 test_the_share_module_holds_no_boat_facts):
        case()
    print("-" * 78)
    failed = [what for ok, what in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} checks pass"
          + (f" — FAILED: {failed}" if failed else ""))
    sys.exit(1 if failed else 0)
