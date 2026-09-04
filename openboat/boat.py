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

from .profile import DEFAULT_PATHS, Profile, load

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
    return the wrong one. `state()` and `engine.read()` honour `[paths]` first and only
    fall back to this hunt when the *default* path is missing. An explicit override that
    is absent is "no sender", not the next instance along.
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


def _walk(tree: dict, dotted: str):
    """Walk a dotted Signal K path. Returns the node, or None if a step is missing."""
    if not dotted or not isinstance(tree, dict):
        return None
    node = tree
    for key in dotted.split("."):
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def _at(tree: dict, dotted: str):
    """Leaf `.value` at a dotted path, or None when the path is absent."""
    node = _walk(tree, dotted)
    if node is None:
        return None
    if isinstance(node, dict):
        return node.get("value") if "value" in node else None
    return node


def _leaf_at(tree: dict, dotted: str) -> dict:
    """Whole leaf (value and timestamp) at a dotted path, or {} when absent."""
    node = _walk(tree, dotted)
    if node is None:
        return {}
    return node if isinstance(node, dict) else {"value": node}


def _configured(boat: Profile | None, key: str) -> str | None:
    paths = boat.paths if boat is not None else DEFAULT_PATHS
    return paths.get(key)


def path_value(tree: dict, key: str, boat: Profile | None = None, fallback=None):
    """Read one profile path from a vessel tree.

    The named path wins when it is present. If it is still the default and missing,
    `fallback` runs — that is how an unconfigured boat with `propulsion.port` instead
    of `engine_1` keeps working. An explicit override that is missing is None: the
    skipper named a sender, and it is not there.
    """
    dotted = _configured(boat, key)
    if dotted:
        node = _walk(tree, dotted)
        if node is not None:
            if isinstance(node, dict) and "value" in node:
                return node.get("value")
            if not isinstance(node, dict):
                return node
        elif dotted != DEFAULT_PATHS.get(key):
            return None
    if fallback is not None:
        return fallback()
    return None


def path_leaf(tree: dict, key: str, boat: Profile | None = None, fallback=None) -> dict:
    """Like `path_value`, but keeps the Signal K timestamp. Used by the engine log."""
    dotted = _configured(boat, key)
    if dotted:
        node = _walk(tree, dotted)
        if node is not None:
            if isinstance(node, dict) and "value" in node:
                return node
            if not isinstance(node, dict):
                return {"value": node}
        elif dotted != DEFAULT_PATHS.get(key):
            return {}
    if fallback is not None:
        return fallback() or {}
    return {}


def leaves(vessel: dict | None = None, boat: Profile | None = None) -> list[dict]:
    """Every Signal K leaf under `vessels/self`, for filling in `[paths]`.

    A skipper connecting a real boat should not have to guess instance names. This is
    the tree the server is actually publishing, with source and age where it has them.
    """
    tree = vessel if vessel is not None else _get("vessels/self", boat=boat)
    found: list[dict] = []

    def hunt(node, prefix: str) -> None:
        if not isinstance(node, dict):
            return
        if "value" in node and prefix:
            meta = node.get("meta") if isinstance(node.get("meta"), dict) else {}
            found.append({
                "path": prefix,
                "value": node.get("value"),
                "unit": meta.get("units"),
                "source": node.get("$source"),
                "timestamp": node.get("timestamp"),
            })
            return
        for key, child in node.items():
            if key in ("meta", "$source", "timestamp", "pgn", "sentence") or (
                    isinstance(key, str) and key.startswith("$")):
                continue
            if not isinstance(child, dict):
                continue
            hunt(child, f"{prefix}.{key}" if prefix else key)

    hunt(tree, "")
    return found


def _deg(radians):
    return round(math.degrees(radians)) % 360 if radians is not None else None


def _kn(mps):
    return round(mps * 1.94384, 1) if mps is not None else None


def _c(kelvin):
    """Signal K is SI throughout: temperatures are kelvin, pressures pascal."""
    return round(kelvin - 273.15, 1) if kelvin is not None else None


