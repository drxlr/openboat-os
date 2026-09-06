"""A check records the machinery, never the position.

`logbook.record()` stamps whatever the boat is publishing onto the entry. That is right for
rpm, coolant, oil and volts — they are what make "the impeller looked fine" useful next
season. It was wrong for latitude and longitude, which add nothing to that sentence and are
the one genuinely private number in the set: a berth says where a valuable object sits
unattended, and unlike a name it cannot be retracted once somebody has cloned it.

The module already knew this. `FIRST` is a deliberately position-free list of machinery, and
the comment beside it says a summary leading with latitude buries the number that mattered.
The line after it then swept every remaining number in, latitude included — so the curation
was cosmetic and every check ever logged carried a position anyway. In this repository one
of those files was tracked.

The fix is here rather than in where the file is written. Moving a log somewhere untracked
only moves the pile; not recording the position at all ends the class.

    python3 tests/test_logbook_position.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openboat import logbook  # noqa: E402

results: list[tuple[bool, str]] = []


def check(condition: bool, what: str) -> None:
    results.append((bool(condition), what))
    print(f"{'  ok  ' if condition else '  FAIL'}  {what}")


#: What a boat under way publishes, as `boat.state()` returns it.
UNDER_WAY = {"rpm": 1420, "coolant_c": 82.6, "oil_bar": 1.98, "volts": 14.1,
             "depth_m": 23.3, "sog_kn": 6.0, "cog_deg": 311, "heading_deg": 303,
             "lat": 12.3456, "lon": -65.4321}


def readings_for(state: dict) -> dict:
    """The readings `record()` would keep, without touching the filesystem."""
    numbers = {k: v for k, v in state.items()
               if isinstance(v, (int, float)) and v is not None}
    kept = {k: numbers[k] for k in logbook.FIRST if k in numbers}
    kept.update({k: v for k, v in numbers.items()
                 if k not in kept and k not in logbook.NEVER})
    return kept


def test_position_never_reaches_a_check() -> None:
    kept = readings_for(UNDER_WAY)
    for field in ("lat", "lon", "latitude", "longitude"):
        check(field not in kept, f"{field!r} is not recorded on a check")


def test_the_machinery_still_is() -> None:
    kept = readings_for(UNDER_WAY)
    for field in ("rpm", "coolant_c", "oil_bar", "volts", "depth_m"):
        check(kept.get(field) == UNDER_WAY[field],
              f"{field!r} is still recorded, unchanged")
    check(kept.get("sog_kn") == 6.0,
          "speed is still recorded — it is not a position")
    check(list(kept)[:4] == ["rpm", "coolant_c", "oil_bar", "volts"],
          "the machinery still leads the entry, in FIRST order")


def test_the_guard_would_catch_a_regression() -> None:
    """If the exclusion is ever lost, the private-content check must see it.

    The two halves are independent — one stops a position being written, the other stops a
    written one being committed — and this pins that the second still covers the first's
    output format.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "check_private", Path(__file__).resolve().parent.parent / "scripts" / "check-private.py")
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)

    leaked = '{"what": "check", "readings": {"rpm": 1420, "lat": 12.3456, "lon": -65.4321}}'
    found = [(la, lo) for _, la, lo in guard.positions(leaked)]
    check(found == [("12.3456", "-65.4321")],
          f"a position that slipped into an entry is still caught downstream (got {found})")


if __name__ == "__main__":
    print(__doc__.splitlines()[0])
    print("-" * 78)
    for case in (test_position_never_reaches_a_check,
                 test_the_machinery_still_is,
                 test_the_guard_would_catch_a_regression):
        case()
    print("-" * 78)
    failed = [what for ok, what in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} checks pass"
          + (f" — FAILED: {failed}" if failed else ""))
    sys.exit(1 if failed else 0)
