"""The private-content guard must see a position in the shape this software writes one.

A berth is the fact in this repository with the worst consequences if it escapes: it says
where a valuable object sits unattended, and unlike a name it cannot be retracted once it
is in somebody's clone. `scripts/check-private.py` is the whole defence, and it ran as a
pre-commit hook for months while being blind to the only format that would ever carry a
position out by accident.

The regex looked for two numbers separated by a comma or a space — `12.3456, -65.4321` —
which is how a person writes a coordinate in prose. Nothing this project writes looks like
that. Its logbook, its ledger, its tracks and its AIS targets all record a position as
named JSON fields, where the separator between the two numbers is `, "lon": `. So the
guard reported a clean tree while a real berth sat in a tracked file, and the hook passed
the commit that carried it.

These tests pin both shapes. The bare-pair cases are here so the fix cannot quietly cost
the behaviour that already worked.

    python3 tests/test_private_check.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# scripts/ is not a package, and check-private.py is not an importable name. Load it by
# path rather than reshaping the repository around a test.
_spec = importlib.util.spec_from_file_location("check_private",
                                               ROOT / "scripts" / "check-private.py")
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

#: An invented box, mid-ocean. Deliberately NOT read from `.private-markers` and
#: deliberately not anybody's real one: that file is gitignored and personal, a test
#: depending on it fails on a fresh clone, and a test *containing* a real box would
#: publish the rectangle the whole mechanism exists to hide — which is exactly the
#: mistake that once shipped the example box in the checker itself.
BOX = (12.0, 13.0, -66.0, -65.0)

results: list[tuple[bool, str]] = []


def check(condition: bool, what: str) -> None:
    results.append((bool(condition), what))
    print(f"{'  ok  ' if condition else '  FAIL'}  {what}")


def caught(text: str, box=BOX) -> bool:
    """True if the guard would report a position inside `box` anywhere in `text`."""
    lat_min, lat_max, lon_min, lon_max = box
    for _, lat_text, lon_text in guard.positions(text):
        lat, lon = float(lat_text), float(lon_text)
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return True
    return False


def test_the_shape_this_software_actually_writes() -> None:
    """One line of logbook.jsonl, as the dashboard writes it. This is the regression."""
    line = ('{"what": "check", "verdict": "noted", "readings": {"rpm": 1420, '
            '"lat": 12.3456, "lon": -65.4321, "sog_kn": 6.0}}')
    check(caught(line), "a position in JSON fields is caught")

    check(caught('{"lon": -65.4321, "lat": 12.3456}'),
          "the same, with longitude written first")

    check(caught('{\n  "readings": {\n    "lat": 12.3456,\n    "lon": -65.4321\n  }\n}'),
          "a pretty-printed position, split across lines, is caught")

    check(caught('{"latitude": 12.3456, "longitude": -65.4321}'),
          "spelled out as latitude/longitude")


def test_the_shape_it_always_caught() -> None:
    """The old behaviour, pinned so the fix cannot cost it."""
    check(caught("berthed at 12.3456, -65.4321 last winter"),
          "a bare pair in prose is still caught")
    check(caught("12.3456 -65.4321"),
          "a bare pair separated by a space is still caught")


def test_what_must_not_be_reported() -> None:
    """A guard that cries wolf gets switched off, so the false-positive cases matter."""
    check(not caught('{"lat": 50.3640, "lon": -4.1310}'),
          "the demo boat's harbour, far outside the box, is not reported")
    check(not caught('{"lat": 12.3456}'),
          "a latitude alone is not a position and is not reported")
    check(not caught('{"rpm": 12.3456, "hours": -65.4321}'),
          "two unrelated numbers that happen to fall in range are not a position")
    check(not caught("version 12.3456.33"),
          "a version string is not a position")


def test_a_line_number_is_reported() -> None:
    text = 'first line\n{"lat": 12.3456, "lon": -65.4321}\n'
    numbers = [n for n, _, _ in guard.positions(text)]
    check(numbers and min(numbers) == 2,
          f"the position is reported on line 2, not line 1 (got {numbers})")


if __name__ == "__main__":
    print(__doc__.splitlines()[0])
    print("-" * 78)
    for case in (test_the_shape_this_software_actually_writes,
                 test_the_shape_it_always_caught,
                 test_what_must_not_be_reported,
                 test_a_line_number_is_reported):
        case()
    print("-" * 78)
    failed = [what for ok, what in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} checks pass"
          + (f" — FAILED: {failed}" if failed else ""))
    sys.exit(1 if failed else 0)
