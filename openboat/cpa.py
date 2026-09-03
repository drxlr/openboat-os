"""Closest point of approach — the core arithmetic of collision-avoidance geometry.

Two vessels, each holding course and speed, trace straight lines. Their separation is
therefore a parabola in time with exactly one minimum, and that minimum is the whole
answer: **how close does this ship get, and how long have I got.** Everything else here
is presentation.

The maths is relative-velocity, done in a flat tangent plane centred on own ship with
axes in nautical miles (x east, y north). Over the few miles that AIS is useful for near a
coast, the error from ignoring the curvature of the earth is centimetres — far below the
error in the AIS position itself.

Two traps this module is deliberately built to avoid, because both produce a number that
looks like a safe answer and is not:

1. **A receding target has a mathematical CPA in the past, and it is often zero.**
   A ship that passed 200 m ahead of you ninety seconds ago still solves to
   CPA = 0.0 nm, TCPA = −1.5 min. Reporting that 0.0 as "closest approach" is a
   false alarm at best and a trained-to-ignore-it alarm at worst. `Encounter.cpa_nm`
   is therefore the closest the target comes **from now on**; the signed
   `Encounter.tcpa_min` is kept so the past case stays visible.
2. **Zero relative velocity has no CPA at all.** Two boats on the same course at the
   same speed never converge and never diverge; the range now is the range forever.
   Dividing by |v|² there gives a ZeroDivisionError on a good day and nonsense on a
   bad one. It is handled as its own case, and `tcpa_min` is `None`.

Nothing in here decides who gives way.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

NM_PER_DEGREE = 60.0     # one minute of latitude, by definition

# Below this a target is treated as not making way: anchored, moored, drifting. AIS
# reports noisy fractions of a knot for a ship swinging to its anchor — a crowded
# anchorage is full of them — and feeding that noise into a course calculation invents a
# heading the ship does not have.
STATIONARY_KN = 0.5

# Below this relative speed the encounter is "parallel": the geometry is not converging
# fast enough for a TCPA to mean anything. 0.2 kn is ~6 m per minute.
PARALLEL_KN = 0.2

# COLREGs Rule 13 draws the overtaking sector at more than 22.5° abaft the beam, i.e.
# outside the arc of the sidelights. That is a legal definition and it is used here only
# to *name* the geometry, never to decide an obligation.
ABAFT_BEAM = 112.5

# Rule 14 says "reciprocal or nearly reciprocal courses". No number is given anywhere in
# the rules; ±15° is the common working figure and it is a label, not a threshold for action.
HEAD_ON_DEG = 15.0


@dataclass
class Vessel:
    """A vessel reduced to what CPA needs: where it is and where it is going.

    `cog_deg` is course over ground, true, 0–360 clockwise from north. `sog_kn` is speed
    over ground in knots. Both are optional because AIS routinely delivers neither — a
    Class B target that has just come into range, or a ship at anchor whose course field
    is meaningless. Missing data produces an honest "unknown", never a guess.
    """

    lat: float
    lon: float
    cog_deg: float | None = None
    sog_kn: float | None = None
    name: str = ""
    mmsi: str = ""
    ship_type: str = ""

    @property
    def under_way(self) -> bool:
        return self.sog_kn is not None and self.sog_kn >= STATIONARY_KN


@dataclass
class Limits:
    """What counts as too close, for this boat.

    A small boat with no radar and no AIS transceiver is invisible to the ship it is
    watching, so every margin here has to be one it can hold on its own, unassisted.
    Commercial practice offshore is 1 nm / 20 min; close inshore, with an anchorage and a
    fairway inside a couple of miles of the breakwater, that alarms on everything. Half a
    mile is roughly 90 seconds of a container ship's advance, which is the right order of
    magnitude for "I need to have already decided".
    """

    cpa_nm: float = 0.5
    tcpa_min: float = 20.0
    watch_factor: float = 2.0     # the outer ring: cpa_nm × this, tcpa_min × this


@dataclass
class Encounter:
    """One target, fully assessed against own ship."""

    target: Vessel
    range_nm: float
    bearing_deg: float            # true bearing from own ship to the target
    rel_bearing_deg: float        # ...relative to own course; 0 dead ahead, 90 starboard
    range_rate_kn: float          # negative = closing
    cpa_nm: float | None          # closest approach FROM NOW ON. None = cannot be computed
    tcpa_min: float | None        # signed minutes to the mathematical minimum; None = parallel
    closing: bool
    aspect: str
    level: str                    # danger | watch | unknown | clear

    @property
    def sort_key(self) -> tuple:
        """Most urgent first: level, then time in hand, then plain distance."""
        rank = {"danger": 0, "watch": 1, "unknown": 2, "clear": 3}[self.level]
        soonest = self.tcpa_min if (self.tcpa_min is not None and self.tcpa_min >= 0) else math.inf
        return (rank, soonest, self.range_nm)


def to_local(origin_lat: float, origin_lon: float, lat: float, lon: float) -> tuple[float, float]:
    """Lat/lon to nautical miles east and north of an origin, on the tangent plane.

    East is scaled by the cosine of the *mean* latitude of the two points rather than the
    origin's: over a few miles the difference is negligible, but it makes the transform
    symmetric, so bearing(A→B) and bearing(B→A) come out exactly 180° apart.
    """
    dlon = (lon - origin_lon + 180.0) % 360.0 - 180.0     # short way round the world
    mean_lat = math.radians((origin_lat + lat) / 2.0)
    return dlon * NM_PER_DEGREE * math.cos(mean_lat), (lat - origin_lat) * NM_PER_DEGREE


def velocity(cog_deg: float, sog_kn: float) -> tuple[float, float]:
    """Course and speed to a velocity vector in knots east/north.

    Compass, not maths: 0° is north and angles run clockwise, so it is sin for east and
    cos for north — the transpose of the usual convention, and the classic sign bug here.
    """
    course = math.radians(cog_deg)
    return sog_kn * math.sin(course), sog_kn * math.cos(course)


def closest_approach(rx: float, ry: float, vx: float, vy: float) -> tuple[float, float | None]:
    """The unconstrained minimum of |r + v·t|, as (distance, t in hours).

    Separation squared is f(t) = |r|² + 2t(r·v) + t²|v|², a parabola opening upwards.
    f'(t) = 0 at t = −(r·v)/|v|², which is the CPA — regardless of sign. A negative t
    means the two tracks were closest before now; the caller decides what to do with that.

    Returns t = None when |v| is zero, where the parabola degenerates to a horizontal
    line and there is no minimum to find: the range never changes.
    """
    v_squared = vx * vx + vy * vy
    range_now = math.hypot(rx, ry)
    if v_squared < 1e-12:
        return range_now, None

    tcpa = -(rx * vx + ry * vy) / v_squared
    return math.hypot(rx + vx * tcpa, ry + vy * tcpa), tcpa


def _relative(angle: float, reference: float) -> float:
    return (angle - reference) % 360.0


def classify(own: Vessel, target: Vessel, bearing_deg: float,
             rel_speed_kn: float, closing: bool) -> str:
    """Name the geometry. This is description, not a give-way determination.

    The order of the tests is the order of COLREGs itself: overtaking (Rule 13) overrides
    the crossing rule, head-on (Rule 14) is a special case of meeting, and everything else
    left over is crossing. Naming it in that order at least means the label a human reads
    lines up with the rule they will be thinking about.
    """
    if target.sog_kn is None or own.cog_deg is None:
        return "unknown"
    if not target.under_way:
        return "stationary"      # anchored: its reported course means nothing, and is not used
    if target.cog_deg is None:
        return "unknown"
    if rel_speed_kn < PARALLEL_KN:
        return "parallel"

    rel_bearing = _relative(bearing_deg, own.cog_deg)                    # target, from us
    us_from_them = _relative(bearing_deg + 180.0, target.cog_deg)        # us, from them
    course_delta = _relative(target.cog_deg, own.cog_deg)

    if closing and ABAFT_BEAM < us_from_them < 360.0 - ABAFT_BEAM:
        return "overtaking"                       # we are coming up from behind them
    if closing and ABAFT_BEAM < rel_bearing < 360.0 - ABAFT_BEAM:
        return "overtaken"                        # they are coming up from behind us
    if (closing and abs(course_delta - 180.0) <= HEAD_ON_DEG
            and (rel_bearing <= HEAD_ON_DEG or rel_bearing >= 360.0 - HEAD_ON_DEG)):
        return "head-on"
    if min(course_delta, 360.0 - course_delta) <= HEAD_ON_DEG:
        return "same course"                      # running together, neither gaining
    return "crossing from starboard" if rel_bearing < 180.0 else "crossing from port"


def _level(range_nm: float, cpa_nm: float | None, tcpa_min: float | None,
           closing: bool, limits: Limits) -> str:
    if cpa_nm is None:
        return "unknown"

    outer_cpa = limits.cpa_nm * limits.watch_factor
    outer_tcpa = limits.tcpa_min * limits.watch_factor

    if closing and range_nm <= limits.cpa_nm:
        return "danger"                # already inside the guard ring and still coming
    if tcpa_min is None:
        # Parallel: it is not converging, so it is not a danger — but a ship holding
        # station a quarter of a mile away is still something to keep an eye on.
        return "watch" if range_nm <= outer_cpa else "clear"
    if cpa_nm <= limits.cpa_nm and 0.0 <= tcpa_min <= limits.tcpa_min:
        return "danger"
    if cpa_nm <= outer_cpa and 0.0 <= tcpa_min <= outer_tcpa:
        return "watch"
    return "clear"


def evaluate(own: Vessel, target: Vessel, limits: Limits | None = None) -> Encounter:
    """Own ship against one target."""
    limits = limits or Limits()

    rx, ry = to_local(own.lat, own.lon, target.lat, target.lon)
    range_nm = math.hypot(rx, ry)
    bearing = math.degrees(math.atan2(rx, ry)) % 360.0

    own_v = velocity(own.cog_deg, own.sog_kn) if own.cog_deg is not None and own.sog_kn else (0.0, 0.0)

    if target.sog_kn is None or (target.under_way and target.cog_deg is None):
        # No usable motion. Report position and stop — do not assume it is stopped, and
        # do not assume it is coming at us. Both assumptions have killed people.
        return Encounter(target, range_nm, bearing, _relative(bearing, own.cog_deg or 0.0),
                         0.0, None, None, False, "unknown", "unknown")

    target_v = velocity(target.cog_deg, target.sog_kn) if target.under_way else (0.0, 0.0)

    vx, vy = target_v[0] - own_v[0], target_v[1] - own_v[1]
    rel_speed = math.hypot(vx, vy)

    math_cpa, tcpa_h = closest_approach(rx, ry, vx, vy)
    range_rate = (rx * vx + ry * vy) / range_nm if range_nm > 1e-9 else -rel_speed
    closing = range_rate < 0.0

    # The trap from the module docstring: once the minimum is behind us, the closest this
    # target will come from here on is where it is right now.
    tcpa_min = None if tcpa_h is None else tcpa_h * 60.0
    cpa_nm = math_cpa if (tcpa_h is not None and tcpa_h > 0.0) else range_nm

    aspect = classify(own, target, bearing, rel_speed, closing)
    return Encounter(
        target=target,
        range_nm=range_nm,
        bearing_deg=bearing,
        rel_bearing_deg=_relative(bearing, own.cog_deg or 0.0),
        range_rate_kn=range_rate,
        cpa_nm=cpa_nm,
        tcpa_min=tcpa_min,
        closing=closing,
        aspect=aspect,
        level=_level(range_nm, cpa_nm, tcpa_min, closing, limits),
    )


def assess(own: Vessel, targets: list[Vessel], limits: Limits | None = None) -> list[Encounter]:
    """Every target, most urgent first."""
    limits = limits or Limits()
    return sorted((evaluate(own, t, limits) for t in targets), key=lambda e: e.sort_key)


def advance(vessel: Vessel, seconds: float) -> Vessel:
    """Move a vessel along its own course and speed. Straight lines, no set and drift.

    Used to draw or report where a vessel will be at some point in the future — most
    usefully at its own TCPA, so a plot or a snapshot can show the encounter rather than
    just describe it. A vessel that is not under way, or has no course, does not move: dead
    reckoning a stopped or courseless target would be a guess dressed up as a prediction.
    """
    if not vessel.under_way or vessel.cog_deg is None or seconds == 0:
        return vessel

    distance_nm = vessel.sog_kn * seconds / 3600.0
    north = distance_nm * math.cos(math.radians(vessel.cog_deg))
    east = distance_nm * math.sin(math.radians(vessel.cog_deg))

    lat = vessel.lat + north / 60.0
    mean_lat = math.radians((vessel.lat + lat) / 2.0)
    lon = vessel.lon + east / (60.0 * math.cos(mean_lat))

    return Vessel(lat, lon, vessel.cog_deg, vessel.sog_kn,
                  vessel.name, vessel.mmsi, vessel.ship_type)
