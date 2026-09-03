"""The boat profile — every fact about a vessel, in one file, sourced or blank.

This module is the reason OpenBoat OS is a framework and not one person's boat program.
Everything that used to be a constant in the code — where the boat lives, how fast it goes,
what the skipper considers a nice day, which Signal K path carries the coolant temperature —
is a field here, loaded from a TOML file the owner edits by hand.

Two rules govern this file, and they are not style preferences:

**A number is either sourced or absent.** Every field may carry a `*_source` sibling saying
where it came from — a builder's plate, a registration document, a measurement. A field with
no source is not a fact; it is a guess, and `Profile.unsourced()` will list it. Nothing in
OpenBoat invents a length, a fuel burn or an engine power because a model name implied one.
A confidently wrong figure is worse than a blank.

**A name means one thing.** `berth` is where the boat physically sits. `forecast_point` is
what a forecast is asked about. They are deliberately different fields with no alias that
means both, because on a real boat they are different places and conflating them produced a
genuine bug: a coastal forecast grid cell containing a marina is land-influenced, so it
under-reads the wind the boat will meet and over-reads its gusts. See `docs/FORECAST.md`.

    from openboat.profile import load
    boat = load()                      # OPENBOAT_PROFILE, or the demo boat
    boat.forecast_point                # (lat, lon)
    boat.limits.max_wind_kn            # the skipper's, not the boat's
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["Profile", "Limits", "Vessel", "load", "DEMO_PROFILE", "ProfileError"]

DEMO_PROFILE = Path(__file__).resolve().parent.parent / "profiles" / "demo-boat.toml"

#: Signal K paths OpenBoat reads by default. Override any of them per boat, because
#: instance names differ: one boat's engine is `propulsion.engine_1`, another's is
#: `propulsion.port`. A path that is absent on your boat simply reads as "no sender".
DEFAULT_PATHS: dict[str, str] = {
    "position": "navigation.position",
    "speed_over_ground": "navigation.speedOverGround",
    "course_over_ground": "navigation.courseOverGroundTrue",
    "heading": "navigation.headingTrue",
    "depth": "environment.depth.belowTransducer",
    "wind_speed": "environment.wind.speedApparent",
    "wind_angle": "environment.wind.angleApparent",
    "water_temperature": "environment.water.temperature",
    "engine_revolutions": "propulsion.engine_1.revolutions",
    "engine_temperature": "propulsion.engine_1.temperature",
    "engine_oil_pressure": "propulsion.engine_1.oilPressure",
    "engine_hours": "propulsion.engine_1.runTime",
    "battery_voltage": "electrical.batteries.house.voltage",
    "fuel_level": "tanks.fuel.main.currentLevel",
}


class ProfileError(Exception):
    """The profile could not be read, or says something impossible."""


@dataclass(frozen=True)
class Limits:
    """What this skipper calls a day worth going out on.

    Comfort limits, not survival limits, and that distinction is the whole point. The cost
    of a missed nice day is nothing; the cost of a frightened passenger is the rest of the
    season. Set them where *you* would turn back, not where the boat would.
    """

    max_wind_kn: float = 15.0
    max_gust_kn: float = 20.0
    max_wave_m: float = 0.8
    max_rain_mm: float = 0.2
    daylight_from_h: int = 7
    daylight_to_h: int = 19

    @property
    def daylight(self) -> tuple[int, int]:
        return (self.daylight_from_h, self.daylight_to_h)

    def as_dict(self) -> dict:
        return {"max_wind_kn": self.max_wind_kn, "max_gust_kn": self.max_gust_kn,
                "max_wave_m": self.max_wave_m, "max_rain_mm": self.max_rain_mm,
                "daylight": self.daylight}


@dataclass(frozen=True)
class Vessel:
    """The boat's own facts. Every one of them optional, because most boats do not know
    all of them and pretending otherwise is how a spec sheet becomes a lie.

    `name` is what appears on the dashboard. `length_m` and friends are used for arithmetic
    that OpenBoat refuses to do when they are missing — an anchor-scope suggestion needs a
    bow height, and without one it says so rather than guessing.
    """

    name: str = "Demo Boat"
    kind: str = ""                       # "sailing yacht", "motor cruiser", ...
    length_m: float | None = None
    beam_m: float | None = None
    draft_m: float | None = None
    air_draft_m: float | None = None
    displacement_kg: float | None = None
    engine_kw: float | None = None
    engine_note: str = ""
    fuel_capacity_l: float | None = None
    cruise_speed_kn: float | None = None
    cruise_burn_lph: float | None = None
    mmsi: str = ""                       # left blank by default, and blank is correct
    callsign: str = ""


@dataclass
class Profile:
    """One boat, loaded from one file."""

    vessel: Vessel = field(default_factory=Vessel)
    limits: Limits = field(default_factory=Limits)

    #: Where the boat physically is when it is not going anywhere.
    berth: tuple[float, float] = (0.0, 0.0)
    berth_name: str = ""

    #: What a forecast is asked about. Offshore of the berth, deliberately — see the
    #: module docstring and docs/FORECAST.md.
    forecast_point: tuple[float, float] = (0.0, 0.0)
    forecast_point_name: str = ""

    timezone: str = "UTC"
    signalk_url: str = "http://localhost:3000"
    paths: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_PATHS))

    #: Alarm bands per reading, as [[low, high, severity], ...] in the reading's own unit.
    #: Empty by default: OpenBoat does not know what is hot for *your* engine, and an
    #: invented band is a green light through a real overheat.
    bands: dict[str, list] = field(default_factory=dict)

    #: Service intervals, per item, as {"impeller": {"hours": 100, "months": 12}}. Empty by
    #: default: the intervals belong to your engine's manual, and one invented here would be
    #: confidently wrong for most engines and would still get followed. The fresh-water
    #: flush is the exception the code knows about, because it is a property of raw-water
    #: cooling rather than of a particular engine — see `openboat/maintenance.py`.
    maintenance: dict[str, dict] = field(default_factory=dict)

    #: Ring 2, the only part of OpenBoat that writes to the boat. **Off unless you turn it
    #: on**, and it stays off through an upgrade because the default is here, not in a file
    #: somebody might overwrite. See `openboat/control/` for what turning it on means.
    control: dict = field(default_factory=lambda: {"enabled": False, "allow": []})

    sources: dict[str, str] = field(default_factory=dict)
    path: Path | None = None

    # -- questions the rest of the code asks -------------------------------------------

    def source_of(self, field_name: str) -> str | None:
        """Where a fact came from, or None if nobody said."""
        return self.sources.get(field_name)

    def unsourced(self) -> list[str]:
        """Measurements that carry a value but no source.

        These are guesses in disguise, and any report that leans on one should say so. Only
        numbers are counted: a boat's name and description are not measurements and nobody
        needs a citation for them.
        """
        return sorted(
            name for name, value in vars(self.vessel).items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
            and value not in (None, 0) and not self.sources.get(name)
        )

    def require(self, *field_names: str) -> None:
        """Refuse to compute rather than compute on a blank.

        Call this at the top of anything that needs a real measurement. The error names the
        field and the file, so the fix is obvious and belongs in the profile, not the code.
        """
        missing = [n for n in field_names if getattr(self.vessel, n, None) in (None, "", 0)]
        if missing:
            where = self.path or "the profile"
            raise ProfileError(
                f"{', '.join(missing)} not set in {where}. "
                f"OpenBoat will not guess a boat's measurements — fill it in or accept "
                f"that this calculation cannot be made."
            )

    def as_dict(self) -> dict:
        return {
            "vessel": vars(self.vessel),
            "limits": self.limits.as_dict(),
            "berth": {"lat": self.berth[0], "lon": self.berth[1], "name": self.berth_name},
            "forecast_point": {"lat": self.forecast_point[0], "lon": self.forecast_point[1],
                               "name": self.forecast_point_name},
            "timezone": self.timezone,
            "unsourced": self.unsourced(),
        }


def _point(table: dict, key: str) -> tuple[tuple[float, float], str]:
    node = table.get(key) or {}
    try:
        return (float(node["lat"]), float(node["lon"])), str(node.get("name", ""))
    except (KeyError, TypeError, ValueError) as exc:
        raise ProfileError(f"[{key}] needs a numeric lat and lon: {exc}") from exc


def load(path: str | os.PathLike | None = None) -> Profile:
    """Load a profile.

    Order: the argument, then `$OPENBOAT_PROFILE`, then `./boat.toml`, then the demo boat
    that ships with the project. The demo boat is a real, publicly known harbour and an
    invented vessel, so a fresh clone runs and shows something sensible before anyone has
    typed a word of configuration — and so that nothing in this repository describes a real
    person's boat.
    """
    candidates = [path, os.environ.get("OPENBOAT_PROFILE"), Path("boat.toml"), DEMO_PROFILE]
    chosen = next((Path(c) for c in candidates if c and Path(c).is_file()), None)
    if chosen is None:
        raise ProfileError(
            "no profile found. Copy profiles/demo-boat.toml to boat.toml and edit it, "
            "or set OPENBOAT_PROFILE to point at yours."
        )

    try:
        with open(chosen, "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ProfileError(f"{chosen}: {exc}") from exc

    vessel_table = dict(data.get("vessel") or {})
    sources = {k[:-7]: str(v) for k, v in vessel_table.items() if k.endswith("_source")}
    vessel_fields = {k: v for k, v in vessel_table.items()
                     if not k.endswith("_source") and k in Vessel.__dataclass_fields__}

    limits_table = dict(data.get("limits") or {})
    limits_fields = {k: v for k, v in limits_table.items()
                     if k in Limits.__dataclass_fields__}

    berth, berth_name = _point(data, "berth")
    point, point_name = _point(data, "forecast_point")

    return Profile(
        vessel=Vessel(**vessel_fields),
        limits=Limits(**limits_fields),
        berth=berth,
        berth_name=berth_name,
        forecast_point=point,
        forecast_point_name=point_name,
        timezone=str(data.get("timezone", "UTC")),
        signalk_url=os.environ.get("SIGNALK_URL") or str(data.get("signalk_url",
                                                                  "http://localhost:3000")),
        paths={**DEFAULT_PATHS, **(data.get("paths") or {})},
        bands=dict(data.get("bands") or {}),
        maintenance={k: dict(v) for k, v in (data.get("maintenance") or {}).items()
                     if isinstance(v, dict)},
        control={"enabled": False, "allow": [], **(data.get("control") or {})},
        sources=sources,
        path=chosen,
    )
