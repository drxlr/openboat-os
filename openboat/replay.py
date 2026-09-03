"""Run the anchor watch against a boat that is not there.

An alarm you have never seen fire is not an alarm, it is a hope. But the honest test —
anchoring somewhere marginal and waiting for the anchor to let go — is exactly the night
nobody wants. So the watch is a pure state machine over fixes (`anchor.AnchorWatch`), and
this module feeds it three things:

    swing    a boat lying properly, swinging 120° on 25 m of rode. Must NOT alarm
    drag     the same boat with the anchor walking downwind at 80 m/h. Must alarm — and
             note that it alarms while the boat is still well inside the alarm circle,
             which is the entire reason for fitting a centre rather than measuring a range
    creep    a boat lying dead steady on one bearing, range slowly growing. The circle fit
             is blind here; the watch must say "possible drag" and must NOT wake anybody

    python3 -m openboat.replay drag
    python3 -m openboat.replay gpx night.gpx --radius 30

The GPS scatter in the synthetic tracks is 2.5 m standard deviation — a typical figure for
a consumer receiver with a clear sky, not something measured on any particular boat.
"""

from __future__ import annotations

import argparse
import math
import random
import sys

from .track import Fix, format_summary, offset, read_gpx, write_gpx
from .anchor import AnchorWatch

GPS_SIGMA_M = 2.5


def synthesise(scenario: str, anchorage: tuple[float, float], minutes: float = 45.0,
               interval_s: float = 10.0, rode_radius_m: float = 25.0,
               sigma_m: float = GPS_SIGMA_M,
               seed: int = 7) -> tuple[list[Fix], tuple[float, float]]:
    """Build a position series for one scenario. Returns the fixes and the drop point.

    `anchorage` has no built-in default: it is where the synthetic track is centred, and
    OpenBoat does not invent a position for you. The CLI below defaults it to the profile's
    `forecast_point` — open water, which is where an anchor scenario belongs — but any
    lat/lon works, including one made up for a test.
    """
    from datetime import datetime, timedelta, timezone

    rng = random.Random(seed)
    lat0, lon0 = anchorage
    start = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    steps = int(minutes * 60 / interval_s)
    fixes: list[Fix] = []

    for step in range(steps):
        elapsed = step * interval_s
        fraction = elapsed / (minutes * 60)

        if scenario == "swing":
            # Yawing back and forth across the wind, plus a slow veer in the breeze.
            bearing = 200 + 60 * math.sin(2 * math.pi * elapsed / 900) + 20 * fraction
            radius = rode_radius_m + 2.0 * math.sin(2 * math.pi * elapsed / 300)
            east_drift = north_drift = 0.0
        elif scenario == "drag":
            # Same swing, but the whole circle walks 80 m/h towards 240° (SW).
            bearing = 200 + 60 * math.sin(2 * math.pi * elapsed / 900) + 20 * fraction
            radius = rode_radius_m + 2.0 * math.sin(2 * math.pi * elapsed / 300)
            walked = 80.0 * elapsed / 3600
            east_drift = walked * math.sin(math.radians(240))
            north_drift = walked * math.cos(math.radians(240))
        elif scenario == "creep":
            # Pinned on one bearing by a steady breeze; range grows 40 m/h. This is what a
            # slow drag looks like when the boat never swings enough to show its centre.
            bearing = 200 + 6 * math.sin(2 * math.pi * elapsed / 600)
            radius = rode_radius_m + 40.0 * elapsed / 3600
            east_drift = north_drift = 0.0
        else:
            raise ValueError(f"unknown scenario {scenario!r}")

        east = radius * math.sin(math.radians(bearing)) + east_drift + rng.gauss(0, sigma_m)
        north = radius * math.cos(math.radians(bearing)) + north_drift + rng.gauss(0, sigma_m)
        lat, lon = offset(lat0, lon0, east, north)
        fixes.append(Fix(start + timedelta(seconds=elapsed), lat, lon))

    return fixes, (lat0, lon0)


def replay(fixes: list[Fix], anchor_at: tuple[float, float], radius_m: float,
           window_s: float = 1800.0, every: int = 30) -> AnchorWatch:
    """Feed a series through the watch and print what it would have done overnight."""
    watch = AnchorWatch(anchor_at[0], anchor_at[1], radius_m, window_s=window_s,
                        notify=False)      # replays never make noise
    events = 0

    print(f"anchor {anchor_at[0]:.6f}, {anchor_at[1]:.6f}   alarm radius {radius_m:.0f} m   "
          f"{len(fixes)} fixes")
    print(f"{format_summary(fixes)}\n")

    for index, fix in enumerate(fixes):
        reading = watch.update(fix)
        events += sum(1 for loud, _ in reading.events if loud)   # clears are not alarms
        if index % every == 0 or reading.events:
            print(f"{reading}  {reading.swing.verdict:<14} {reading.swing.why[:72]}")

    print(f"\nfurthest from the anchor: {watch.max_distance_m:.0f} m "
          f"({'inside' if watch.max_distance_m <= radius_m else 'OUTSIDE'} the "
          f"{radius_m:.0f} m circle)")
    print(f"alarms raised: {events}   watch ends in state: {watch.state}")
    print(watch.swing())
    return watch


def main() -> None:
    from .profile import load as load_profile

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("swing", "drag", "creep"):
        scenario = sub.add_parser(name, help=f"synthetic {name} scenario")
        scenario.add_argument("--minutes", type=float, default=45.0)
        scenario.add_argument("--interval", type=float, default=10.0)
        # 80 m is generous on purpose for the drag case: it proves the centre fit alarms
        # long before the range alone ever would.
        scenario.add_argument("--radius", type=float, default=80.0, help="alarm radius, m")
        scenario.add_argument("--rode-radius", type=float, default=25.0,
                              help="how far the boat lies from the anchor, m")
        scenario.add_argument("--noise", type=float, default=GPS_SIGMA_M, help="GPS sigma, m")
        scenario.add_argument("--seed", type=int, default=7)
        scenario.add_argument("--anchor", help="lat,lon (default: the profile's forecast point)")
        scenario.add_argument("--profile", help="path to a boat.toml (default: the usual search)")
        scenario.add_argument("--gpx", help="also write the synthetic track to this file")

    gpx = sub.add_parser("gpx", help="replay a recorded GPX track")
    gpx.add_argument("path")
    gpx.add_argument("--anchor", help="lat,lon (default: the first fix in the file)")
    gpx.add_argument("--radius", type=float, default=30.0)
    gpx.add_argument("--window", type=float, default=1800.0)

    args = parser.parse_args()

    if args.command == "gpx":
        fixes = read_gpx(args.path)
        if not fixes:
            print(f"{args.path} holds no timed track points", file=sys.stderr)
            raise SystemExit(1)
        if args.anchor:
            lat, lon = (float(part) for part in args.anchor.split(","))
        else:
            lat, lon = fixes[0].lat, fixes[0].lon
        replay(fixes, (lat, lon), args.radius, args.window)
        return

    profile = load_profile(args.profile)
    if args.anchor:
        anchorage = tuple(float(part) for part in args.anchor.split(","))
    else:
        anchorage = profile.forecast_point

    fixes, anchor_at = synthesise(args.command, anchorage, args.minutes, args.interval,
                                  args.rode_radius, args.noise, args.seed)
    if args.gpx:
        write_gpx(fixes, args.gpx, f"{profile.vessel.name} — synthetic {args.command}")
        print(f"track written to {args.gpx}")
    replay(fixes, anchor_at, args.radius)


if __name__ == "__main__":
    main()
