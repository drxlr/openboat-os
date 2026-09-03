"""Positions in, a GPX file out — and the arithmetic that makes a track worth keeping.

Two reasons this exists and is the bottom of the anchor-watch stack:

  1. The log book. *How far was that, how long did the engine run, what did we actually
     average* — questions nobody can answer from memory a week later, and every one of them
     is arithmetic over a list of positions with times on them.
  2. The night at anchor. A track recorded overnight can be replayed through `anchor.py`
     the next morning, at full speed, to see whether the boat moved. That is the only way
     to tune an alarm radius without spending a second night finding out.

GPX 1.1 because every chart app on every phone reads it — OpenCPN, Navionics, Freeboard,
Garmin — and because it is plain XML that will still open in thirty years.

    python3 -m openboat.track record night.gpx --duration 3600 --interval 5
    python3 -m openboat.track show night.gpx
"""

from __future__ import annotations

import argparse
import math
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

EARTH_R_M = 6371008.8      # IUGG mean radius; good to metres over any coastal distance
M_PER_NM = 1852.0
GPX_NS = "http://www.topografix.com/GPX/1/1"
CREATOR = "openboat-os"


@dataclass
class Fix:
    """One position at one moment. The only thing anything in this module consumes."""

    time: datetime
    lat: float
    lon: float
    sog_kn: float | None = None


# --- geometry -------------------------------------------------------------------------
#
# Two different tools for two different jobs, deliberately:
#
#   distance_m   haversine — great-circle, correct at any distance, used for track legs
#                and for the range from the anchor
#   local_xy     equirectangular metres east/north of a reference point, used for the
#                circle fitting in anchor.py. Over the ~100 m of a swinging circle its
#                error against the geodesic is well under a millimetre, and unlike
#                lat/lon it is a flat plane you can do least squares on.

def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_R_M * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing from the first point to the second, degrees true."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    y = math.sin(dlambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def local_xy(lat: float, lon: float, ref_lat: float, ref_lon: float) -> tuple[float, float]:
    """Metres east and north of a reference point."""
    x = math.radians(lon - ref_lon) * EARTH_R_M * math.cos(math.radians(ref_lat))
    y = math.radians(lat - ref_lat) * EARTH_R_M
    return x, y


def offset(lat: float, lon: float, east_m: float, north_m: float) -> tuple[float, float]:
    """The inverse of local_xy — used by the synthetic tracks in replay.py."""
    out_lat = lat + math.degrees(north_m / EARTH_R_M)
    out_lon = lon + math.degrees(east_m / (EARTH_R_M * math.cos(math.radians(lat))))
    return out_lat, out_lon


# --- summary --------------------------------------------------------------------------

def summary(fixes: list[Fix], smooth_s: float = 10.0) -> dict:
    """Distance run, duration, and speeds.

    `smooth_s` exists because max speed off raw GPS is a lie. A 1 Hz fix has a couple of
    metres of scatter on it; two metres in one second is 4 kn of pure noise, and the
    headline "max speed" of an unsmoothed track is always that noise rather than the boat.
    Speeds here are measured over pairs of fixes at least `smooth_s` apart.
    """
    if len(fixes) < 2:
        return {"fixes": len(fixes), "distance_nm": 0.0, "duration_s": 0.0,
                "avg_kn": None, "max_kn": None, "start": None, "end": None}

    metres = 0.0
    for a, b in zip(fixes, fixes[1:]):
        metres += distance_m(a.lat, a.lon, b.lat, b.lon)

    duration_s = (fixes[-1].time - fixes[0].time).total_seconds()

    # Sliding pairs at least smooth_s apart, walked once.
    max_kn = None
    anchor_index = 0
    for i, fix in enumerate(fixes):
        while (fix.time - fixes[anchor_index].time).total_seconds() > smooth_s and anchor_index < i:
            anchor_index += 1
        base = fixes[max(0, anchor_index - 1)]
        gap = (fix.time - base.time).total_seconds()
        if gap >= smooth_s:
            leg = distance_m(base.lat, base.lon, fix.lat, fix.lon)
            kn = leg / gap / M_PER_NM * 3600
            max_kn = kn if max_kn is None else max(max_kn, kn)

    if max_kn is None and duration_s > 0:
        # A track shorter than the smoothing window: report over the whole thing rather
        # than print "--", which reads like a failure instead of like a short track.
        max_kn = distance_m(fixes[0].lat, fixes[0].lon,
                            fixes[-1].lat, fixes[-1].lon) / duration_s / M_PER_NM * 3600

    return {
        "fixes": len(fixes),
        "distance_nm": metres / M_PER_NM,
        "duration_s": duration_s,
        "avg_kn": (metres / duration_s / M_PER_NM * 3600) if duration_s > 0 else None,
        "max_kn": max_kn,
        "start": fixes[0].time,
        "end": fixes[-1].time,
    }


def format_summary(fixes: list[Fix]) -> str:
    s = summary(fixes)
    if not s["fixes"]:
        return "empty track"
    hours, remainder = divmod(int(s["duration_s"]), 3600)
    minutes, seconds = divmod(remainder, 60)
    avg = f"{s['avg_kn']:.1f}" if s["avg_kn"] is not None else "--"
    top = f"{s['max_kn']:.1f}" if s["max_kn"] is not None else "--"
    return (f"{s['fixes']} fixes  {s['distance_nm']:.2f} nm  "
            f"{hours}h{minutes:02d}m{seconds:02d}s  avg {avg} kn  max {top} kn")


