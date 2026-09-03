"""Route legs, ETAs, and the weather each leg will actually meet.

The point of this file: a forecast for the marina is not a forecast for the passage.
A leg that leaves at 08:00 in a flat calm can arrive at 13:00 in a 20 kn sea breeze,
and that is the number that decides whether the trip happens.

⚠️ This does NOT check for land, depth, or restricted areas. It is a distance/time/weather
calculator over waypoints you supply, not a chart engine. Cross-check every route against
a real chart. See docs/DISCLAIMER.md.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .marine import Hour, forecast
from .profile import Profile, load

NM_PER_DEGREE = 60.0  # one minute of latitude, by definition


@dataclass
class Waypoint:
    name: str
    lat: float
    lon: float


@dataclass
class Leg:
    frm: Waypoint
    to: Waypoint
    distance_nm: float
    bearing_deg: float
    depart: datetime
    arrive: datetime
    weather: Hour | None = None

    @property
    def bearing_name(self) -> str:
        points = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                  "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")
        return points[int((self.bearing_deg % 360) / 22.5 + 0.5) % 16]


@dataclass
class Passage:
    legs: list[Leg] = field(default_factory=list)
    speed_kn: float = 20.0
    litres_per_hour: float = 0.0

    @property
    def distance_nm(self) -> float:
        return sum(leg.distance_nm for leg in self.legs)

    @property
    def hours(self) -> float:
        return self.distance_nm / self.speed_kn if self.speed_kn else 0.0

    @property
    def fuel_litres(self) -> float:
        return self.hours * self.litres_per_hour


def rhumb(a: Waypoint, b: Waypoint) -> tuple[float, float]:
    """Rhumb-line distance in nautical miles and initial bearing in degrees.

    Rhumb rather than great circle: over coastal distances the difference is metres, and a
    constant compass bearing is what actually gets steered. Over an ocean crossing it is the
    wrong choice, and this project is not the tool for one.
    """
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlat = lat2 - lat1
    dlon = math.radians(b.lon - a.lon)

    # Mercator projected latitude difference, with the limiting case for due east/west.
    dpsi = math.log(math.tan(math.pi / 4 + lat2 / 2) / math.tan(math.pi / 4 + lat1 / 2))
    q = dlat / dpsi if abs(dpsi) > 1e-12 else math.cos(lat1)

    # Keep the longitude difference on the short way round.
    if abs(dlon) > math.pi:
        dlon = dlon - math.copysign(2 * math.pi, dlon)

    distance_nm = math.hypot(dlat, q * dlon) * NM_PER_DEGREE * 180 / math.pi
    bearing = (math.degrees(math.atan2(dlon, dpsi)) + 360) % 360
    return distance_nm, bearing


def plan(waypoints: list[Waypoint], speed_kn: float | None = None,
         depart: datetime | None = None, litres_per_hour: float | None = None,
         with_weather: bool = True, boat: Profile | None = None) -> Passage:
    """Chain waypoints into timed legs and hang the forecast on each one.

    Speed and fuel burn fall back to the profile. Both may legitimately be unknown: a burn
    figure nobody has measured is arithmetic on a guess, so it defaults to zero and the
    fuel total comes out as zero rather than as a confident invention.
    """
    if len(waypoints) < 2:
        raise ValueError("a passage needs at least two waypoints")

    boat = boat or load()
    if speed_kn is None:
        speed_kn = boat.vessel.cruise_speed_kn or 6.0
    if litres_per_hour is None:
        litres_per_hour = boat.vessel.cruise_burn_lph or 0.0

    depart = depart or datetime.now().replace(minute=0, second=0, microsecond=0)
    passage = Passage(speed_kn=speed_kn, litres_per_hour=litres_per_hour)

    clock = depart
    for frm, to in zip(waypoints, waypoints[1:]):
        distance_nm, bearing = rhumb(frm, to)
        arrive = clock + timedelta(hours=distance_nm / speed_kn if speed_kn else 0)
        passage.legs.append(Leg(frm, to, distance_nm, bearing, clock, arrive))
        clock = arrive

    if with_weather:
        _attach_weather(passage)
    return passage


def _attach_weather(passage: Passage) -> None:
    """One forecast call per leg, at the leg's midpoint, for the hour it is under way."""
    for leg in passage.legs:
        mid_lat = (leg.frm.lat + leg.to.lat) / 2
        mid_lon = (leg.frm.lon + leg.to.lon) / 2
        midway = leg.depart + (leg.arrive - leg.depart) / 2

        try:
            hours = forecast(mid_lat, mid_lon, days=7)
        except Exception:
            continue

        if not hours:
            continue          # every forecast hour is already past: nothing to attach

        # Forecast hours are naive local time at the point. A caller may hand us an aware
        # departure; subtracting the two raises rather than returning a wrong answer, so
        # drop the tzinfo rather than pretend to convert.
        when = midway.replace(tzinfo=None) if midway.tzinfo else midway

        # Nearest forecast hour to the moment the boat is actually there.
        leg.weather = min(hours, key=lambda h: abs((h.time - when).total_seconds()))
