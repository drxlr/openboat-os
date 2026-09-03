#!/usr/bin/env python3
"""A boat that does not exist, on a TCP port, talking NMEA 0183.

Signal K, OpenCPN and every plotter app will happily connect to this and believe it.
That is the whole point: the entire stack can be built, wired and debugged on the kitchen
table in January, and the first time it meets a real boat nothing is new except the wiring.

    python3 simulate.py                 # listens on 0.0.0.0:10110
    nc localhost 10110                  # watch the sentences

Three modes, because a boat under way and a boat at anchor are different test subjects:

    --mode circle     a slow 6 kn circle off the demo position (default)
    --mode anchored   swinging on 35 m of rode, wind veering — an anchor watch should stay quiet
    --mode dragging   the same, with the anchor walking 40 m/h — an anchor watch should shout

The anchored modes exist because an anchor watch can only prove its plumbing against a boat
doing 6 kn: its drag heuristic watches the *centre* of the swing circle migrate, and a boat
under way has no swing circle at all. Without these modes the heuristic is testable only
against synthetic series, never through the real Signal K path.

The starting position defaults to Plymouth Sound, UK — a real, public stretch of water and
nobody's actual boat. Override it with --lat/--lon to simulate anywhere.
"""

from __future__ import annotations

import argparse
import math
import random
import socket
import threading
import time
from datetime import datetime, timezone

# Plymouth Sound, UK — open water, this project's public demo position. See
# profiles/demo-boat.toml for where the rest of the demo boat's facts live.
DEFAULT_LAT = 50.3100
DEFAULT_LON = -4.1500

CENTRE = (DEFAULT_LAT, DEFAULT_LON)   # overridden from --lat/--lon in __main__
RADIUS_NM = 1.5
SPEED_KN = 6.0

# Anchored modes. 35 m of rode in ~8 m of water is ordinary scope for a boat this size.
# The anchorage sits a little south-west of CENTRE, computed from it at runtime so
# --lat/--lon moves both together.
ANCHOR_OFFSET_NORTH_M = -220.0
ANCHOR_OFFSET_EAST_M = -300.0
ANCHOR = None                 # set from CENTRE in __main__
RODE_M = 35.0
SWING_PERIOD_S = 900.0        # how long the wind takes to veer the boat through its arc
SWING_ARC_DEG = 120.0         # how far it veers — a real boat rarely sweeps the full circle
DRAG_M_PER_H = 40.0           # anchor walking downwind, in `dragging` mode only
DRAG_BEARING_DEG = 215.0
GPS_NOISE_M = 1.5             # one sigma, consumer receiver under open sky
LEEWAY_DEG = -8.0             # how far off the track the bow actually points

MODE = "circle"
ENGINE = False        # --engine: also speak the sentences arduino/ will speak

M_PER_DEG_LAT = 1852.0 * 60.0


def checksum(body: str) -> str:
    value = 0
    for char in body:
        value ^= ord(char)
    return f"{value:02X}"


def sentence(body: str) -> str:
    return f"${body}*{checksum(body)}\r\n"


def dm(value: float, degrees_width: int) -> tuple[str, float]:
    """NMEA degrees-minutes: 3452.4500 for 34°52.45'."""
    magnitude = abs(value)
    degrees = int(magnitude)
    minutes = (magnitude - degrees) * 60
    return f"{degrees:0{degrees_width}d}{minutes:07.4f}", value


def offset(origin: tuple[float, float], north_m: float, east_m: float) -> tuple[float, float]:
    """Metres north and east of a point, in degrees. Flat-earth, fine over a swing circle."""
    lat = origin[0] + north_m / M_PER_DEG_LAT
    lon = origin[1] + east_m / (M_PER_DEG_LAT * math.cos(math.radians(origin[0])))
    return lat, lon


