"""Read-only Signal K client. Works when the boat is online, says so plainly when it is not.

Signal K is the open marine data standard: one server aboard collects NMEA 0183 and NMEA
2000, GPS, engine and tank data, and serves it over REST and WebSocket. See `signalk/`.

**Nothing here writes.** No steering, no switching, no sending. The client is built from
GET requests only, and that is a design decision rather than an omission — see
`docs/DISCLAIMER.md`.

## Offline is the normal case

A boat is in its berth most of the year, and a berth often has no network. `Offline` is
therefore not an error condition to be logged and escalated; it is the answer most of the
time, and every caller in this project handles it. A dashboard that shows a stack trace
because the boat is where it is supposed to be is a broken dashboard.

That includes a subtler case: a marina captive portal, or Signal K's own restart page,
answers a request with HTTP 200 and a page of HTML. That is the boat being unreachable, and
it is raised as `Offline` too, because letting a JSON parse error escape from here once
killed an anchor watch in the middle of the night.
"""

from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request

from .profile import Profile, load

#: Where the Signal K server is. The profile supplies it; `SIGNALK_URL` overrides it. On a
#: private network the same name works from aboard and from home, which is the whole trick
#: to reaching the boat from a sofa — see `docs/NETWORK.md`.
SIGNALK_URL = os.environ.get("SIGNALK_URL", "")


def _base(boat: Profile | None) -> str:
    return SIGNALK_URL or (boat or load()).signalk_url


class Offline(Exception):
    """The boat's server did not answer. Expected most of the time — handle, don't crash."""


def _get(path: str, timeout: float = 5.0, boat: Profile | None = None):
    base = _base(boat)
    url = f"{base.rstrip('/')}/signalk/v1/api/{path.lstrip('/')}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise Offline(f"no answer from {base}: {exc}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        # A marina captive portal, or Signal K's own restart page, answers 200 with HTML.
        # That is the boat being unreachable, not a bug in the caller — and letting it
        # escape as a JSONDecodeError killed the anchor watch mid-watch.
        raise Offline(f"{base} answered but not with JSON: {exc}") from exc


def _value(tree: dict, *path: str):
    """Walk a Signal K subtree to a leaf .value, tolerating missing branches."""
    node = tree
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    if isinstance(node, dict):
        return node.get("value")
    return node


def _first(tree: dict, branch: str, leaf: str, depth: int = 3):
    """First `leaf` found anywhere under `branch`.

    Signal K nests instances at different depths depending on the domain:
    `propulsion.engine_1.temperature` is two levels, `electrical.batteries.house.voltage`
    and `tanks.fuel.main.currentLevel` are three. Searching rather than indexing means one
    call site works for all of them.

    ⚠️ The FIRST match wins, which is right for one engine, one bank and one tank and wrong
    for anything else: on a twin-engine boat this returns whichever engine the server
    happened to list first, and on a boat with a start battery and a house bank it can
    return the wrong one. Name the path explicitly in your profile's [paths] table when
    your boat has more than one of something.
    """
    def hunt(node, budget: int):
        if not isinstance(node, dict) or budget < 0:
            return None
        if leaf in node:
            candidate = node[leaf]
            if isinstance(candidate, dict) and "value" in candidate:
                return candidate["value"]
            if not isinstance(candidate, dict):
                return candidate
        for key, child in node.items():
            if key in ("meta", "$source", "timestamp") or not isinstance(child, dict):
                continue
            found = hunt(child, budget - 1)
            if found is not None:
                return found
        return None

    return hunt(tree.get(branch), depth)


def _deg(radians):
    return round(math.degrees(radians)) % 360 if radians is not None else None


def _kn(mps):
    return round(mps * 1.94384, 1) if mps is not None else None


def _c(kelvin):
    """Signal K is SI throughout: temperatures are kelvin, pressures pascal."""
    return round(kelvin - 273.15, 1) if kelvin is not None else None


