#!/usr/bin/env python3
"""AIS parsing, dead reckoning, and the profile-derived bounding box.

    python3 tests/test_ais.py

Ported from `70-ais-watch/test_sources.py`, which also covered Signal K unit-conversion
and a source-fallback chain (Signal K, then aisstream, then a fixture). Those two live in
a `watch`-style orchestration layer together with the boat's own Signal K client, neither
of which is part of this port — the Signal K client is being built separately as
`openboat.boat`. What is ported here is everything that stands on its own: parsing an
aisstream.io report into a `Vessel`, dead-reckoning a vessel forward, and the bounding box
that now comes from the profile's forecast point instead of a hard-coded location.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openboat.ais import _to_vessel, bounding_box
from openboat.cpa import Vessel, advance
from openboat.profile import load as load_profile

failures: list[str] = []


def check(label: str, got, want, tol: float | None = None) -> None:
    if tol is not None and isinstance(want, float) and isinstance(got, float):
        ok = abs(got - want) <= tol
    else:
        ok = got == want
    print(f"   {'✓' if ok else '✗'} {label:<48} {got!r}")
    if not ok:
        failures.append(f"{label}: got {got!r}, expected {want!r}")


print("\n1. aisstream: the not-available sentinels are not courses")
meta = {"MMSI": 232123456, "ShipName": "  ATLANTIC STAR  ",
        "latitude": 50.32, "longitude": -4.12}
usable = _to_vessel({"Latitude": 50.32, "Longitude": -4.12,
                     "Cog": 187.4, "Sog": 13.2}, meta)
check("course read through", usable.cog_deg, 187.4)
check("name trimmed", usable.name, "ATLANTIC STAR")
check("mmsi as a string", usable.mmsi, "232123456")

# AIS encodes "course not available" as 360.0 and "speed not available" as 102.3. Passed
# on unchanged those become a ship steering due north at 102 knots.
sentinels = _to_vessel({"Latitude": 50.32, "Longitude": -4.12,
                        "Cog": 360.0, "Sog": 102.3}, meta)
check("COG 360.0 sentinel -> None", sentinels.cog_deg, None)
check("SOG 102.3 sentinel -> None", sentinels.sog_kn, None)
check("no position -> no vessel", _to_vessel({}, {"MMSI": 1}), None)


print("\n2. Dead reckoning moves an under-way vessel and leaves a stopped one alone")
start = Vessel(50.3100, -4.1500, cog_deg=120.0, sog_kn=18.0)
moved = advance(start, 600)                      # 10 minutes
run_nm = math.hypot((moved.lat - start.lat) * 60,
                    (moved.lon - start.lon) * 60 * math.cos(math.radians(start.lat)))
check("vessel ran 18 kn x 10 min = 3.0 nm", round(run_nm, 2), 3.0, 0.01)

still = advance(Vessel(50.30, -4.10, 90.0, 0.1), 3600)   # 0.1 kn is below STATIONARY_KN
check("a vessel under STATIONARY_KN does not dead-reckon", (still.lat, still.lon), (50.30, -4.10))

no_course = advance(Vessel(50.30, -4.10, None, 12.0), 3600)
check("a vessel with no course does not dead-reckon", (no_course.lat, no_course.lon), (50.30, -4.10))


print("\n3. bounding_box(): derived from the profile's forecast point, not a constant")
demo = load_profile()
box = bounding_box(demo, radius_nm=25.0)
(south, west), (north, east) = box
lat, lon = demo.forecast_point
check("forecast point latitude falls inside the box", south <= lat <= north, True)
check("forecast point longitude falls inside the box", west <= lon <= east, True)
check("box spans roughly 25 nm north of the point", round((north - lat) * 60, 1), 25.0, 0.1)
check("box spans roughly 25 nm south of the point", round((lat - south) * 60, 1), 25.0, 0.1)


print("\n" + "-" * 78)
if failures:
    print(f"FAILED — {len(failures)} check(s):")
    for line in failures:
        print(f"  {line}")
    sys.exit(1)
print("AIS parsing, dead reckoning and the profile-derived bounding box all check out.")