def true_position(elapsed: float) -> tuple[float, float]:
    """Where the boat really is — before the receiver adds its own scatter."""
    if MODE == "circle":
        circumference_nm = 2 * math.pi * RADIUS_NM
        angle = (elapsed * SPEED_KN / 3600 / circumference_nm) * 2 * math.pi
        lat = CENTRE[0] + RADIUS_NM / 60 * math.cos(angle)
        lon = CENTRE[1] + RADIUS_NM / 60 * math.sin(angle) / math.cos(math.radians(CENTRE[0]))
        return lat, lon

    # Anchored: the boat lies at rode length from the anchor, on a bearing that veers
    # back and forth through an arc rather than sweeping the full circle.
    phase = 2 * math.pi * elapsed / SWING_PERIOD_S
    bearing = math.radians(DRAG_BEARING_DEG + SWING_ARC_DEG / 2 * math.sin(phase))

    anchor = ANCHOR
    if MODE == "dragging":
        walked = DRAG_M_PER_H * elapsed / 3600.0
        anchor = offset(ANCHOR, walked * math.cos(math.radians(DRAG_BEARING_DEG)),
                                walked * math.sin(math.radians(DRAG_BEARING_DEG)))

    return offset(anchor, RODE_M * math.cos(bearing), RODE_M * math.sin(bearing))


def position(elapsed: float) -> tuple[float, float, float, float]:
    """Reported position, heading and speed.

    Course and speed come from the *noiseless* track, deliberately: a real receiver derives
    them from Doppler, not by differentiating a scattered position. Differentiating the
    noisy one would put 3 kn of nonsense on a boat lying still at anchor.
    """
    lat, lon = true_position(elapsed)
    ahead_lat, ahead_lon = true_position(elapsed + 1.0)

    north = (ahead_lat - lat) * M_PER_DEG_LAT
    east = (ahead_lon - lon) * M_PER_DEG_LAT * math.cos(math.radians(lat))
    sog_kn = math.hypot(north, east) * 3600 / 1852.0
    heading = math.degrees(math.atan2(east, north)) % 360

    if GPS_NOISE_M and MODE != "circle":
        lat, lon = offset((lat, lon), random.gauss(0, GPS_NOISE_M), random.gauss(0, GPS_NOISE_M))

    return lat, lon, heading, sog_kn


def sentences(elapsed: float) -> list[str]:
    lat, lon, heading, sog_kn = position(elapsed)
    now = datetime.now(timezone.utc)
    hhmmss = now.strftime("%H%M%S.00")
    ddmmyy = now.strftime("%d%m%y")

    lat_dm, _ = dm(lat, 2)
    lon_dm, _ = dm(lon, 3)
    ns, ew = ("N" if lat >= 0 else "S"), ("E" if lon >= 0 else "W")

    depth = 18 + 6 * math.sin(elapsed / 40)
    water = 26.5 + 0.4 * math.sin(elapsed / 300)
    wind_angle = (heading + 140) % 360
    wind_kn = 7 + 3 * math.sin(elapsed / 120)

    lines = [
        sentence(f"GPRMC,{hhmmss},A,{lat_dm},{ns},{lon_dm},{ew},"
                 f"{sog_kn:.1f},{heading:.1f},{ddmmyy},,,A"),
        sentence(f"GPGGA,{hhmmss},{lat_dm},{ns},{lon_dm},{ew},1,09,0.9,0.0,M,0.0,M,,"),
        sentence(f"GPVTG,{heading:.1f},T,,M,{sog_kn:.1f},N,{sog_kn * 1.852:.1f},K,A"),
        sentence(f"SDDPT,{depth:.1f},0.5,"),
        sentence(f"SDMTW,{water:.1f},C"),
        sentence(f"WIMWV,{wind_angle:.1f},R,{wind_kn:.1f},N,A"),
        # A compass, offset from the track. A boat almost never points exactly where it is
        # going — wind on the bow and a cross-set push it off, and the gap between heading
        # and course made good is the whole reason a helm panel shows both.
        sentence(f"HCHDT,{(heading + LEEWAY_DEG) % 360:.1f},T"),
    ]

    if ENGINE:
        lines += engine_sentences(elapsed, sog_kn)
    return lines


