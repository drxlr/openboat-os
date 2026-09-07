#!/usr/bin/env python3
"""The snag list: appended, never rewritten, and the file is the truth.

    python3 tests/test_snag.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS, FAIL = "  ok  ", "  FAIL"
results: list[tuple[bool, str]] = []


def check(ok: bool, what: str) -> None:
    results.append((bool(ok), what))
    print(f"{PASS if ok else FAIL}  {what}")


def _boats_dir(tmp: Path) -> Path:
    for key, name in (("first-boat", "Demo Boat"), ("second-boat", "Other Boat")):
        d = tmp / key
        d.mkdir(parents=True)
        (d / "boat.toml").write_text(f'[vessel]\nname = "{name}"\n')
    return tmp


def test_snags_round_trip_and_stay_separate() -> None:
    from openboat import snag

    with tempfile.TemporaryDirectory() as raw:
        os.environ["OPENBOAT_BOATS"] = str(_boats_dir(Path(raw)))
        try:
            check([b["key"] for b in snag.boats()] == ["first-boat", "second-boat"],
                  "both boats are discovered from the boats directory")

            snag.record("second-boat", "Locker will not latch", "saloon", [b"\xff\xd8\xffnotreallyajpeg"])
            snag.record("second-boat", "Second fault", "", [])
            snag.record("first-boat", "Different boat entirely", "", [])

            cm = snag.read_snags("second-boat")
            check(len(cm) == 2, f"two snags on the second boat (got {len(cm)})")
            check(cm[0]["title"] == "Second fault", "newest is first")
            check(all(r["open"] for r in cm), "a new snag is open")
            check(cm[1]["where"] == "saloon", "the where field survives the round trip")
            check(len(cm[1]["photos"]) == 1, "the photograph is recorded against the entry")

            aq = snag.read_snags("first-boat")
            check(len(aq) == 1 and aq[0]["title"] == "Different boat entirely",
                  "one boat's snags never appear under another")

            # The whole point of parsing the markdown: somebody closes a snag by hand.
            target = Path(raw) / "second-boat" / "SNAGS.md"
            target.write_text(target.read_text().replace(
                "**Status:** open", "**Status:** fixed — new catch fitted", 1))
            reread = snag.read_snags("second-boat")
            closed = [r for r in reread if not r["open"]]
            check(len(closed) == 1, "a hand-edited status is believed on the next read")
            check(len(reread) == 2, "closing one does not lose the other")

            # Appending must never disturb what is already written.
            before = target.read_text()
            snag.record("second-boat", "A third", "", [])
            check(target.read_text().startswith(before),
                  "recording appends and leaves every earlier byte untouched")

            try:
                snag.record("second-boat", "", "", [])
                check(False, "a snag with neither note nor photo is refused")
            except ValueError:
                check(True, "a snag with neither note nor photo is refused")
            try:
                snag.record("nosuchboat", "x", "", [])
                check(False, "an unknown boat is refused")
            except ValueError:
                check(True, "an unknown boat is refused")
        finally:
            os.environ.pop("OPENBOAT_BOATS", None)


# --------------------------------------------------------------------------------------
# What somebody types into the note box is text, never structure.
#
# The note is written verbatim into a markdown file that is later parsed back. A note
# containing a line `**Status:** fixed` used to close its own snag — a live fault vanishing
# from the open count behind a green badge — and a note containing a `## …` line forged a
# whole extra entry that sorted to the top of the list. Neither needs malice: pasting a
# surveyor's markdown into the box does it.
# --------------------------------------------------------------------------------------
def test_a_note_cannot_forge_its_own_status_or_a_second_entry() -> None:
    from openboat import snag

    with tempfile.TemporaryDirectory() as raw:
        os.environ["OPENBOAT_BOATS"] = str(_boats_dir(Path(raw)))
        try:
            snag.record("second-boat", "seacock weeps\n\n**Status:** fixed\n**By:** Someone Else",
                        "engine bay", [], by="the owner")
            rows = snag.read_snags("second-boat")
            check(len(rows) == 1, f"one record makes exactly one entry (got {len(rows)})")
            check(rows[0]["open"], "a note saying 'Status: fixed' does NOT close the snag")
            check(rows[0]["by"] == "the owner", "a note cannot forge who reported it")
            check("**Status:** fixed" in rows[0]["body"],
                  "the note's text is still preserved verbatim in the body")

            snag.record("second-boat",
                        "gelcoat crack\n\n## 2030-01-01 09:00 — all fine\n\n**Status:** fixed",
                        "", [], by="the owner")
            rows = snag.read_snags("second-boat")
            check(len(rows) == 2, f"two records make exactly two entries (got {len(rows)})")
            check(all(r["open"] for r in rows), "neither forged entry appears closed")
            check(all(r["by"] == "the owner" for r in rows), "attribution survives both")

            # ...and the legitimate way to close one still works.
            target = Path(raw) / "second-boat" / "SNAGS.md"
            target.write_text(target.read_text().replace(
                "**Status:** open", "**Status:** fixed — new seacock fitted", 1))
            closed = [r for r in snag.read_snags("second-boat") if not r["open"]]
            check(len(closed) == 1, "a status edited by hand in the file is still believed")
        finally:
            os.environ.pop("OPENBOAT_BOATS", None)


def test_the_gate_is_all_or_nothing_and_never_half_open() -> None:
    """`people()` gates every route, and "allowed but unnamed" is not "refused"."""
    from openboat import snag

    before = os.environ.get("OPENBOAT_SNAG_PEOPLE")
    try:
        os.environ.pop("OPENBOAT_SNAG_PEOPLE", None)
        check(snag.people() == {}, "no configuration means no gate")

        os.environ["OPENBOAT_SNAG_PEOPLE"] = "skipper:supersecrettoken1,crew:supersecrettoken2"
        allowed = snag.people()
        check(allowed == {"supersecrettoken1": "skipper", "supersecrettoken2": "crew"},
              "configured people are keyed by their token")

        # A short token is not a token. Silently accepting one would be a gate that is not.
        os.environ["OPENBOAT_SNAG_PEOPLE"] = "skipper:short"
        check(snag.people() == {}, "a token under 12 characters is rejected, not accepted")
    finally:
        os.environ.pop("OPENBOAT_SNAG_PEOPLE", None)
        if before is not None:
            os.environ["OPENBOAT_SNAG_PEOPLE"] = before


# --------------------------------------------------------------------------------------
# One fault reads as one item, however many people wrote about it.
#
# An owner filed a symptom and then the cause of the same broken ladder, and the phone
# showed two open items for one fault — so "5 open" overstated the boat. Appending stays
# the rule (rewriting the first entry would lose *when* the symptom was seen versus when
# the cause was found); what was missing was a way to say these are the same fault.
# --------------------------------------------------------------------------------------
def test_a_follow_up_folds_into_the_fault_it_continues() -> None:
    from openboat import snag

    with tempfile.TemporaryDirectory() as raw:
        os.environ["OPENBOAT_BOATS"] = str(_boats_dir(Path(raw)))
        try:
            first = snag.record("second-boat", "Ladder will not attach", "transom", [],
                                by="the owner")
            rows = snag.read_snags("second-boat")
            parent_when = rows[0]["when"]

            snag.record("second-boat", "Cause found: the screw thread is stripped", "transom",
                        [], by="the owner", follow_up_to=parent_when)
            snag.record("second-boat", "An unrelated fault", "galley", [], by="the owner")

            rows = snag.read_snags("second-boat")
            check(len(rows) == 2, f"two faults, not three items (got {len(rows)})")
            ladder = [r for r in rows if "Ladder" in r["title"]][0]
            check(len(ladder["updates"]) == 1, "the follow-up is attached to its parent")
            check("stripped" in ladder["updates"][0]["body"], "the update keeps its text")
            check(all("Cause found" not in r["title"] for r in rows),
                  "the follow-up does not appear as an item of its own")
            check(sum(1 for r in rows if r["open"]) == 2,
                  "the open count counts faults, not entries")

            # A plain follow-up says nothing about status, so it carries no Status line —
            # only the two faults do. Silence is "no change"; a status is a deliberate word.
            target = Path(raw) / "second-boat" / "SNAGS.md"
            text = target.read_text()
            check(text.count("**Status:** open") == 2,
                  "the faults carry a status line and the plain follow-up carries none")
            check(ladder["open"], "a fault with an update on it is still open")

            # Entries filed inside the same second share a timestamp, and that must not
            # break the link. A follow-up attaches to the nearest *earlier* entry with that
            # timestamp, so the ambiguity resolves without inventing a more precise time
            # than really happened — and without an entry ever adopting itself.
            snag.record("second-boat", "Same second one", "", [], by="x")
            same = [r for r in snag.read_snags("second-boat") if "Same second one" in r["title"]]
            check(len(same) == 1, "the first of two same-second entries is found")
            snag.record("second-boat", "Same second two", "", [], by="x",
                        follow_up_to=same[0]["when"])
            rows = snag.read_snags("second-boat")
            host = [r for r in rows if "Same second one" in r["title"]]
            check(len(host) == 1 and len(host[0]["updates"]) == 1,
                  "a colliding timestamp still attaches the update to the right entry")
            check(all("Same second two" not in r["title"] for r in rows),
                  "and the update does not also stand on its own")
        finally:
            os.environ.pop("OPENBOAT_BOATS", None)





def test_a_follow_up_can_change_the_status_and_silence_changes_nothing() -> None:
    """`review` and `fixed` arrive as follow-ups from the console; a plain follow-up leaves
    the status alone; a closed fault is not reopened by an old follow-up's default line."""
    from openboat import snag

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        os.environ["OPENBOAT_BOATS"] = str(_boats_dir(tmp))
        if True:
            snag.record("second-boat", "Hatch seal weeps", "foredeck", [], by="the owner")
            when = snag.read_snags("second-boat")[0]["when"]

            snag.record("second-boat", "Looked again, still weeping", "", [], by="the owner",
                        follow_up_to=when)
            item = snag.read_snags("second-boat")[0]
            check(item["status"] == "open" and item["open"],
                  f"a follow-up without a status changes nothing (got {item['status']!r})")

            snag.record("second-boat", "Idea: re-bed the frame with butyl", "", [],
                        by="the owner", follow_up_to=when, status="review")
            item = snag.read_snags("second-boat")[0]
            check(item["status"] == "review" and item["open"],
                  f"a follow-up marked review moves the fault to review, still open (got {item['status']!r})")
            check(len(item["updates"]) == 2 and item["updates"][1]["status"] == "review",
                  "and the follow-up itself remembers what it said")

            try:
                snag.record("second-boat", "", "", [b"\xff\xd8\xffx"], by="x",
                            follow_up_to=when, status="fixed")
                check(False, "closing without a note is refused")
            except ValueError:
                check(True, "closing without a note is refused")
            try:
                snag.record("second-boat", "x", "", [], by="x", follow_up_to=when, status="parked")
                check(False, "an unknown status is refused")
            except ValueError:
                check(True, "an unknown status is refused")
            try:
                snag.record("second-boat", "x", "", [], by="x", status="review")
                check(False, "a status on a new snag is refused")
            except ValueError:
                check(True, "a status on a new snag is refused")

            snag.record("second-boat", "Re-bedded the frame, dry since", "", [],
                        by="the owner", follow_up_to=when, status="fixed")
            item = snag.read_snags("second-boat")[0]
            check(item["status"] == "fixed" and not item["open"],
                  f"a follow-up marked fixed closes it (got {item['status']!r})")

            # The legacy shape: an old follow-up that the recorder stamped `open` under a
            # fault somebody has since closed by hand must not reopen it.
            target = tmp / "second-boat" / "SNAGS.md"
            text = target.read_text()
            target.write_text(text + "\n## 2030-01-01 00:00:00 — Old note\n\n**Status:** open\n"
                              f"**Follow-up to:** {when}\n\n> filed long ago\n")
            item = snag.read_snags("second-boat")[0]
            check(item["status"] == "fixed" and not item["open"],
                  "an old follow-up's default `open` does not reopen a closed fault")

