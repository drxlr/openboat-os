#!/usr/bin/env python3
"""Hand-computed CPA scenarios. Every number below was worked out on paper first.

    python3 tests/test_cpa.py

The arithmetic is printed, not just asserted, because the failure mode of this file is
not "the test breaks" — it is "the test agrees with a plausible wrong implementation".
If the printed derivation and the printed result stop matching, the derivation is the
one to trust.

Geometry used throughout: own ship at 50.3100 N, 4.1500 W, in open water. Targets are
placed due north of her by a whole number of hundredths of a degree of latitude, because
one minute of latitude is one nautical mile *by definition* — so 0.05° is exactly 3.0 nm
and no projection error creeps into the hand arithmetic. Scenario 6 is the one that
deliberately goes east, to check the cosine scaling.

Frame: x east, y north, nautical miles and knots. Velocity from course C at speed S is
(S·sin C, S·cos C) — compass angles, so sin is east.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openboat.cpa import Limits, Vessel, closest_approach, evaluate

OWN_LAT, OWN_LON = 50.3100, -4.1500

failures: list[str] = []


def check(label: str, got, want, tol: float = 1e-6) -> None:
    if want is None or got is None:
        ok = got is want
        shown = f"{got!r}"
    else:
        ok = abs(got - want) <= tol
        shown = f"{got:.6f}"
    print(f"      {'✓' if ok else '✗'} {label:<22} = {shown:>12}   (hand: {want})")
    if not ok:
        failures.append(f"{label}: got {got!r}, hand-computed {want!r}")


def scenario(number: int, title: str, working: str) -> None:
    print(f"\n{number}. {title}")
    for line in working.strip().splitlines():
        print(f"   {line.strip()}")


# --------------------------------------------------------------------------------------
scenario(1, "HEAD-ON, closing — the collision case", """
own    50.3100 N 4.1500 W, course 000, 20 kn
target 50.3600 N 4.1500 W, course 180, 10 kn
r = (0, +0.05° x 60) = (0, 3.0) nm            range 3.0 nm, bearing 000
v_own = 20(sin0, cos0)   = (0, +20)
v_tgt = 10(sin180,cos180) = (0, -10)
v = v_tgt - v_own = (0, -30)                  |v|^2 = 900
r.v = 3.0 x -30 = -90                          negative => closing
TCPA = -(r.v)/|v|^2 = 90/900 = 0.1 h = 6.0 min
CPA  = |r + v(0.1)| = |(0, 3.0-3.0)| = 0.0 nm  they arrive at the same point
range rate = (r.v)/|r| = -90/3 = -30 kn
""")
e = evaluate(Vessel(OWN_LAT, OWN_LON, 0, 20), Vessel(50.3600, -4.1500, 180, 10))
check("range_nm", e.range_nm, 3.0, 1e-9)
check("bearing_deg", e.bearing_deg, 0.0)
check("cpa_nm", e.cpa_nm, 0.0, 1e-9)
check("tcpa_min", e.tcpa_min, 6.0, 1e-9)
check("range_rate_kn", e.range_rate_kn, -30.0, 1e-9)
assert e.aspect == "head-on", e.aspect
assert e.level == "danger", e.level
print(f"      ✓ aspect / level        =  {e.aspect} / {e.level}")


# --------------------------------------------------------------------------------------
scenario(2, "PARALLEL, same speed — never closes, no CPA exists", """
own    50.3100 N, course 090, 12 kn
target 50.3600 N, course 090, 12 kn           r = (0, 3.0) nm
v = v_tgt - v_own = (12,0) - (12,0) = (0,0)   |v| = 0
The separation function |r + vt| is a flat line: 3.0 nm now, 3.0 nm forever.
There is no minimum to solve for, so TCPA is undefined -> None,
and the closest approach is simply the present range, 3.0 nm.
""")
e = evaluate(Vessel(OWN_LAT, OWN_LON, 90, 12), Vessel(50.3600, -4.1500, 90, 12))
check("cpa_nm", e.cpa_nm, 3.0, 1e-9)
check("tcpa_min", e.tcpa_min, None)
assert e.closing is False and e.aspect == "parallel" and e.level == "clear"
print(f"      ✓ closing / aspect      =  {e.closing} / {e.aspect} / {e.level}")


# --------------------------------------------------------------------------------------
scenario(3, "CROSSING — a real, non-zero CPA", """
own    50.3100 N, course 000, 12 kn
target 50.4100 N, course 270, 12 kn           r = (0, +0.1 x 60) = (0, 6.0) nm
v_own = (0, +12);  v_tgt = 12(sin270, cos270) = (-12, 0)
v = (-12, -12)                                 |v|^2 = 144+144 = 288
r.v = 6.0 x -12 = -72                          closing
TCPA = 72/288 = 0.25 h = 15.0 min
CPA position = r + 0.25v = (0-3.0, 6.0-3.0) = (-3.0, +3.0)
CPA = sqrt(9+9) = 3 sqrt(2) = 4.242641 nm     three miles off the port bow
range rate = -72/6 = -12 kn
""")
e = evaluate(Vessel(OWN_LAT, OWN_LON, 0, 12), Vessel(50.4100, -4.1500, 270, 12))
check("range_nm", e.range_nm, 6.0, 1e-9)
check("cpa_nm", e.cpa_nm, 3 * math.sqrt(2), 1e-9)
check("tcpa_min", e.tcpa_min, 15.0, 1e-9)
check("range_rate_kn", e.range_rate_kn, -12.0, 1e-9)
assert e.aspect == "crossing from starboard" and e.level == "clear"
print(f"      ✓ aspect / level        =  {e.aspect} / {e.level}  (4.24 nm is nowhere near)")


# --------------------------------------------------------------------------------------
scenario(4, "PAST CPA — the trap: mathematical CPA is 0.0 nm, in the past", """
own    50.3100 N, course 000, 20 kn
target 50.3600 N, course 000, 30 kn           r = (0, 3.0) nm, dead ahead, faster
v = (0,30) - (0,20) = (0, +10)                |v|^2 = 100
r.v = 3.0 x +10 = +30                          POSITIVE => opening
TCPA = -30/100 = -0.3 h = -18.0 min           the minimum was 18 minutes ago
and at that moment |r + v(-0.3)| = |(0, 3.0-3.0)| = 0.0 nm.
A naive implementation reports "CPA 0.0 nm" and screams. The correct future answer is
that this ship is 3.0 nm ahead and drawing away at 10 kn: closest from now on = 3.0 nm.
""")
e = evaluate(Vessel(OWN_LAT, OWN_LON, 0, 20), Vessel(50.3600, -4.1500, 0, 30))
check("tcpa_min (signed)", e.tcpa_min, -18.0, 1e-8)
check("cpa_nm (future)", e.cpa_nm, 3.0, 1e-9)
check("range_rate_kn", e.range_rate_kn, +10.0, 1e-9)
assert e.closing is False and e.level == "clear", (e.closing, e.level)
print(f"      ✓ closing / aspect      =  {e.closing} / {e.aspect} / {e.level}")
# ...and the raw solver, asked for the unconstrained minimum, still returns the past one:
raw_cpa, raw_t = closest_approach(0.0, 3.0, 0.0, 10.0)
check("raw solver CPA", raw_cpa, 0.0, 1e-12)
check("raw solver t (h)", raw_t, -0.3, 1e-12)


# --------------------------------------------------------------------------------------
scenario(5, "STATIONARY target — a ship at anchor nearby", """
own    50.3100 N, course 000, 12 kn
target 50.3400 N, sog 0.0 kn                  r = (0, +0.03 x 60) = (0, 1.8) nm
v = (0,0) - (0,12) = (0, -12)                 |v|^2 = 144
r.v = 1.8 x -12 = -21.6
TCPA = 21.6/144 = 0.15 h = 9.0 min            CPA = |(0, 1.8-1.8)| = 0.0 nm
Course over ground is meaningless for an anchored ship, so it is not used: velocity is
taken as zero and the aspect is named "stationary" rather than invented as a crossing.
""")
e = evaluate(Vessel(OWN_LAT, OWN_LON, 0, 12), Vessel(50.3400, -4.1500, 137.0, 0.0))
check("cpa_nm", e.cpa_nm, 0.0, 1e-6)
check("tcpa_min", e.tcpa_min, 9.0, 1e-6)
assert e.aspect == "stationary" and e.level == "danger"
print(f"      ✓ aspect / level        =  {e.aspect} / {e.level}")


# --------------------------------------------------------------------------------------
scenario(6, "EAST offset — the cosine scaling in the tangent plane", """
own    50.3100 N 4.1500 W, target 50.3100 N 4.0500 W  (0.1 deg of longitude)
Longitude minutes shrink by cos(latitude): cos(50.31 deg) = 0.63863352
x = 0.1 x 60 x 0.63863352 = 3.831801 nm, y = 0
range 3.831801 nm, bearing atan2(x, y) = atan2(+, 0) = 090
""")
e = evaluate(Vessel(OWN_LAT, OWN_LON, 0, 0), Vessel(50.3100, -4.0500, 0, 0))
check("range_nm", e.range_nm, 6.0 * math.cos(math.radians(50.3100)), 1e-9)
check("range_nm (literal)", e.range_nm, 3.831801, 1e-6)
check("bearing_deg", e.bearing_deg, 90.0, 1e-9)


# --------------------------------------------------------------------------------------
scenario(7, "MISSING data — a target under way with no course reported", """
AIS delivers this constantly. There is no honest CPA for a moving object whose direction
is unknown, so none is offered: no zero, no guess, no assumption that it is stopped.
""")
e = evaluate(Vessel(OWN_LAT, OWN_LON, 0, 12), Vessel(50.3600, -4.1500, None, 14.0))
check("cpa_nm", e.cpa_nm, None)
check("tcpa_min", e.tcpa_min, None)
assert e.aspect == "unknown" and e.level == "unknown"
print(f"      ✓ aspect / level        =  {e.aspect} / {e.level}")


# --------------------------------------------------------------------------------------
scenario(8, "ORDERING — the danger ranks above the merely close", """
Two targets: A closes head-on to a 0.0 nm CPA in 6 min; B sits 0.4 nm away, parallel,
matched speed, never converging. B is nearer *now*; A is the one that matters. Sorting
is by level first, then time in hand — never by range.
""")
own = Vessel(OWN_LAT, OWN_LON, 0, 20)
crosser = Vessel(50.3600, -4.1500, 180, 10, name="A crossing")
tagalong = Vessel(OWN_LAT + 0.4 / 60, OWN_LON, 0, 20, name="B parallel")
from openboat.cpa import assess
order = assess(own, [tagalong, crosser], Limits())
print(f"      -> {order[0].target.name} ({order[0].level})  then  "
      f"{order[1].target.name} ({order[1].level})")
assert order[0].target.name == "A crossing" and order[0].level == "danger"
assert order[1].target.name == "B parallel" and order[1].level == "watch"


print("\n" + "-" * 78)
if failures:
    print(f"FAILED — {len(failures)} check(s) disagree with the hand arithmetic:")
    for line in failures:
        print(f"  {line}")
    sys.exit(1)
print("All 8 scenarios agree with the hand-computed arithmetic.")