def engine_sentences(elapsed: float, sog_kn: float) -> list[str]:
    """Exactly what `arduino/engine_sender/engine_sender.ino` emits, and nothing it does not.

    The point is not to invent engine data — it is to exercise the path that data will
    take, from NMEA sentence through Signal K's conversions to the helm panel, before a
    single wire is run. If the panel reads these correctly it will read the Arduino
    correctly, because they are the same sentences.

    The numbers are a plausible petrol V8, not a measurement of this one. Everything is
    tied to speed so the gauges move together the way an engine's do.
    """
    load = min(sog_kn / 30.0, 1.0)
    rpm = 0.0 if sog_kn < 0.3 else 700 + 3600 * load
    # Oil pressure is high at idle on the relief valve, then rises with rpm.
    oil_bar = 0.0 if rpm == 0 else 1.4 + 2.9 * load
    # Coolant climbs to the thermostat and then holds, with load pushing it a little past.
    warm = min(elapsed / 420.0, 1.0)
    coolant = 26.0 + (55.0 * warm) + 8.0 * load
    # 12.6 V at rest, alternator up once the engine turns.
    volts = 12.6 if rpm == 0 else 14.1
    fuel = max(0.0, 0.78 - elapsed / 90000.0)

    return [
        sentence(f"IIRPM,E,1,{rpm:.0f},,A"),
        sentence(f"IIXDR,P,{oil_bar * 100000:.0f},P,OILPRESS"),
        sentence(f"IIXDR,C,{coolant:.1f},C,ENGTEMP"),
        sentence(f"IIXDR,U,{volts:.2f},V,BATT1"),
        sentence(f"IIXDR,V,{fuel:.3f},P,FUEL"),
    ]


def serve(host: str, port: int) -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((host, port))
    listener.listen(5)
    where = f"{CENTRE[0]:.4f}, {CENTRE[1]:.4f}"
    what = {"circle": f"{SPEED_KN} kn circle around {where}",
            "anchored": f"swinging on {RODE_M:.0f} m of rode, {SWING_ARC_DEG:.0f}° arc, near {where}",
            "dragging": f"anchored near {where}, dragging {DRAG_M_PER_H:.0f} m/h on {DRAG_BEARING_DEG:.0f}°"}[MODE]
    print(f"NMEA 0183 on {host}:{port} — {what}"
          f"{', with engine data' if ENGINE else ''}", flush=True)

    start = time.time()

    def talk(client: socket.socket, address) -> None:
        print(f"  connected: {address}", flush=True)
        try:
            while True:
                for line in sentences(time.time() - start):
                    client.sendall(line.encode("ascii"))
                time.sleep(1)
        except (BrokenPipeError, ConnectionResetError, OSError):
            print(f"  gone: {address}", flush=True)
        finally:
            client.close()

    while True:
        client, address = listener.accept()
        threading.Thread(target=talk, args=(client, address), daemon=True).start()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=10110)
    parser.add_argument("--mode", choices=["circle", "anchored", "dragging"], default="circle")
    parser.add_argument("--lat", type=float, default=DEFAULT_LAT,
                        help=f"starting latitude (default: {DEFAULT_LAT}, Plymouth Sound)")
    parser.add_argument("--lon", type=float, default=DEFAULT_LON,
                        help=f"starting longitude (default: {DEFAULT_LON}, Plymouth Sound)")
    parser.add_argument("--rode", type=float, default=RODE_M, help="metres of rode veered")
    parser.add_argument("--drag", type=float, default=DRAG_M_PER_H, help="drag rate, m/h")
    parser.add_argument("--noise", type=float, default=GPS_NOISE_M, help="GPS sigma, metres")
    parser.add_argument("--engine", action="store_true",
                        help="also speak the engine sentences arduino/ will speak")
    args = parser.parse_args()

    MODE, RODE_M, DRAG_M_PER_H, GPS_NOISE_M = args.mode, args.rode, args.drag, args.noise
    ENGINE = args.engine
    CENTRE = (args.lat, args.lon)
    ANCHOR = offset(CENTRE, ANCHOR_OFFSET_NORTH_M, ANCHOR_OFFSET_EAST_M)
    serve(args.host, args.port)