# --------------------------------------------------------------------------------------
# Who a fault is on, and who was given a way in to it.
#
# Both are the same shape as the status: a line in a follow-up, appended, and the newest one
# anybody wrote is the answer. Nothing is edited, so the history of who had it and who was
# let in stays readable — which is the whole reason the grant is in the file at all.
# --------------------------------------------------------------------------------------
def test_a_fault_is_handed_on_by_the_newest_name_anybody_wrote() -> None:
    from openboat import snag

    with tempfile.TemporaryDirectory() as raw:
        os.environ["OPENBOAT_BOATS"] = str(_boats_dir(Path(raw)))
        try:
            snag.record("second-boat", "Ladder will not attach", "transom", [], by="the owner")
            when = snag.read_snags("second-boat")[0]["when"]
            check(snag.read_snags("second-boat")[0]["assigned"] == "",
                  "a fault nobody has taken is on nobody")

            snag.record("second-boat", "Looked again, still loose", "", [], by="the owner",
                        follow_up_to=when)
            check(snag.read_snags("second-boat")[0]["assigned"] == "",
                  "a follow-up that says nothing about it changes nothing")

            snag.record("second-boat", "", "", [], by="the owner", follow_up_to=when,
                        assigned="Jo at the yard")
            item = snag.read_snags("second-boat")[0]
            check(item["assigned"] == "Jo at the yard",
                  f"a follow-up naming somebody puts it on them ({item['assigned']!r})")
            check(len(item["updates"]) == 2,
                  "and a follow-up with nothing but a name on it is still an entry")

            snag.record("second-boat", "", "", [], by="the owner", follow_up_to=when,
                        assigned="Someone Else")
            check(snag.read_snags("second-boat")[0]["assigned"] == "Someone Else",
                  "the newest name wins")

            snag.record("second-boat", "", "", [], by="the owner", follow_up_to=when,
                        assigned="-")
            item = snag.read_snags("second-boat")[0]
            check(item["assigned"] == "", "and a dash hands it back to nobody")
            check(item["updates"][-1]["assigned"] == "-",
                  "while the follow-up itself remembers that that is what it did")

            # An empty line means the same as a dash, because a field written blank is a
            # field somebody cleared. Absence is the thing that means "no change".
            snag.record("second-boat", "", "", [], by="x", follow_up_to=when, assigned="Jo")
            target = Path(raw) / "second-boat" / "SNAGS.md"
            target.write_text(target.read_text() +
                              f"\n## 2030-01-01 00:00:00 — cleared\n\n"
                              f"**Follow-up to:** {when}\n**Assigned:**\n\n> by hand\n")
            check(snag.read_snags("second-boat")[0]["assigned"] == "",
                  "an Assigned line written empty by hand clears it too")

            try:
                snag.record("second-boat", "", "", [], by="x", follow_up_to=when)
                check(False, "a follow-up that says and changes nothing is refused")
            except ValueError:
                check(True, "a follow-up that says and changes nothing is refused")
        finally:
            os.environ.pop("OPENBOAT_BOATS", None)


