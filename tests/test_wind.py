#!/usr/bin/env python3
"""True wind, derived from apparent — worked by hand, then asserted.

    python3 tests/test_wind.py

This exists because the first version of `_true_wind` added 180° to the result, and the
consequence was not a crash or an obviously silly number. It was 20 knots on the bow
reported as a following breeze: a plausible reading, on the instrument that decides whether
a small boat leaves the harbour, wrong by exactly half a turn.

Every case below is worked out on paper first and the expected answer written down before
the code was asked. Signal K's wind angles are the direction the wind comes **from**, and
so are these.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from openboat.boat import _true_wind  # noqa: E402

results: list[tuple[bool, str]] = []


def check(condition: bool, what: str) -> None:
    results.append((bool(condition), what))
    print(f"{'  ok  ' if condition else '  FAIL'}  {what}")


def wind(app_kn, app_deg, boat_kn, heading):
    return _true_wind({"angleApparent": {"value": app_deg * 3.14159265 / 180},
                       "speedApparent": {"value": app_kn / 1.9438445}},
                      boat_kn, heading)


def near(got, want, tol=0.6) -> bool:
    return got is not None and abs(got - want) <= tol


def deg_near(got, want, tol=2) -> bool:
    if got is None:
        return False
    diff = abs((got - want + 180) % 360 - 180)
    return diff <= tol


# --------------------------------------------------------------------------------------
# The four cases. Each one is a situation a person can picture from the deck.
# --------------------------------------------------------------------------------------
def test_headwind() -> None:
    """Motoring at 10 kn into 20 kn apparent on the bow. Ten of it is the boat's own."""
    w = wind(20, 0, 10, 0)
    check(near(w["wind_true_kn"], 10), "20 kn apparent on the bow at 10 kn is 10 kn true")
    check(deg_near(w["wind_true_deg"], 0), "and it still comes from ahead, not astern")


def test_stopped_beam() -> None:
    """Stopped. Whatever the masthead reads is the truth."""
    w = wind(15, 90, 0, 0)
    check(near(w["wind_true_kn"], 15), "stopped, apparent is true")
    check(deg_near(w["wind_true_deg"], 90), "15 kn on the starboard beam, heading north, is from 090")

    w = wind(15, 90, 0, 270)
    check(deg_near(w["wind_true_deg"], 0),
          "the same wind, heading west, is from 000 — the heading is part of the answer")


def test_following() -> None:
    """Running downwind: the boat's speed is subtracted from what it feels, so true is more."""
    w = wind(5, 180, 10, 0)
    check(near(w["wind_true_kn"], 15), "5 kn apparent from astern at 10 kn is 15 kn true")
    check(deg_near(w["wind_true_deg"], 180), "and it comes from astern")


def test_dead_calm() -> None:
    """Apparent exactly equals boat speed on the bow: there is no wind, only the boat."""
    w = wind(10, 0, 10, 90)
    check(w["wind_true_kn"] == 0.0, "a wind made entirely by the boat reads as calm")
    check(w["wind_true_deg"] is None,
          "and reports no direction, because a calm has none — not a leftover angle")


def test_published_wins() -> None:
    """A server that derives its own true wind knows more than this arithmetic does."""
    w = _true_wind({"speedTrue": {"value": 5.0}, "directionTrue": {"value": 1.5708},
                    "speedApparent": {"value": 10.0}, "angleApparent": {"value": 0.0}},
                   10, 0)
    check(w["wind_true_derived"] is False, "a published true wind is used as published")
    check(near(w["wind_true_kn"], 9.7, 0.2), "and is not recomputed from apparent")


def test_missing_inputs() -> None:
    """No heading, no answer. A derived wind needs all of its inputs or none of it is real."""
    w = _true_wind({"speedApparent": {"value": 10.0}, "angleApparent": {"value": 0.0}}, 5, None)
    check(w["wind_true_kn"] is None and w["wind_true_derived"] is False,
          "a missing heading yields no derived wind rather than a plausible one")

    w = _true_wind({}, 5, 0)
    check(w["wind_true_kn"] is None, "no apparent wind yields no true wind")


if __name__ == "__main__":
    print(__doc__.splitlines()[0])
    print("-" * 78)
    for case in (test_headwind, test_stopped_beam, test_following, test_dead_calm,
                 test_published_wins, test_missing_inputs):
        case()
    print("-" * 78)
    failed = [what for ok, what in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} checks pass"
          + (f" — FAILED: {failed}" if failed else ""))
    sys.exit(1 if failed else 0)
