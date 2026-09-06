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


if __name__ == "__main__":
    print(__doc__.splitlines()[0])
    print("-" * 78)
    test_snags_round_trip_and_stay_separate()
    test_a_note_cannot_forge_its_own_status_or_a_second_entry()
    test_the_gate_is_all_or_nothing_and_never_half_open()
    test_a_follow_up_folds_into_the_fault_it_continues()
    print("-" * 78)
    failed = [what for ok, what in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} checks pass"
          + (f" — FAILED: {failed}" if failed else ""))
    sys.exit(1 if failed else 0)


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