def test_a_share_is_written_into_the_file_and_taken_back_there() -> None:
    from openboat import snag

    with tempfile.TemporaryDirectory() as raw:
        os.environ["OPENBOAT_BOATS"] = str(_boats_dir(Path(raw)))
        try:
            snag.record("second-boat", "Seacock weeps", "engine bay", [], by="the owner")
            when = snag.read_snags("second-boat")[0]["when"]
            check(snag.read_snags("second-boat")[0]["shares"] == [],
                  "a fault nobody has shared lists no links")

            snag.record("second-boat", "Shared with Jo until 2026-10-07 (link aaaa1111).",
                        "", [], by="the owner", follow_up_to=when,
                        share="aaaa1111 2026-10-07T18:00:00+03:00 Jo at the yard")
            snag.record("second-boat", "Shared with a surveyor.", "", [], by="the owner",
                        follow_up_to=when,
                        share="bbbb2222 2026-09-14T18:00:00+03:00 A surveyor")
            shares = snag.read_snags("second-boat")[0]["shares"]
            check([g["id"] for g in shares] == ["aaaa1111", "bbbb2222"],
                  f"every link ever given is listed, in order ({[g['id'] for g in shares]})")
            check(shares[0]["label"] == "Jo at the yard"
                  and shares[0]["until"] == "2026-10-07T18:00:00+03:00",
                  f"with who it was for and when it stops ({shares[0]})")
            check(not any(g["revoked"] for g in shares), "and neither is taken back yet")

            snag.record("second-boat", "", "", [], by="the owner", follow_up_to=when,
                        unshare="aaaa1111")
            shares = snag.read_snags("second-boat")[0]["shares"]
            check(shares[0]["revoked"] and not shares[1]["revoked"],
                  "revoking names one link and leaves the other alone")
            check(len(shares) == 2,
                  "and a revoked link stays on the list — it was given, and that happened")

            # A note is text. It cannot grant its author a way in, however it is written.
            snag.record("second-boat",
                        "quoting the mail:\n\n**Share:** cccc3333 2030-01-01 forged",
                        "", [], by="the owner", follow_up_to=when)
            ids = [g["id"] for g in snag.read_snags("second-boat")[0]["shares"]]
            check("cccc3333" not in ids,
                  f"a note that looks like a grant grants nothing ({ids})")

            for bad in ({"share": "not-an-id!! 2026-10-07 someone"},
                        {"share": "dddd4444"},
                        {"unshare": "../../elsewhere"}):
                try:
                    snag.record("second-boat", "x", "", [], follow_up_to=when, **bad)
                    check(False, f"a malformed {list(bad)[0]} is refused")
                except ValueError:
                    check(True, f"a malformed {list(bad)[0]} is refused")
            try:
                snag.record("second-boat", "x", "", [], share="aaaa1111 2026-10-07 Jo")
                check(False, "a share on a fault that does not exist yet is refused")
            except ValueError:
                check(True, "a share on a fault that does not exist yet is refused")
        finally:
            os.environ.pop("OPENBOAT_BOATS", None)