def state(boat: Profile | None = None, vessel: dict | None = None) -> dict:
    """Everything the helm panel shows, in the units a person reads them in.

    Every field is optional and every one of them will be None on this boat until the
    matching sender exists. That is the normal case, not an error: the panel is built to
    say "no sender" rather than to show a confident zero.

    Instance names (`engine_1`, `house`, `main`) come from the profile's `[paths]` table.
    Pass `vessel` to parse a tree you already have — tests, and anyone who has just
    fetched `vessels/self` for another reason.
    """
    boat = boat or load()
    if vessel is None:
        vessel = _get("vessels/self", boat=boat)

    def named(key, hunt=None):
        return path_value(vessel, key, boat, fallback=hunt)

    position = named("position") or _value(vessel, "navigation", "position") or {}
    if not isinstance(position, dict):
        position = {}
    environment = vessel.get("environment", {})
    wind = environment.get("wind", {}) if isinstance(environment, dict) else {}

    rpm_hz = named("engine_revolutions",
                   lambda: _first(vessel, "propulsion", "revolutions"))
    fuel = named("fuel_level",
                 lambda: _first(vessel.get("tanks") or {}, "fuel", "currentLevel"))

    sog = named("speed_over_ground",
                lambda: _value(vessel, "navigation", "speedOverGround"))
    stw = named("speed_through_water",
                lambda: _value(vessel, "navigation", "speedThroughWater"))
    heading = named("heading",
                    lambda: _value(vessel, "navigation", "headingTrue"))

    return {
        "name": vessel.get("name"),
        "mmsi": vessel.get("mmsi"),
        "lat": position.get("latitude"),
        "lon": position.get("longitude"),
        "sog_kn": _kn(sog),
        "cog_deg": _deg(named("course_over_ground",
                              lambda: _value(vessel, "navigation", "courseOverGroundTrue"))),
        "heading_deg": _deg(heading),
        "depth_m": (lambda d: round(d, 1) if d is not None else None)(
            named("depth", lambda: _value(vessel, "environment", "depth", "belowTransducer"))),

        # Apparent wind is what the masthead measures; true wind is derived and only
        # appears once the server has both boat speed and heading to derive it from.
        "wind_apparent_deg": _deg(named("wind_angle",
                                        lambda: _value(wind, "angleApparent"))),
        "wind_apparent_kn": _kn(named("wind_speed",
                                      lambda: _value(wind, "speedApparent"))),
        # Derived here when the server does not publish it. The helm rose has always
        # preferred true wind over apparent, but nothing computed it, so on any boat whose
        # Signal K has no wind-derivation plugin the rose silently fell back to apparent
        # forever — a feature the interface was ready for and the data never arrived for.
        **_true_wind(wind, _kn(stw) or _kn(sog), _deg(heading)),

        "stw_kn": _kn(stw),
        "rate_of_turn": _value(vessel, "navigation", "rateOfTurn"),
        "roll_deg": _deg(_value(vessel, "navigation", "attitude", "roll")),
        "pitch_deg": _deg(_value(vessel, "navigation", "attitude", "pitch")),
        "rudder_deg": _deg(named("rudder",
                                 lambda: _value(vessel, "steering", "rudderAngle"))),
        "trip_log_nm": (lambda m: round(m / 1852.0, 1) if m is not None else None)(
            _value(vessel, "navigation", "trip", "log")),

        "water_c": _c(named("water_temperature",
                            lambda: _value(vessel, "environment", "water", "temperature"))),
        "air_c": _c(named("air_temperature",
                          lambda: _value(vessel, "environment", "outside", "temperature"))),

        "rpm": round(rpm_hz * 60) if rpm_hz is not None else None,
        "coolant_c": _c(named("engine_temperature",
                              lambda: _first(vessel, "propulsion", "temperature"))),
        "oil_bar": (lambda p: round(p / 100000.0, 2) if p is not None else None)(
            named("engine_oil_pressure",
                  lambda: _first(vessel, "propulsion", "oilPressure"))),
        "oil_c": _c(named("engine_oil_temperature",
                          lambda: _first(vessel, "propulsion", "oilTemperature"))),
        "exhaust_c": _c(named("engine_exhaust_temperature",
                              lambda: _first(vessel, "propulsion", "exhaustTemperature"))),
        "engine_load_pct": (lambda r: round(r * 100) if r is not None else None)(
            named("engine_load", lambda: _first(vessel, "propulsion", "engineLoad"))),
        "fuel_lph": (lambda r: round(r * 3600000.0, 1) if r is not None else None)(
            named("engine_fuel_rate", lambda: _first(vessel, "propulsion", "rate"))),
        "engine_hours": (lambda s: round(s / 3600.0, 1) if s is not None else None)(
            named("engine_hours", lambda: _first(vessel, "propulsion", "runTime"))),

        "volts": named("battery_voltage",
                       lambda: _first(vessel, "electrical", "voltage")),
        "amps": named("battery_current",
                      lambda: _first(vessel, "electrical", "current")),
        "soc_pct": (lambda r: round(r * 100) if r is not None else None)(
            named("battery_soc",
                  lambda: _first(vessel, "electrical", "stateOfCharge"))),

        "fuel_pct": round(fuel * 100) if fuel is not None else None,
        "water_pct": (lambda r: round(r * 100) if r is not None else None)(
            named("water_level", lambda: _tank_level(vessel, "freshWater"))),
        "waste_pct": (lambda r: round(r * 100) if r is not None else None)(
            named("waste_level", lambda: _tank_level(vessel, "wasteWater"))),

        "pressure_hpa": (lambda p: round(p / 100.0) if p is not None else None)(
            named("pressure",
                  lambda: _value(vessel, "environment", "outside", "pressure"))),
        "humidity_pct": (lambda r: round(r * 100) if r is not None else None)(
            named("humidity",
                  lambda: _value(vessel, "environment", "outside", "humidity"))),

        # Below the keel is the number that matters and the one nobody has: the sounder
        # measures from its own face. One profile constant turns a reading into an answer,
        # and its absence leaves this None rather than guessing the offset.
        "depth_keel_m": _below_keel(vessel, boat),
    }


