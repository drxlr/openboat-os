"""An anchor watch: where the anchor was dropped, how far the boat is from it, and whether
that distance is the boat swinging or the anchor walking.

Why this and not anything else in this package: everything else here answers a question
that can wait until morning. This one wakes you up. A boat that drags at 03:00 in an
onshore breeze is on the beach in twenty minutes, and the only thing between those two
states is something watching the GPS and making a noise.

The hard part is not the distance — that is trigonometry. The hard part is that a boat at
anchor *is supposed to move*. It lies to the wind on 30 m of rode, the breeze veers, and it
sweeps a 60 m arc across the anchorage without the anchor having shifted a centimetre. So a
plain "distance from where I dropped it" alarm is either set so tight it cries wolf all
night, or so wide it announces the drag once the boat is already over the shallows.

What separates the two cases is the *centre*, not the distance:

    swinging   the fixes lie on an arc of roughly constant radius; fit a circle through
               them and its centre sits on top of the anchor, where it was dropped
    dragging   the whole swing circle translates downwind; the fitted centre migrates away
               from the drop point and keeps going

So this watches the fitted centre. What that can and cannot see is written down honestly
in `Swing.why` — it is a heuristic over noisy positions, not a sensor.

    python3 -m openboat.anchor --radius 30                 # drop here, watch, alarm
    python3 -m openboat.anchor --rode 40 --depth 6 --bow-height 1.2   # radius from scope
    python3 -m openboat.anchor --anchor 50.31,-4.15 --radius 25 --record night.gpx
"""

from __future__ import annotations

import argparse
import math
import shutil
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .track import Fix, bearing_deg, distance_m, local_xy, write_gpx

# A 2 m alarm radius is a guaranteed 03:00 false alarm — consumer GPS scatter alone is
# metres. This is the slack added on top of the geometry to absorb it. Not a boat fact:
# it is about what a consumer GPS receiver does, not about any particular vessel, so it
# stays a plain constant rather than a profile field. See README.md.
GPS_MARGIN_M = 10.0


def swing_radius(rode_m: float, depth_m: float, bow_height_m: float, gps_offset_m: float,
                 margin_m: float = GPS_MARGIN_M) -> float:
    """The circle the boat can legitimately reach, from the scope actually veered.

    Horizontal reach is the rode with the vertical part taken out — Pythagoras on
    (depth + height of the bow roller above water). That gives where the *bow* can get to;
    the GPS is somewhere aft of it, so `gps_offset_m` is added, then the GPS margin.

    `bow_height_m` and `gps_offset_m` are required and have no built-in default on
    purpose: OpenBoat will not invent a bow roller height or an antenna offset, because on
    most boats nobody has measured either. See `swing_radius_for`, which supplies a
    conservative `gps_offset_m` from the profile when the boat's length is known.

    This is deliberately the worst case: catenary means the rode is never a straight line
    and the real reach is shorter. An anchor watch that alarms early is a bad anchor watch.
    """
    vertical = depth_m + bow_height_m
    horizontal = math.sqrt(max(rode_m ** 2 - vertical ** 2, 0.0))
    return horizontal + gps_offset_m + margin_m


def swing_radius_for(profile, rode_m: float, depth_m: float, bow_height_m: float,
                     margin_m: float = GPS_MARGIN_M) -> float:
    """`swing_radius`, with the GPS offset defaulted from the boat's own length.

    Nobody measures where their GPS antenna sits relative to the bow roller, so the
    conservative bound is the whole boat length — the same reasoning the profile itself
    uses for `require()`. `bow_height_m` still has to be passed in: it is not a profile
    field, because it is even less commonly known than a boat's overall length and a wrong
    guess makes the alarm circle too small, not too large.
    """
    profile.require("length_m")
    return swing_radius(rode_m, depth_m, bow_height_m, profile.vessel.length_m, margin_m)


# --- circle fitting -------------------------------------------------------------------