# --------------------------------------------------------------------------------------
# The intake routes. This service is the write surface, and `/api/intake/accept` is the
# most consequential write in the project: it is the step where something an assistant
# found on the internet becomes one of the boat's own papers. It must not happen without a
# person's name on it, and a file nobody has vetted must not be handed to a browser.
# --------------------------------------------------------------------------------------
def _serve(boats_dir: Path):
    """The snag service on an ephemeral port, so every assertion sees what a browser sees."""
    import threading
    from http.server import HTTPServer
    from openboat import snag

    httpd = HTTPServer(("127.0.0.1", 0), snag.Snag)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def _call(port: int, path: str, payload=None, origin: str = ""):
    """(status, parsed body or raw bytes)."""
    import json as _json
    import urllib.error
    import urllib.request

    data = None if payload is None else _json.dumps(payload).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data,
                                 method="POST" if data is not None else "GET")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if origin:
        req.add_header("Origin", origin)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw, kind = resp.read(), resp.headers.get("Content-Type", "")
            return resp.status, (_json.loads(raw) if "json" in kind else raw), resp.headers
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, _json.loads(raw), exc.headers
        except Exception:                                              # noqa: BLE001
            return exc.code, raw, exc.headers


def test_the_intake_routes_need_a_name_and_never_serve_a_flagged_file() -> None:
    from openboat import intake, snag

    with tempfile.TemporaryDirectory() as raw:
        tmp = _boats_dir(Path(raw))
        os.environ["OPENBOAT_BOATS"] = str(tmp)
        httpd, thread = _serve(tmp)
        port = httpd.server_port
        try:
            clean = intake.add_link("https://manuals.example/pump", title="A bulletin",
                                    reason="matches the part", by="chatgpt",
                                    boat="second-boat")
            # A PDF with active content in it, put there directly: the fetch path has its
            # own tests, and what is under test here is what the routes do with one.
            base = tmp / "second-boat" / "intake"
            base.mkdir(parents=True, exist_ok=True)
            blob = b"%PDF-1.4\n/OpenAction 1 0 R\n%%EOF\n"
            import hashlib
            ident = hashlib.sha256(blob).hexdigest()[:16]
            (base / f"{ident}.pdf").write_bytes(blob)
            intake._write_sidecar(base / f"{ident}.toml", {
                "url": "https://manuals.example/x.pdf", "title": "Flagged", "reason": "",
                "by": "chatgpt", "fetched": "2026-09-07T10:00:00+03:00",
                "sha256": hashlib.sha256(blob).hexdigest(), "bytes": len(blob),
                "content_type": "application/pdf", "flags": ["OpenAction"],
                "status": "inbox"})

            status, body, _ = _call(port, "/api/intake?boat=second-boat")
            check(status == 200 and body["boat"] == "second-boat",
                  f"GET /api/intake answers for the boat asked for ({status})")
            ids = [i["id"] for i in body["items"]]
            check(clean["id"] in ids and ident in ids,
                  f"both the link and the file are listed ({ids})")
            flagged = next(i for i in body["items"] if i["id"] == ident)
            check(flagged["flags"] == ["OpenAction"] and flagged["by"] == "chatgpt",
                  "with its flags and who submitted it")

            # The boat parameter is honoured: the other boat's inbox is its own.
            status, other, _ = _call(port, "/api/intake?boat=first-boat")
            check(status == 200 and other["items"] == [],
                  f"another boat's inbox is empty and separate ({other})")

            status, body, headers = _call(
                port, f"/api/intake/file?boat=second-boat&id={ident}")
            check(status == 404, f"a flagged file is not served at all ({status})")

            status, body, _ = _call(port, "/api/intake/accept?who=",
                                    {"boat": "second-boat", "id": clean["id"]})
            check(status == 400 and "who you are" in body.get("error", ""),
                  f"accepting with no name is refused ({status} {body})")
            status, body, _ = _call(port, "/api/intake/reject?who=Alex%20Rivers",
                                    {"boat": "second-boat", "id": clean["id"]})
            check(status == 400 and "why" in body.get("error", "").lower(),
                  f"rejecting with no reason is refused ({status} {body})")
            check(intake.item("second-boat", clean["id"])["status"] == "inbox",
                  "and neither refusal decided anything")

            status, body, _ = _call(port, "/api/intake/accept?who=Alex%20Rivers",
                                    {"boat": "second-boat", "id": clean["id"]})
            check(status == 200 and body.get("ok") and body["by"] == "Alex Rivers",
                  f"accepting with a name works and records it ({status} {body})")
            check((tmp / "second-boat" / "documents" / "LINKS.md").is_file(),
                  "and the link lands in the boat's documents")

            status, body, _ = _call(port, "/api/intake/reject?who=Alex%20Rivers",
                                    {"boat": "second-boat", "id": ident,
                                     "why": "active content, not worth the risk"})
            check(status == 200 and body.get("ok"), f"rejecting works ({status} {body})")
            check(not (base / f"{ident}.pdf").exists(), "the bytes are deleted")
            check((base / f"{ident}.toml").is_file(), "and the decision is kept")

            # Every intake POST route preflights the way /api/snag does, and nothing else.
            import urllib.request
            for route, want in (("/api/snag", 204), ("/api/intake/accept", 204),
                                ("/api/intake/reject", 204), ("/api/intake", 404),
                                ("/api/anything", 404)):
                req = urllib.request.Request(f"http://127.0.0.1:{port}{route}",
                                             method="OPTIONS")
                req.add_header("Origin", "http://127.0.0.1:8747")
                try:
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        got = resp.status
                except Exception as exc:                               # noqa: BLE001
                    got = getattr(exc, "code", 0)
                check(got == want, f"OPTIONS {route} answers {want} (got {got})")
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()
            os.environ.pop("OPENBOAT_BOATS", None)


