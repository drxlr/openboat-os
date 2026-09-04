#!/usr/bin/env python3
"""[paths] and [bands] must actually reach the numbers the helm shows.

    python3 tests/test_paths.py

The profile has always *accepted* a [paths] table. Until this test existed, boat.state()
and engine.read() ignored it and took whichever engine or tank Signal K listed first.
On a twin that is a plausible wrong temperature; on a boat with house and start banks
it is the wrong volts. Same class of bug as the rest of test_regressions.py.

Do not weaken an assertion here to make a change pass.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from openboat.boat import leaves, state  # noqa: E402
from openboat.engine import read  # noqa: E402
from openboat.profile import DEFAULT_PATHS, Profile  # noqa: E402

results: list[tuple[bool, str]] = []


def check(condition: bool, what: str) -> None:
    results.append((bool(condition), what))
    print(f"{'  ok  ' if condition else '  FAIL'}  {what}")


def leaf(value):
    return {"value": value}


def twin() -> dict:
    """Port listed first, start battery listed first, fresh water listed before fuel."""
    return {
        "name": "Twin",
        "navigation": {
            "position": {"value": {"latitude": 50.36, "longitude": -4.13}},
            "speedOverGround": {"value": 5.0 / 1.94384},
        },
        "propulsion": {
            "port": {
                "revolutions": leaf(20.0),          # 1200 rpm
                "temperature": leaf(333.15),         # 60.0 °C
                "oilPressure": leaf(250000),         # 2.50 bar
            },
            "starboard": {
                "revolutions": leaf(40.0),          # 2400 rpm
                "temperature": leaf(358.15),         # 85.0 °C
                "oilPressure": leaf(400000),         # 4.00 bar
            },
        },
        "electrical": {
            "batteries": {
                "start": {"voltage": leaf(12.1)},
                "house": {"voltage": leaf(13.4)},
            }
        },
        "tanks": {
            "freshWater": {"main": {"currentLevel": leaf(0.80)}},
            "fuel": {"main": {"currentLevel": leaf(0.45)}},
        },
    }


def with_paths(**overrides) -> Profile:
    return replace(Profile(), paths={**DEFAULT_PATHS, **overrides})


def test_named_engine_wins() -> None:
    vessel = twin()
    port = state(boat=Profile(), vessel=vessel)
    # Default path engine_1 is missing; fallback is first-listed = port.
    check(port["rpm"] == 1200, "unconfigured twin falls back to the first engine")
    check(port["coolant_c"] == 60.0, "that first engine's coolant, not the other's")

    starboard = state(
        boat=with_paths(
            engine_revolutions="propulsion.starboard.revolutions",
            engine_temperature="propulsion.starboard.temperature",
            engine_oil_pressure="propulsion.starboard.oilPressure",
        ),
        vessel=vessel,
    )
    check(starboard["rpm"] == 2400, "[paths] selects the starboard engine, not the first")
    check(starboard["coolant_c"] == 85.0, "and its coolant, not port's")
    check(starboard["oil_bar"] == 4.0, "and its oil pressure")


def test_missing_override_is_absent() -> None:
    panel = state(
        boat=with_paths(engine_temperature="propulsion.missing.temperature"),
        vessel=twin(),
    )
    check(panel["coolant_c"] is None,
          "an explicit path that is absent is no sender, not the other engine")
    check(panel["rpm"] == 1200,
          "the other engine fields still fall back")


def test_named_battery_and_fuel() -> None:
    vessel = twin()
    house = state(boat=Profile(), vessel=vessel)
    check(house["volts"] == 13.4,
          "default battery path is house, even when start is listed first")
    check(house["fuel_pct"] == 45,
          "fuel is the fuel tank, not the fresh-water tank listed above it")

    start = state(boat=with_paths(battery_voltage="electrical.batteries.start.voltage"),
                  vessel=vessel)
    check(start["volts"] == 12.1, "[paths] can point at the start bank")


def test_engine_log_follows_paths() -> None:
    vessel = twin()
    first = read(vessel, boat=Profile())
    check(first["rpm"] == 1200.0, "engine log fallback is the first engine")

    named = read(vessel, boat=with_paths(
        engine_revolutions="propulsion.starboard.revolutions",
        engine_temperature="propulsion.starboard.temperature",
        engine_oil_pressure="propulsion.starboard.oilPressure",
        battery_voltage="electrical.batteries.start.voltage",
    ))
    check(named["rpm"] == 2400.0, "engine log honours [paths] for rpm")
    check(named["temp_c"] == 85.0, "engine log honours [paths] for coolant")
    check(named["volts"] == 12.1, "engine log honours [paths] for the battery")


def test_leaves_list_both_engines() -> None:
    listed = {row["path"] for row in leaves(vessel=twin())}
    check("propulsion.port.temperature" in listed, "leaves() sees the port engine")
    check("propulsion.starboard.temperature" in listed, "leaves() sees the starboard engine")
    check("electrical.batteries.start.voltage" in listed, "leaves() sees the start bank")


def test_bands_and_paths_reach_the_dashboard() -> None:
    profile = replace(Profile(),
                      bands={"engine_temperature": [[82, 90, "warn"], [90, 999, "bad"]]},
                      paths={**DEFAULT_PATHS,
                             "engine_temperature": "propulsion.port.temperature"})
    payload = profile.as_dict()
    check(payload["bands"]["engine_temperature"][0][2] == "warn",
          "/api/profile carries [bands] so the helm can paint them")
    check(payload["paths"]["engine_temperature"] == "propulsion.port.temperature",
          "/api/profile carries [paths] so the helm knows what it is bound to")


if __name__ == "__main__":
    print(__doc__.splitlines()[0])
    print("-" * 78)
    for case in (test_named_engine_wins, test_missing_override_is_absent,
                 test_named_battery_and_fuel, test_engine_log_follows_paths,
                 test_leaves_list_both_engines, test_bands_and_paths_reach_the_dashboard):
        case()
    print("-" * 78)
    failed = [what for ok, what in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} checks pass"
          + (f" — FAILED: {failed}" if failed else ""))
    sys.exit(1 if failed else 0)