def state(boat: Profile | None = None) -> dict:
    """Everything the helm panel shows, in the units a person reads them in.

    Every field is optional and every one of them will be None on this boat until the
    matching sender exists. That is the normal case, not an error: the panel is built to
    say "no sender" rather than to show a confident zero.
    """
    vessel = _get("vessels/self", boat=boat)

    position = _value(vessel, "navigation", "position") or {}
    environment = vessel.get("environment", {})
    wind = environment.get("wind", {}) if isinstance(environment, dict) else {}

    rpm_hz = _first(vessel, "propulsion", "revolutions")           # Hz, not rpm
    fuel = _first(vessel, "tanks", "currentLevel")                 # 0..1

    return {
        "name": vessel.get("name"),
        "mmsi": vessel.get("mmsi"),
        "lat": position.get("latitude"),
        "lon": position.get("longitude"),
        "sog_kn": _kn(_value(vessel, "navigation", "speedOverGround")),
        "cog_deg": _deg(_value(vessel, "navigation", "courseOverGroundTrue")),
        "heading_deg": _deg(_value(vessel, "navigation", "headingTrue")),
        "depth_m": (lambda d: round(d, 1) if d is not None else None)(
            _value(vessel, "environment", "depth", "belowTransducer")),

        # Apparent wind is what the masthead measures; true wind is derived and only
        # appears once the server has both boat speed and heading to derive it from.
        "wind_apparent_deg": _deg(_value(wind, "angleApparent")),
        "wind_apparent_kn": _kn(_value(wind, "speedApparent")),
        "wind_true_deg": _deg(_value(wind, "directionTrue")),
        "wind_true_kn": _kn(_value(wind, "speedTrue")),

        "water_c": _c(_value(vessel, "environment", "water", "temperature")),
        "air_c": _c(_value(vessel, "environment", "outside", "temperature")),

        "rpm": round(rpm_hz * 60) if rpm_hz is not None else None,
        "coolant_c": _c(_first(vessel, "propulsion", "temperature")),
        "oil_bar": (lambda p: round(p / 100000.0, 2) if p is not None else None)(
            _first(vessel, "propulsion", "oilPressure")),
        "volts": _first(vessel, "electrical", "voltage"),
        "fuel_pct": round(fuel * 100) if fuel is not None else None,
    }


def ais(limit: int = 20, boat: Profile | None = None) -> list[dict]:
    """Other vessels the boat can see. Empty list when there is no AIS receiver.

    Course and speed are carried alongside the position because a target without them is
    a dot, and a dot cannot be assessed: closest-point-of-approach needs a velocity vector
    on both vessels. See `openboat/cpa.py`. AIS legitimately delivers neither for a ship at
    anchor or a target just come into range, so both stay optional.

    ⚠️ `limit` truncates **silently**, and the order is whatever the server returns — not
    distance. Fine for "what is around"; wrong for anything safety-shaped, where the
    dropped target is precisely the one nobody saw. The collision watch raises it to 200 for
    that reason. Raise it too, or sort before you cut.
    """
    vessels = _get("vessels", boat=boat)
    mine = _get("self", boat=boat)           # e.g. "vessels.urn:mrn:imo:mmsi:<nine digits>"
    mine = mine if isinstance(mine, str) else ""

    targets = []
    for key, vessel in vessels.items():
        if not isinstance(vessel, dict) or key in mine:
            continue
        position = _value(vessel, "navigation", "position") or {}
        if not position.get("latitude"):
            continue
        sog = _value(vessel, "navigation", "speedOverGround")          # m/s
        cog = _value(vessel, "navigation", "courseOverGroundTrue")      # radians
        targets.append({
            "name": vessel.get("name") or key,
            "mmsi": vessel.get("mmsi"),
            "lat": position["latitude"],
            "lon": position["longitude"],
            "sog_kn": round(sog * 1.94384, 1) if sog is not None else None,
            "cog_deg": round(math.degrees(cog)) % 360 if cog is not None else None,
        })
    return targets[:limit]