def test_an_unflagged_waiting_file_is_served_as_an_attachment() -> None:
    """The download exists so a person can read what they are about to accept. It is an
    attachment with nosniff on it, and it stops the moment somebody decides."""
    import hashlib
    from openboat import intake

    with tempfile.TemporaryDirectory() as raw:
        tmp = _boats_dir(Path(raw))
        os.environ["OPENBOAT_BOATS"] = str(tmp)
        httpd, thread = _serve(tmp)
        port = httpd.server_port
        try:
            base = tmp / "second-boat" / "intake"
            base.mkdir(parents=True, exist_ok=True)
            blob = b"%PDF-1.4\n1 0 obj\n%%EOF\n"
            ident = hashlib.sha256(blob).hexdigest()[:16]
            (base / f"{ident}.pdf").write_bytes(blob)
            intake._write_sidecar(base / f"{ident}.toml", {
                "url": "https://manuals.example/ok.pdf", "title": "Readable", "reason": "",
                "by": "claude", "fetched": "2026-09-07T10:00:00+03:00",
                "sha256": hashlib.sha256(blob).hexdigest(), "bytes": len(blob),
                "content_type": "application/pdf", "flags": [], "status": "inbox"})

            status, body, headers = _call(
                port, f"/api/intake/file?boat=second-boat&id={ident}")
            check(status == 200 and body == blob, f"the PDF comes back whole ({status})")
            check("attachment" in (headers.get("Content-Disposition") or ""),
                  f"as an attachment ({headers.get('Content-Disposition')!r})")
            check(headers.get("X-Content-Type-Options") == "nosniff",
                  "with nosniff on it")
            check(ident in (headers.get("Content-Disposition") or "")
                  and "Readable" not in (headers.get("Content-Disposition") or ""),
                  "and the filename is the id, never a title from the network")

            status, body, _ = _call(port, "/api/intake/file?boat=second-boat&id=../../boat")
            check(status == 404, f"an id that is not an id gets nothing ({status})")

            _call(port, "/api/intake/reject?who=Alex%20Rivers",
                  {"boat": "second-boat", "id": ident, "why": "not this engine"})
            status, body, _ = _call(port, f"/api/intake/file?boat=second-boat&id={ident}")
            check(status == 404, f"and a decided item is no longer served ({status})")
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()
            os.environ.pop("OPENBOAT_BOATS", None)