# --- GPX ------------------------------------------------------------------------------
#
# Note what is NOT written: speed. GPX 1.0 had <speed> on a trackpoint; GPX 1.1 removed it
# and put that sort of thing behind namespaced <extensions>. Rather than emit an
# unqualified element that makes the file fail its own schema, speed is left out and
# recomputed from positions and times on load — which is what a reader would have to do
# with an untrusted <speed> anyway.

def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_gpx(fixes: list[Fix], path: str | Path, name: str = "Boat",
              description: str | None = None) -> Path:
    ET.register_namespace("", GPX_NS)
    gpx = ET.Element(f"{{{GPX_NS}}}gpx", {"version": "1.1", "creator": CREATOR})

    # Child order is fixed by the schema: metadata, then trk. Inside metadata: name, desc,
    # then time. Get it wrong and the file parses everywhere and validates nowhere.
    metadata = ET.SubElement(gpx, f"{{{GPX_NS}}}metadata")
    ET.SubElement(metadata, f"{{{GPX_NS}}}name").text = name
    if description:
        ET.SubElement(metadata, f"{{{GPX_NS}}}desc").text = description
    ET.SubElement(metadata, f"{{{GPX_NS}}}time").text = _iso(
        fixes[0].time if fixes else datetime.now(timezone.utc))

    trk = ET.SubElement(gpx, f"{{{GPX_NS}}}trk")
    ET.SubElement(trk, f"{{{GPX_NS}}}name").text = name
    trkseg = ET.SubElement(trk, f"{{{GPX_NS}}}trkseg")
    for fix in fixes:
        point = ET.SubElement(trkseg, f"{{{GPX_NS}}}trkpt",
                              {"lat": f"{fix.lat:.7f}", "lon": f"{fix.lon:.7f}"})
        ET.SubElement(point, f"{{{GPX_NS}}}time").text = _iso(fix.time)

    tree = ET.ElementTree(gpx)
    ET.indent(tree, space="  ")
    path = Path(path)
    tree.write(path, encoding="utf-8", xml_declaration=True)
    return path


def read_gpx(path: str | Path) -> list[Fix]:
    """Read a GPX track back. Namespace-tolerant, so 1.0 files from old plotters load too."""
    root = ET.parse(Path(path)).getroot()
    fixes: list[Fix] = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "trkpt":
            continue
        when = None
        for child in element:
            if child.tag.rsplit("}", 1)[-1] == "time" and child.text:
                when = datetime.fromisoformat(child.text.strip().replace("Z", "+00:00"))
        if when is None:
            continue          # a trackpoint with no time is useless to everything here
        fixes.append(Fix(when, float(element.get("lat")), float(element.get("lon"))))
    fixes.sort(key=lambda f: f.time)
    return fixes


# --- recording --------------------------------------------------------------------------
#
# This is the one place in the module that touches a live boat, and it is a thin one: the
# Signal K client (`openboat.boat`) is imported lazily, inside the function, rather than at
# module load. That keeps `import openboat.track` — and everything that only wants Fix,
# the geometry or the GPX read/write — working with no boat, no network and no Signal K
# client wired up yet, which matters while that client is still being built out.

def record(path: str | Path, duration_s: float = 3600, interval_s: float = 5.0,
           name: str = "Boat", flush_every: int = 10, quiet: bool = False) -> list[Fix]:
    """Poll Signal K and write a GPX track. Survives the boat going offline mid-track.

    The file is rewritten every `flush_every` fixes rather than only at the end: a track
    that exists only in memory until the process is killed is a track that does not exist.
    """
    import time

    from . import boat  # noqa: PLC0415 — read-only Signal K client, imported lazily

    fixes: list[Fix] = []
    deadline = time.monotonic() + duration_s
    offline_since = None

    while time.monotonic() < deadline:
        try:
            state = boat.state()
            offline_since = None
        except boat.Offline as exc:
            # The normal case ashore. Keep the loop alive; the track simply has a gap.
            if offline_since is None and not quiet:
                offline_since = time.monotonic()
                print(f"boat offline ({exc}) — waiting", file=sys.stderr)
            time.sleep(interval_s)
            continue

        if state.get("lat") is None:
            time.sleep(interval_s)
            continue

        fix = Fix(datetime.now(timezone.utc), state["lat"], state["lon"], state.get("sog_kn"))
        fixes.append(fix)
        if not quiet:
            speed = f"{fix.sog_kn:.1f} kn" if fix.sog_kn is not None else "-- kn"
            print(f"{fix.time:%H:%M:%S}  {fix.lat:.6f} {fix.lon:.6f}  {speed}")
        if len(fixes) % flush_every == 0:
            write_gpx(fixes, path, name)
        time.sleep(interval_s)

    write_gpx(fixes, path, name)
    return fixes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    rec = sub.add_parser("record", help="record a GPX track from Signal K")
    rec.add_argument("path")
    rec.add_argument("--duration", type=float, default=3600, help="seconds (default 3600)")
    rec.add_argument("--interval", type=float, default=5.0, help="seconds between fixes")
    rec.add_argument("--name", default=None, help="default: the boat's name from the profile")
    rec.add_argument("--profile", help="path to a boat.toml (default: the usual search)")

    show = sub.add_parser("show", help="summarise an existing GPX file")
    show.add_argument("path")

    args = parser.parse_args()
    if args.command == "record":
        name = args.name
        if name is None:
            from .profile import load as load_profile
            try:
                name = load_profile(args.profile).vessel.name
            except Exception:
                name = "Boat"
        fixes = record(args.path, args.duration, args.interval, name)
        print(f"\n{args.path}: {format_summary(fixes)}")
    else:
        fixes = read_gpx(args.path)
        print(f"{args.path}: {format_summary(fixes)}")


if __name__ == "__main__":
    main()