def _true_wind(wind: dict, boat_kn, heading_deg) -> dict:
    """True wind from apparent, when the server has not already derived it.

    Vector arithmetic, not a model: the apparent vector is what the masthead feels, which is
    the true wind plus the wind the boat makes by moving. Subtract the boat's own motion and
    what remains is the true wind.

    Published values always win — a server with a real derivation plugin, or a masthead that
    does it in hardware, knows more than this does (it can use speed *through the water*
    and correct for leeway). This only fills a hole, and only when it has everything it
    needs; missing an input returns None rather than a plausible number.
    """
    published_kn = _kn(_value(wind, "speedTrue"))
    published_deg = _deg(_value(wind, "directionTrue"))
    if published_kn is not None and published_deg is not None:
        return {"wind_true_kn": published_kn, "wind_true_deg": published_deg,
                "wind_true_derived": False}

    apparent_kn = _kn(_value(wind, "speedApparent"))
    apparent_deg = _deg(_value(wind, "angleApparent"))
    if None in (apparent_kn, apparent_deg, boat_kn, heading_deg):
        return {"wind_true_kn": published_kn, "wind_true_deg": published_deg,
                "wind_true_derived": False}

    import math
    # Apparent angle is relative to the bow. Resolve it into along/across components,
    # remove the boat's own speed from the along component, and recombine.
    angle = math.radians(apparent_deg)
    along = apparent_kn * math.cos(angle) - boat_kn
    across = apparent_kn * math.sin(angle)
    speed = math.hypot(along, across)
    if speed < 0.05:                       # dead calm: an angle here would be noise
        return {"wind_true_kn": 0.0, "wind_true_deg": None, "wind_true_derived": True}
    # No 180° flip here, and it is worth saying why: Signal K's wind angles are the
    # direction the wind is coming FROM, and so are the components above. Adding half a
    # turn "to get the direction it blows towards" was in the first draft of this function
    # and put every derived wind direction exactly backwards — 20 kn on the bow reported as
    # a following breeze. tests/test_wind.py holds four hand-worked cases against it.
    relative = math.degrees(math.atan2(across, along))
    return {"wind_true_kn": round(speed, 1),
            "wind_true_deg": round((heading_deg + relative) % 360),
            "wind_true_derived": True}


def _tank_level(vessel: dict, kind: str):
    """First `currentLevel` under one tank kind, as a 0..1 ratio. None if none exist."""
    tanks = vessel.get("tanks", {})
    if not isinstance(tanks, dict):
        return None
    for name, node in (tanks.get(kind) or {}).items():
        del name
        level = _value(node, "currentLevel") if isinstance(node, dict) else None
        if level is not None:
            return level
    return None


def _below_keel(vessel: dict, boat: Profile | None):
    """Depth below the keel: the sounder's reading less how far it sits above the keel."""
    published = _value(vessel, "environment", "depth", "belowKeel")
    if published is not None:
        return round(published, 1)
    from .profile import load as load_profile
    boat = boat or load_profile()
    offset = getattr(boat.vessel, "transducer_to_keel_m", None)
    below = _value(vessel, "environment", "depth", "belowTransducer")
    if offset in (None, 0) or below is None:
        return None
    return round(below - float(offset), 1)


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