def test_the_write_route_takes_a_link_away_and_never_grants_one() -> None:
    """`POST /api/snag` carries a name and a revocation, and deliberately not a grant.

    Taking a link back is something anybody who can reach this route may do — the worst a
    mistake does is shut a door. Handing one out is minting a credential, and it happens in
    `openboat.share`, behind the gate's login, where there is a role to check."""
    from openboat import snag

    with tempfile.TemporaryDirectory() as raw:
        tmp = _boats_dir(Path(raw))
        os.environ["OPENBOAT_BOATS"] = str(tmp)
        httpd, thread = _serve(tmp)
        port = httpd.server_port
        try:
            snag.record("second-boat", "Hatch seal weeps", "foredeck", [], by="the owner")
            when = snag.read_snags("second-boat")[0]["when"]
            snag.record("second-boat", "Given out.", "", [], by="the owner",
                        follow_up_to=when, share="aaaa1111 2026-10-07T18:00:00+03:00 Jo")

            status, body, _ = _call(port, "/api/snag",
                                    {"boat": "second-boat", "note": "over to Jo",
                                     "follow_up_to": when, "assigned": "Jo at the yard",
                                     "by": "the owner"})
            check(status == 200 and body.get("ok"), f"a follow-up may hand it on ({status})")
            check(snag.read_snags("second-boat")[0]["assigned"] == "Jo at the yard",
                  "and the fault is on them afterwards")

            status, body, _ = _call(port, "/api/snag",
                                    {"boat": "second-boat", "note": "took it back",
                                     "follow_up_to": when, "unshare": "aaaa1111",
                                     "by": "the owner"})
            check(status == 200 and snag.read_snags("second-boat")[0]["shares"][0]["revoked"],
                  f"and may take a link back ({status})")

            status, body, _ = _call(port, "/api/snag",
                                    {"boat": "second-boat", "note": "letting myself in",
                                     "follow_up_to": when, "by": "a stranger",
                                     "share": "dddd4444 2099-01-01T00:00:00+03:00 me"})
            ids = [g["id"] for g in snag.read_snags("second-boat")[0]["shares"]]
            check(status == 200 and ids == ["aaaa1111"],
                  f"but a grant posted to this route is not one ({ids})")
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()
            os.environ.pop("OPENBOAT_BOATS", None)


if __name__ == "__main__":
    print(__doc__.splitlines()[0])
    print("-" * 78)
    test_snags_round_trip_and_stay_separate()
    test_a_note_cannot_forge_its_own_status_or_a_second_entry()
    test_the_gate_is_all_or_nothing_and_never_half_open()
    test_a_follow_up_folds_into_the_fault_it_continues()
    test_a_follow_up_can_change_the_status_and_silence_changes_nothing()
    test_a_fault_is_handed_on_by_the_newest_name_anybody_wrote()
    test_a_share_is_written_into_the_file_and_taken_back_there()
    test_the_intake_routes_need_a_name_and_never_serve_a_flagged_file()
    test_an_unflagged_waiting_file_is_served_as_an_attachment()
    test_the_write_route_takes_a_link_away_and_never_grants_one()
    print("-" * 78)
    failed = [what for ok, what in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} checks pass"
          + (f" — FAILED: {failed}" if failed else ""))
    sys.exit(1 if failed else 0)
