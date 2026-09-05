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


if __name__ == "__main__":
    print(__doc__.splitlines()[0])
    print("-" * 78)
    test_snags_round_trip_and_stay_separate()
    print("-" * 78)
    failed = [what for ok, what in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} checks pass"
          + (f" — FAILED: {failed}" if failed else ""))
    sys.exit(1 if failed else 0)