def _solve3(matrix: list[list[float]], rhs: list[float]) -> list[float] | None:
    """Gaussian elimination with partial pivoting on a 3x3. Returns None if singular."""
    a = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            return None
        a[col], a[pivot] = a[pivot], a[col]
        for row in range(col + 1, 3):
            factor = a[row][col] / a[col][col]
            for k in range(col, 4):
                a[row][k] -= factor * a[col][k]
    out = [0.0, 0.0, 0.0]
    for row in reversed(range(3)):
        total = a[row][3] - sum(a[row][k] * out[k] for k in range(row + 1, 3))
        out[row] = total / a[row][row]
    return out


def fit_circle(points: list[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    """Kåsa algebraic circle fit: centre, radius and RMS residual, all in metres.

    x² + y² = 2·cx·x + 2·cy·y + c  is linear in (cx, cy, c), so the least-squares fit is
    one 3x3 solve with no iteration and no numpy. It is biased when the points cover only a
    short arc — which is exactly the case this file has to be careful about — so the caller
    checks the arc before believing the answer. Returns None when the system is singular
    (all points collinear, or barely moved at all).
    """
    if len(points) < 3:
        return None

    n = float(len(points))
    sx = sum(p[0] for p in points)
    sy = sum(p[1] for p in points)
    sxx = sum(p[0] * p[0] for p in points)
    syy = sum(p[1] * p[1] for p in points)
    sxy = sum(p[0] * p[1] for p in points)
    sxz = sum(p[0] * (p[0] ** 2 + p[1] ** 2) for p in points)
    syz = sum(p[1] * (p[0] ** 2 + p[1] ** 2) for p in points)
    sz = sum(p[0] ** 2 + p[1] ** 2 for p in points)

    solution = _solve3([[2 * sxx, 2 * sxy, sx],
                        [2 * sxy, 2 * syy, sy],
                        [2 * sx, 2 * sy, n]],
                       [sxz, syz, sz])
    if solution is None:
        return None

    cx, cy, c = solution
    squared = c + cx * cx + cy * cy
    if squared <= 0:
        return None
    radius = math.sqrt(squared)

    residuals = [math.hypot(x - cx, y - cy) - radius for x, y in points]
    rms = math.sqrt(sum(r * r for r in residuals) / len(residuals))
    return cx, cy, radius, rms


def arc_span_deg(bearings: list[float]) -> float:
    """The angular width of a set of bearings — the widest gap, subtracted from 360.

    Bearings wrap, so a boat lying between 350° and 010° has swung 20°, not 340°. Sorting
    and finding the largest empty sector gets that right without any circular statistics.
    """
    if len(bearings) < 2:
        return 0.0
    ordered = sorted(b % 360 for b in bearings)
    gaps = [b - a for a, b in zip(ordered, ordered[1:])]
    gaps.append(ordered[0] + 360 - ordered[-1])
    return 360.0 - max(gaps)


def _slope(times: list[float], values: list[float]) -> float | None:
    """Least-squares slope of values against time, per second."""
    n = len(times)
    if n < 3:
        return None
    mean_t = sum(times) / n
    mean_v = sum(values) / n
    denominator = sum((t - mean_t) ** 2 for t in times)
    if denominator < 1e-9:
        return None
    return sum((t - mean_t) * (v - mean_v) for t, v in zip(times, values)) / denominator


# --- the assessment -------------------------------------------------------------------

@dataclass
class Swing:
    """What the recent fixes say about how the boat is lying. `why` is the honest part."""

    fixes: int
    span_s: float
    radius_min_m: float
    radius_max_m: float
    radius_mean_m: float
    arc_deg: float
    mean_bearing_deg: float
    centre_offset_m: float | None       # None when the arc is too narrow to trust a fit
    centre_bearing_deg: float | None
    fit_radius_m: float | None
    fit_rms_m: float | None
    creep_m_per_h: float | None
    verdict: str                         # waiting | swinging | possible drag | dragging
    why: str

    def __str__(self) -> str:
        centre = (f"centre {self.centre_offset_m:.0f} m off"
                  if self.centre_offset_m is not None else "centre unfitted")
        return (f"{self.verdict.upper()}: {self.why} "
                f"[{self.fixes} fixes / {self.span_s / 60:.0f} min, "
                f"range {self.radius_min_m:.0f}-{self.radius_max_m:.0f} m, "
                f"arc {self.arc_deg:.0f}°, {centre}]")


@dataclass
class Reading:
    """One fix, measured against the anchor."""

    fix: Fix
    distance_m: float
    bearing_deg: float
    inside: bool
    state: str                           # ok | alarm
    swing: Swing | None = None
    events: list[tuple[bool, str]] = field(default_factory=list)

    def __str__(self) -> str:
        mark = {"ok": "  ", "alarm": "!!"}.get(self.state, "  ")
        return (f"{self.fix.time:%H:%M:%S} {mark} {self.distance_m:6.1f} m  "
                f"{self.bearing_deg:03.0f}°  {self.state}")


class AnchorWatch:
    """Set the anchor, feed it fixes, read the alarms.

    Deliberately not a thread and not a daemon: it is a pure state machine over fixes, so
    the same object serves the live watch, a GPX replay and a synthetic series. Anything
    that cannot be replayed cannot be trusted at 03:00.
    """

    def __init__(self, lat: float, lon: float, radius_m: float,
                 window_s: float = 1800.0,
                 breaches_to_alarm: int = 3,
                 clear_after: int = 5,
                 centre_threshold_m: float = 15.0,
                 creep_threshold_m_per_h: float = 25.0,
                 min_arc_for_fit_deg: float = 90.0,
                 drag_confirm_s: float = 180.0,
                 min_fixes: int = 15,
                 min_span_s: float = 300.0,
                 notify: bool = True,
                 vessel_name: str = "Boat"):
        self.lat, self.lon = lat, lon
        self.radius_m = radius_m
        self.window_s = window_s
        self.breaches_to_alarm = breaches_to_alarm
        self.clear_after = clear_after
        self.centre_threshold_m = centre_threshold_m
        self.creep_threshold_m_per_h = creep_threshold_m_per_h
        self.min_arc_for_fit_deg = min_arc_for_fit_deg
        self.drag_confirm_s = drag_confirm_s
        self.min_fixes = min_fixes
        self.min_span_s = min_span_s
        self.notify_enabled = notify
        self.vessel_name = vessel_name

        self.fixes: deque[Fix] = deque()
        self._breaches = 0
        self._inside_run = 0
        self._range_alarm = False
        self._drag_alarm = False
        self._drag_since: float | None = None
        self.max_distance_m = 0.0

    @property
    def state(self) -> str:
        """Two independent alarms, one lamp: the range was breached, or the centre walked."""
        return "alarm" if (self._range_alarm or self._drag_alarm) else "ok"

    # -- feeding ------------------------------------------------------------------------

    def update(self, fix: Fix) -> Reading:
        self.fixes.append(fix)
        cutoff = fix.time.timestamp() - self.window_s
        while self.fixes and self.fixes[0].time.timestamp() < cutoff:
            self.fixes.popleft()

        distance = distance_m(self.lat, self.lon, fix.lat, fix.lon)
        bearing = bearing_deg(self.lat, self.lon, fix.lat, fix.lon)
        self.max_distance_m = max(self.max_distance_m, distance)
        inside = distance <= self.radius_m

        # Debounce both ways. One fix outside the circle is GPS scatter; three in a row is
        # the boat. Coming back in has to be convincing too, or the alarm chatters on the
        # boundary and gets switched off by a tired human, which is the real failure mode.
        events: list[tuple[bool, str]] = []
        if inside:
            self._inside_run += 1
            self._breaches = 0
            if self._range_alarm and self._inside_run >= self.clear_after:
                self._range_alarm = False
                events.append((False, f"back inside the {self.radius_m:.0f} m circle"))
        else:
            self._inside_run = 0
            self._breaches += 1
            if self._breaches >= self.breaches_to_alarm and not self._range_alarm:
                self._range_alarm = True
                events.append((True, f"OUTSIDE the {self.radius_m:.0f} m circle "
                                     f"— {distance:.0f} m, bearing {bearing:03.0f}°"))

        swing = self.swing()
        # A drag verdict has to hold before it is believed. A circle fit over an arc that
        # has only just widened past the threshold is badly conditioned and will call drag
        # for a fix or two — seen, and the reason this debounce exists. Nothing is lost by
        # waiting: an anchor walking fast enough to matter is still walking three minutes
        # later, and the range alarm is underneath as the backstop.
        if swing.verdict == "dragging":
            if self._drag_since is None:
                self._drag_since = fix.time.timestamp()
            held = fix.time.timestamp() - self._drag_since
            if held >= self.drag_confirm_s:
                if not self._drag_alarm:
                    self._drag_alarm = True
                    events.append((True, f"DRAGGING — {swing.why}"))
            else:
                swing.verdict = "possible drag"
                swing.why += (f" — held {held:.0f}s of the "
                              f"{self.drag_confirm_s:.0f}s needed to call it")
        else:
            self._drag_since = None
            # Only positive evidence clears a drag alarm. Losing the fit is not evidence:
            # a boat that has dragged 40 m from its drop point now bears almost constantly
            # from that point, so the arc narrows and the fit disappears — the alarm was
            # cancelling itself precisely because the drag had gone further. The centre has
            # to be seen back over the anchor before this goes quiet.
            if self._drag_alarm:
                back = (swing.centre_offset_m is not None
                        and swing.centre_offset_m <= self.centre_threshold_m)
                if back:
                    self._drag_alarm = False
                    events.append((False, f"drag alarm cleared — swing centre back within "
                                          f"{swing.centre_offset_m:.0f} m of the anchor"))

        for loud, message in events:
            self.alarm(message, fix.time, notify=loud)

        return Reading(fix, distance, bearing, inside, self.state, swing, events)

    # -- the heuristic ------------------------------------------------------------------

    def swing(self) -> Swing:
        """Read the recent fixes as a swing pattern, and judge it.

        Two independent detectors, because they fail in opposite conditions:

        1. **Centre fit.** Needs the boat to have actually swung — 90° of arc or more. Fit
           a circle through the window; if its centre has walked more than
           `centre_threshold_m` from where the anchor was dropped, the whole swing circle
           has moved and the anchor is not where it was. This is the strong signal and the
           only one that raises the drag alarm.
        2. **Radial creep.** Works when the boat is lying dead steady on one bearing, which
           is precisely when detector 1 is blind. Least-squares slope of range-from-anchor
           against time. Steady outward creep on a narrow bearing is what a slow drag in
           constant wind looks like — but so does a rode straightening out as the breeze
           builds, so it reports "possible drag" and does not wake anybody.
        """
        fixes = list(self.fixes)
        if len(fixes) < 3:
            return Swing(len(fixes), 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, None, None, None, None,
                         None, "waiting", "not enough fixes yet")

        span_s = (fixes[-1].time - fixes[0].time).total_seconds()
        ranges = [distance_m(self.lat, self.lon, f.lat, f.lon) for f in fixes]
        bearings = [bearing_deg(self.lat, self.lon, f.lat, f.lon) for f in fixes]
        points = [local_xy(f.lat, f.lon, self.lat, self.lon) for f in fixes]

        arc = arc_span_deg(bearings)
        # Vector mean, so the average of 350° and 010° is 000° and not 180°.
        mean_bearing = math.degrees(math.atan2(
            sum(math.sin(math.radians(b)) for b in bearings),
            sum(math.cos(math.radians(b)) for b in bearings))) % 360

        times = [f.time.timestamp() - fixes[0].time.timestamp() for f in fixes]
        slope = _slope(times, ranges)
        creep = slope * 3600 if slope is not None else None

        fit = fit_circle(points)
        centre_offset = centre_bearing = fit_radius = fit_rms = None
        if fit is not None:
            cx, cy, fit_radius, fit_rms = fit
            centre_offset = math.hypot(cx, cy)
            centre_bearing = (math.degrees(math.atan2(cx, cy)) + 360) % 360

        base = Swing(len(fixes), span_s, min(ranges), max(ranges), sum(ranges) / len(ranges),
                     arc, mean_bearing, None, None, None, None, creep, "waiting", "")

        if len(fixes) < self.min_fixes or span_s < self.min_span_s:
            base.why = (f"only {len(fixes)} fixes over {span_s / 60:.0f} min — "
                        f"needs {self.min_fixes} over {self.min_span_s / 60:.0f} min "
                        f"before it can tell swinging from dragging")
            return base

        if arc >= self.min_arc_for_fit_deg and fit is not None and fit_rms is not None:
            base.centre_offset_m = centre_offset
            base.centre_bearing_deg = centre_bearing
            base.fit_radius_m = fit_radius
            base.fit_rms_m = fit_rms
            # A large residual means the points are not on any one circle — the boat is
            # under way, or the wind is shifting faster than it swings. Not a drag call.
            if fit_rms > max(0.25 * fit_radius, 10.0):
                base.verdict = "swinging"
                base.why = (f"swept {arc:.0f}° but the fixes do not lie on one circle "
                            f"(residual {fit_rms:.0f} m) — no centre to judge")
                return base
            if centre_offset > self.centre_threshold_m:
                base.verdict = "dragging"
                base.why = (f"swing centre has moved {centre_offset:.0f} m "
                            f"({centre_bearing:03.0f}°) from where the anchor was dropped, "
                            f"fitted over {arc:.0f}° of arc")
            else:
                base.verdict = "swinging"
                base.why = (f"swept {arc:.0f}° at {fit_radius:.0f} m and the fitted centre "
                            f"is still within {centre_offset:.0f} m of the drop point")
            return base

        # Narrow arc: the circle fit is not to be believed, so say so rather than dress it up.
        if creep is not None and creep > self.creep_threshold_m_per_h:
            base.verdict = "possible drag"
            base.why = (f"lying on {mean_bearing:03.0f}° over only {arc:.0f}° of arc, "
                        f"but the range is growing {creep:.0f} m/h — too narrow an arc to "
                        f"fit a centre, so this could equally be the rode straightening")
        else:
            base.verdict = "swinging"
            base.why = (f"lying quietly on {mean_bearing:03.0f}°, {arc:.0f}° of arc — "
                        f"not enough swing to locate the centre, range steady")
        return base

    # -- output -------------------------------------------------------------------------

    def alarm(self, message: str, when: datetime | None = None, notify: bool = True) -> None:
        """Stdout always; a macOS notification with a sound when there is one to be had.

        Silence is the wrong failure mode for an alarm, so the print happens first and
        unconditionally. `osascript` is absent on the Pi and on Linux — degrade, do not
        crash, and do not pretend a notification was delivered. `notify=False` is for the
        events that are good news: an alarm clearing should be visible, not audible.

        The stamp is the *fix* time, not the wall clock, so a replayed night reads in the
        order it happened rather than in the order it was replayed.
        """
        stamp = (when or datetime.now(timezone.utc)).strftime("%H:%M:%S")
        print(f"\a{stamp}  *** {message}", flush=True)
        if not notify or not self.notify_enabled or not shutil.which("osascript"):
            return
        safe = message.replace('"', "'")
        script = (f'display notification "{safe}" with title "{self.vessel_name} — '
                  f'anchor watch" sound name "Sosumi"')
        try:
            subprocess.run(["osascript", "-e", script], check=False,
                           capture_output=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass


# --- live watch -----------------------------------------------------------------------

def watch(anchor: AnchorWatch, interval_s: float = 5.0, duration_s: float | None = None,
          record_path: str | Path | None = None, verbose: bool = True) -> list[Fix]:
    """Poll Signal K until interrupted. Offline is survivable; a stale fix is not."""
    from . import boat  # noqa: PLC0415 — imported lazily, see track.record()

    recorded: list[Fix] = []
    deadline = None if duration_s is None else time.monotonic() + duration_s
    warned_offline = False

    while deadline is None or time.monotonic() < deadline:
        try:
            state = boat.state()
            warned_offline = False
        except boat.Offline as exc:
            # Losing the boat's server while at anchor is itself worth saying out loud —
            # once. An anchor watch that has stopped watching must not look like one that
            # is watching and seeing nothing wrong.
            if not warned_offline:
                warned_offline = True
                anchor.alarm(f"no position — anchor watch is blind ({exc})")
            time.sleep(interval_s)
            continue

        if state.get("lat") is None:
            if not warned_offline:
                warned_offline = True
                anchor.alarm("Signal K is up but has no position — anchor watch is blind")
            time.sleep(interval_s)
            continue

        fix = Fix(datetime.now(timezone.utc), state["lat"], state["lon"], state.get("sog_kn"))
        recorded.append(fix)
        reading = anchor.update(fix)
        if verbose:
            print(f"{reading}  {reading.swing.verdict}", flush=True)
        if record_path and len(recorded) % 10 == 0:
            write_gpx(recorded, record_path, f"{anchor.vessel_name} at anchor")
        time.sleep(interval_s)

    if record_path:
        write_gpx(recorded, record_path, f"{anchor.vessel_name} at anchor")
    return recorded


def main() -> None:
    from .profile import load as load_profile

    parser = argparse.ArgumentParser(
        description="Anchor watch, reading position from Signal K.",
        epilog="With no --anchor, the anchor is dropped at the boat's current position.")
    parser.add_argument("--anchor", help="lat,lon of the anchor (default: where the boat is now)")
    parser.add_argument("--radius", type=float, help="alarm radius in metres")
    parser.add_argument("--rode", type=float, help="metres of rode veered — radius is computed")
    parser.add_argument("--depth", type=float, help="depth in metres (default: from Signal K)")
    parser.add_argument("--bow-height", type=float,
                        help="bow roller height above the water, m — required with --rode; "
                             "OpenBoat will not guess it")
    parser.add_argument("--gps-offset", type=float,
                        help="GPS antenna offset from the bow, m "
                             "(default: the boat's length from the profile)")
    parser.add_argument("--profile", help="path to a boat.toml (default: the usual search)")
    parser.add_argument("--interval", type=float, default=5.0, help="seconds between fixes")
    parser.add_argument("--duration", type=float, help="stop after this many seconds")
    parser.add_argument("--window", type=float, default=1800.0,
                        help="seconds of history the drag heuristic looks at")
    parser.add_argument("--record", help="also write the watch to this GPX file")
    parser.add_argument("--no-notify", action="store_true", help="stdout only, no notification")
    args = parser.parse_args()

    profile = load_profile(args.profile)

    from . import boat  # noqa: PLC0415

    try:
        state = boat.state()
    except boat.Offline as exc:
        print(f"the boat is offline: {exc}", file=sys.stderr)
        raise SystemExit(1)

    if args.anchor:
        lat, lon = (float(part) for part in args.anchor.split(","))
    elif state.get("lat") is not None:
        lat, lon = state["lat"], state["lon"]
    else:
        print("Signal K has no position — pass --anchor lat,lon", file=sys.stderr)
        raise SystemExit(1)

    if args.radius:
        radius = args.radius
    elif args.rode:
        if args.bow_height is None:
            print("no --bow-height given — OpenBoat will not guess the bow roller's height "
                  "above the water; measure it, or pass --radius directly", file=sys.stderr)
            raise SystemExit(1)
        gps_offset = args.gps_offset if args.gps_offset is not None else profile.vessel.length_m
        if gps_offset is None:
            print("no --gps-offset given and the profile has no vessel length — pass one "
                  "or the other", file=sys.stderr)
            raise SystemExit(1)
        depth = args.depth if args.depth is not None else state.get("depth_m")
        if depth is None:
            print("no depth from Signal K — pass --depth with --rode", file=sys.stderr)
            raise SystemExit(1)
        radius = swing_radius(args.rode, depth, args.bow_height, gps_offset)
        print(f"{args.rode:.0f} m of rode in {depth:.1f} m: "
              f"alarm radius {radius:.0f} m "
              f"(horizontal reach + {gps_offset:.1f} m GPS offset + "
              f"{GPS_MARGIN_M:.0f} m GPS margin)")
    else:
        print("pass --radius METRES or --rode METRES", file=sys.stderr)
        raise SystemExit(1)

    anchor = AnchorWatch(lat, lon, radius, window_s=args.window, notify=not args.no_notify,
                         vessel_name=profile.vessel.name)
    print(f"anchor down at {lat:.6f}, {lon:.6f} — alarm at {radius:.0f} m, "
          f"fix every {args.interval:.0f} s")
    try:
        fixes = watch(anchor, args.interval, args.duration, args.record)
    except KeyboardInterrupt:
        fixes = []
        print("\nanchor watch stopped")
    print(f"furthest out: {anchor.max_distance_m:.0f} m")
    if anchor.fixes:
        print(anchor.swing())
    if args.record and fixes:
        print(f"track written to {args.record}")


if __name__ == "__main__":
    main()
