"""A recorded track in, a written log entry out — with the real weather of the day attached.

    python3 -m openboat.trip --make-sample sample-track.gpx   # a plausible day out, no boat needed
    python3 -m openboat.trip sample-track.gpx                 # -> markdown on stdout
    python3 -m openboat.trip sample-track.gpx -o entry.md --lph 60

The interesting half is not the arithmetic, it is the weather. A log records what was
*done*; what it never records is what the day was actually like, because nobody writes
that down at the time and nobody can reconstruct it afterwards. Open-Meteo's ERA5 archive
can, for those exact hours at that exact position, years later. So a log entry written from
a track is a better log entry than one written from memory the same evening.

INPUT CONTRACT — plain GPX 1.1, which is what every recorder emits, `openboat.track`
included:

    <gpx version="1.1"><trk><trkseg>
      <trkpt lat="50.3640" lon="-4.1310"><ele>0.4</ele><time>2026-08-16T05:40:00Z</time></trkpt>
      ...

Only `lat`, `lon` and `time` are read. `<ele>` is ignored — GPS altitude on a boat is noise
about a known value. Multiple `<trkseg>` are concatenated. Namespaces are stripped, so a
file that declares GPX 1.0 or carries other extensions parses fine.

⚠️ FUEL is arithmetic, not measurement. Most boats have no fuel-flow sender, and a burn
rate is rarely actually measured. Every fuel figure this file prints is `moving hours × a
number you supplied` (or a `cruise_burn_lph` your profile has sourced), and it says so on
the same line, every time — or says it does not know, rather than defaulting to a guess.

⚠️ NAMED PLACES are optional and start empty. Nothing in this file knows your local
coastline; pass your own `places` (a list of `Waypoint`, first one your home berth) to
`analyse()` if you want the log entry to say what it passed. Without one, the entry still
reports distance, time and weather — it just does not try to narrate a route it was never
told about. `--make-sample` uses a small set of real, public Plymouth Sound landmarks to
demonstrate the feature; they are illustrative, not chart-checked, and irrelevant to a real
track anywhere else.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo as TzInfo
from pathlib import Path

from .marine import Hour
from .route import Waypoint, rhumb
from .track import read_gpx as _read_gpx_utc

# A boat at anchor wanders. Below this the GPS is describing the swinging circle, not a
# passage, and both the clock and the distance total have to stop.
MOVING_KN = 1.0

# Point-to-point speed over a 10 s sample with metre-level GPS noise reads high; a max
# speed taken off it is a fiction. Everything here is displacement over a centred window
# instead, which costs nothing on a straight plane and kills the noise.
SPEED_WINDOW_S = 20

# How close the boat has to come before a place is worth naming in the entry.
NAMED_WITHIN_NM = 3.0

_COMPASS_16 = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
              "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")


def compass(degrees: float) -> str:
    """Nearest of the 16 compass points a bearing is closest to."""
    return _COMPASS_16[int((degrees % 360) / 22.5 + 0.5) % 16]


# --- the track --------------------------------------------------------------------------

@dataclass
class Fix:
    time: datetime      # aware, in the boat's own local time
    lat: float
    lon: float


@dataclass
class Pass:
    """The boat's closest approach to a named place, and when it happened."""

    place: str
    distance_nm: float
    time: datetime
    bearing_deg: float   # from the place to the boat, i.e. which side it went by


@dataclass
class Trip:
    fixes: list[Fix]
    speeds_kn: list[float]          # smoothed, one per fix
    distance_nm: float              # summed over moving fixes only
    moving_h: float
    stopped_h: float
    max_kn: float
    weather: list[Hour]
    passes: list[Pass]

    @property
    def start(self) -> datetime:
        return self.fixes[0].time

    @property
    def end(self) -> datetime:
        return self.fixes[-1].time

    @property
    def elapsed_h(self) -> float:
        return (self.end - self.start).total_seconds() / 3600

    @property
    def avg_moving_kn(self) -> float:
        return self.distance_nm / self.moving_h if self.moving_h else 0.0

    @property
    def avg_overall_kn(self) -> float:
        return self.distance_nm / self.elapsed_h if self.elapsed_h else 0.0


def parse_gpx(path: Path, tz: TzInfo = timezone.utc) -> list[Fix]:
    """Every timed point in a GPX file, converted to the boat's own local wall clock.

    Parsing itself is `openboat.track.read_gpx` — no reason to have two GPX readers in the
    same package. What this adds is the timezone: `track.Fix.time` is aware UTC, and a log
    entry wants to say "off the berth at 08:40" in the time the skipper actually saw, not
    in UTC. Aware on both sides throughout, so the arithmetic downstream is correct and any
    mistake is a loud `TypeError` rather than a silently wrong wall clock.
    """
    return [Fix(time=fix.time.astimezone(tz), lat=fix.lat, lon=fix.lon)
            for fix in _read_gpx_utc(path)]


def _nm(a: Fix | Waypoint, b: Fix | Waypoint) -> float:
    return rhumb(Waypoint("", a.lat, a.lon), Waypoint("", b.lat, b.lon))[0]


def _smoothed_speeds(fixes: list[Fix]) -> list[float]:
    """Displacement over a centred ±SPEED_WINDOW_S window, in knots, one value per fix."""
    speeds = []
    for i, fix in enumerate(fixes):
        low = high = i
        while low > 0 and (fix.time - fixes[low - 1].time).total_seconds() <= SPEED_WINDOW_S:
            low -= 1
        while (high < len(fixes) - 1
               and (fixes[high + 1].time - fix.time).total_seconds() <= SPEED_WINDOW_S):
            high += 1
        span_h = (fixes[high].time - fixes[low].time).total_seconds() / 3600
        speeds.append(_nm(fixes[low], fixes[high]) / span_h if span_h > 0 else 0.0)
    return speeds


def _closest_passes(fixes: list[Fix], places: list[Waypoint]) -> list[Pass]:
    """Every distinct approach to every named place — not one per place.

    A day out can pass the same headland twice, and collapsing that to a single nearest fix
    puts it on whichever leg happened to come a metre closer, which then reads as if the
    boat only saw it once. So: walk the distance series, and every time it dips inside
    `NAMED_WITHIN_NM` and comes back out again, that run is one approach and its minimum is
    the closest point of it.

    Nearest *fix* rather than nearest point along the leg between fixes: at a ten-second
    sample rate the difference is under a cable, and the number exists to write "a mile off".
    """
    found = []
    for place in places:
        run: list[tuple[float, Fix]] = []
        for fix in fixes + [None]:
            near = fix is not None and _nm(fix, place) <= NAMED_WITHIN_NM
            if near:
                run.append((_nm(fix, place), fix))
                continue
            if run:
                distance, nearest = min(run, key=lambda pair: pair[0])
                _, bearing = rhumb(place, Waypoint("", nearest.lat, nearest.lon))
                found.append(Pass(place.name, distance, nearest.time, bearing))
                run = []
    return sorted(found, key=lambda leg: leg.time)


def analyse(fixes: list[Fix], places: list[Waypoint] | None = None,
           with_weather: bool = True, boat=None) -> Trip:
    """Everything derivable from the track, plus the archive weather for its hours.

    `places` is deliberately not defaulted from anywhere private: leave it out and the
    entry reports what happened without trying to name where. Pass your own list — first
    entry treated as home — to get the "out past X to Y, home past Z" narrative.
    """
    if len(fixes) < 2:
        raise ValueError("a trip needs at least two timed track points")
    places = places or []

    speeds = _smoothed_speeds(fixes)

    distance = moving_s = stopped_s = 0.0
    for i in range(1, len(fixes)):
        seconds = (fixes[i].time - fixes[i - 1].time).total_seconds()
        # Under way if either end of the interval is: this keeps the moment of getting
        # under way and the moment of stopping out of the anchored total.
        if max(speeds[i], speeds[i - 1]) >= MOVING_KN:
            moving_s += seconds
            # Distance only accumulates while moving. Summed over an anchored hour, GPS
            # wander alone invents most of a mile — a log entry that reports it is wrong.
            distance += _nm(fixes[i - 1], fixes[i])
        else:
            stopped_s += seconds

    weather = _weather_for(fixes, boat) if with_weather else []

    return Trip(fixes=fixes, speeds_kn=speeds, distance_nm=distance,
                moving_h=moving_s / 3600, stopped_h=stopped_s / 3600,
                max_kn=max(speeds), weather=weather, passes=_closest_passes(fixes, places))


def _weather_for(fixes: list[Fix], boat=None) -> list[Hour]:
    """The archive hours that overlap the trip, at the track's mean position.

    One position for the whole trip, not one per hour: ERA5's grid is about 28 km, so even
    a forty-mile day lands in two or three cells and the mean of the track is inside the
    same weather as every part of it. Fetching per hour would spend requests to resolve a
    difference the model does not contain.
    """
    lat = sum(f.lat for f in fixes) / len(fixes)
    lon = sum(f.lon for f in fixes) / len(fixes)
    day = fixes[0].time.date()
    tz = fixes[0].time.tzinfo or timezone.utc

    try:
        # A day either side, so a trip that runs to midnight still gets its last hour.
        got = era5_hours(day - timedelta(days=1), day + timedelta(days=1), lat, lon,
                         boat=boat, tz=tz)
    except Exception as exc:
        print(f"# weather unavailable: {exc}", file=sys.stderr)
        return []

    first = fixes[0].time.replace(minute=0, second=0, microsecond=0)
    return [h for h in got if first <= h.time <= fixes[-1].time]


# --- ERA5 — what the weather actually was, ported from the standalone archive module ----
#
# Same two providers as `openboat.marine`, pointed backwards:
#
#   archive-api.open-meteo.com/v1/archive   ERA5 reanalysis, hourly, 1940 -> yesterday
#   marine-api.open-meteo.com/v1/marine     wave height / period / direction, 2022-01-01 ->
#
# Why this is not a flag on `marine.forecast()`: that client asks for `forecast_days` and
# is handed a model's opinion about the future. This asks for a date range and is handed
# the reanalysis of what actually happened. A log entry that quotes the forecast is a log
# entry that quotes a guess — the whole point of this module is that it does not do that.
# It returns `marine.Hour` objects on purpose, so `season.py` can run `openboat.windows`'s
# own pass/fail test over ten years of history without a line of change.

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"

ARCHIVE_VARS = "wind_speed_10m,wind_gusts_10m,wind_direction_10m,temperature_2m,precipitation"
MARINE_VARS = "wave_height,wave_period,wave_direction,sea_surface_temperature"

#: The wave archive is younger than the wind archive; every caller has to cope with that.
WAVE_START = date(2022, 1, 1)

CACHE_DIR = Path(os.environ.get("OPENBOAT_CACHE_DIR", ".cache"))


def _cached_get(url: str, params: dict, refresh: bool = False) -> dict:
    """GET with a disk cache. Ten years of hourly ERA5 is a few megabytes and never changes."""
    query = urllib.parse.urlencode(params)
    key = hashlib.sha1(f"{url}?{query}".encode()).hexdigest()[:16]
    path = CACHE_DIR / f"{key}.json"

    if path.exists() and not refresh:
        return json.loads(path.read_text())

    with urllib.request.urlopen(f"{url}?{query}", timeout=180) as response:
        payload = json.loads(response.read())

    CACHE_DIR.mkdir(exist_ok=True, parents=True)
    path.write_text(json.dumps(payload))
    return payload


def _local(utc_stamp: str, tz: TzInfo) -> datetime:
    """Open-Meteo's `timezone=UTC` stamps, converted with a real timezone.

    ⚠️ Open-Meteo's `timezone=auto` returns **one** offset for the whole request, which is
    wrong for any place that observes daylight saving anywhere inside the requested range —
    a multi-year request would put every winter hour in the wrong bucket, smearing exactly
    the diurnal pattern `season.py` exists to measure. So every request here asks for UTC
    and this converts it, using `zoneinfo` and the profile's own timezone name, which
    handles the DST transition correctly for wherever the boat actually is.
    """
    moment = datetime.fromisoformat(utc_stamp)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(tz)


def era5_hours(start: date, end: date, lat: float | None = None, lon: float | None = None,
               boat=None, waves: bool = True, refresh: bool = False,
               tz: TzInfo = timezone.utc) -> list[Hour]:
    """Every hour between two dates, inclusive, at one point, on the boat's own wall clock.

    With no coordinates, asks about the profile's `forecast_point`, matching `marine.forecast`.

    `waves=True` joins the marine archive on top where it reaches. Where it does not, the
    wave fields stay `None` — which is not a gap to apologise for: `windows.passes()`
    already skips the sea-state test when it has no sea state, so a wind-only verdict falls
    out of the same code path rather than needing a second one.
    """
    if lat is None or lon is None:
        from .profile import load as load_profile
        lat, lon = (boat or load_profile()).forecast_point

    common = {"latitude": lat, "longitude": lon,
              "start_date": start.isoformat(), "end_date": end.isoformat(),
              "timezone": "UTC"}

    wind = _cached_get(ARCHIVE_URL, {**common, "hourly": ARCHIVE_VARS,
                                     "wind_speed_unit": "kn"}, refresh)["hourly"]

    sea: dict[str, list] = {}
    if waves and end >= WAVE_START:
        wave_start = max(start, WAVE_START)
        try:
            sea = _cached_get(MARINE_URL, {**common, "start_date": wave_start.isoformat(),
                                           "hourly": MARINE_VARS}, refresh)["hourly"]
        except Exception:
            sea = {}  # degrade, never crash — the wind half is still worth having

    # Join on the UTC stamp rather than on index: the two endpoints start on different days
    # whenever the request reaches back past 2022.
    by_stamp = {stamp: i for i, stamp in enumerate(sea.get("time", []))}

    def wave(key: str, stamp: str):
        index = by_stamp.get(stamp)
        series = sea.get(key)
        return series[index] if index is not None and series else None

    out = []
    for i, stamp in enumerate(wind["time"]):
        out.append(Hour(
            time=_local(stamp, tz),
            wind_kn=wind["wind_speed_10m"][i],
            gust_kn=wind["wind_gusts_10m"][i],
            wind_deg=wind["wind_direction_10m"][i],
            temp_c=wind["temperature_2m"][i],
            rain_mm=wind["precipitation"][i] or 0.0,
            wave_m=wave("wave_height", stamp),
            wave_s=wave("wave_period", stamp),
            wave_deg=wave("wave_direction", stamp),
        ))

    # ERA5 trails real time by a few days; Open-Meteo pads the tail with nulls rather than
    # truncating. Drop those instead of letting a None wind speed reach the arithmetic.
    return [h for h in out if h.wind_kn is not None and h.gust_kn is not None]


def era5_sea_temperature(start: date, end: date, lat: float, lon: float,
                         refresh: bool = False) -> dict[str, float]:
    """Sea surface temperature by UTC stamp. Separate call because only `season.py` wants it."""
    if end < WAVE_START:
        return {}
    payload = _cached_get(MARINE_URL, {"latitude": lat, "longitude": lon,
                                       "start_date": max(start, WAVE_START).isoformat(),
                                       "end_date": end.isoformat(), "timezone": "UTC",
                                       "hourly": "sea_surface_temperature"}, refresh)["hourly"]
    return {stamp: value
            for stamp, value in zip(payload["time"], payload["sea_surface_temperature"])
            if value is not None}


# --- writing it down --------------------------------------------------------------------

def de(value: float, digits: int = 1) -> str:
    """European decimals — `24,6`, `1.240` — the style this log entry is written in."""
    text = f"{value:,.{digits}f}"
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _hm(hours: float) -> str:
    total = round(hours * 60)
    return f"{total // 60} h {total % 60:02d} min"


def _side(bearing: float) -> str:
    """Which way the place lay from the boat — the reverse of the boat's bearing from it."""
    return compass((bearing + 180) % 360)


def _listed(names: list[str]) -> str:
    """`a`, `a and b`, `a, b and c` — the log is prose, not a comma-separated field."""
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"


def _narrative(trip: Trip, places: list[Waypoint]) -> str:
    """One sentence saying where the boat went, built out of what it actually passed.

    The shape of a day out is out-to-somewhere-and-back, so the sentence is hung on the
    turning point: the named place *farthest from home*, not the last one passed. Taking
    the last one gives "…to the marina entrance", two hundred metres from home, which reads
    as nonsense next to a forty-mile day.
    """
    if not places:
        return "Off the berth and back."
    home = places[0]
    away = [leg for leg in trip.passes if leg.place != home.name]
    if not away:
        return "Off the berth and back — nothing named came within three miles."

    place_by_name = {w.name: w for w in places}
    turn = max(away, key=lambda leg: _nm(home, place_by_name[leg.place]))
    out = [leg.place for leg in away if leg.time < turn.time]
    back = [leg.place for leg in away if leg.time > turn.time]
    if out and back == list(reversed(out)):
        return f"Out past {_listed(out)} to {turn.place}, and the same way home."

    sentence = f"Out past {_listed(out)} to {turn.place}" if out else f"Out to {turn.place}"
    if back:
        sentence += f", home past {_listed(back)}."
    else:
        sentence += " and back."
    return sentence


def to_markdown(trip: Trip, lph: float | None, title: str | None = None,
                places: list[Waypoint] | None = None, sample: bool = False) -> str:
    """A `## YYYY-MM-DD — Title` entry, ready to paste into your own log."""
    places = places or []
    day = trip.start.date()
    heading = title or _headline(trip, places)
    lines = [f"## {day.isoformat()} — {heading}", ""]

    if sample:
        lines += ["> ⚠️ **Sample, not a passage.** The track was generated by "
                  "`openboat.trip --make-sample` and the boat was not there. "
                  "The weather below is real: it is the ERA5 reanalysis for those hours "
                  "at that position.", ""]

    # 1 — the prose, because that is what the log is
    lines += [
        f"{_narrative(trip, places)} Off the berth at **{trip.start:%H:%M}**, back alongside "
        f"at **{trip.end:%H:%M}** — {_hm(trip.elapsed_h)} out, of which {_hm(trip.moving_h)} "
        f"under way and {_hm(trip.stopped_h)} stopped. "
        f"**{de(trip.distance_nm)} nm** run, {de(trip.avg_moving_kn)} kn average while "
        f"moving, {de(trip.max_kn)} kn the best of it.",
        "",
    ]

    if trip.weather:
        lines += [_weather_prose(trip), ""]

    # 2 — the numbers, once, in a table
    if lph is not None:
        fuel_line = (f"| Fuel | ~{de(trip.moving_h * lph, 0)} L — **arithmetic, not "
                     f"measured**: {_hm(trip.moving_h)} under way × {de(lph, 0)} l/h "
                     f"assumed |")
        fuel_note = (f"⚠️ The {de(lph, 0)} l/h is a placeholder, not a measured property of "
                     f"this boat unless your profile's `cruise_burn_lph` is sourced. Nothing "
                     f"measures fuel flow on most boats and no burn rate is measured until "
                     f"someone does a tank-to-tank test over a known distance. The figure "
                     f"above is a multiplication and should be read as one.")
    else:
        fuel_line = "| Fuel | unknown — no litres-per-hour given, and none is set in the profile |"
        fuel_note = ("⚠️ Pass `--lph` or set a sourced `cruise_burn_lph` in your profile to "
                     "get a fuel estimate. Nothing here will guess one.")

    lines += [
        "| | |",
        "|---|---|",
        f"| Distance | **{de(trip.distance_nm)} nm** |",
        f"| Under way | {_hm(trip.moving_h)} |",
        f"| Stopped | {_hm(trip.stopped_h)} |",
        f"| Elapsed | {_hm(trip.elapsed_h)} |",
        f"| Average, moving | {de(trip.avg_moving_kn)} kn |",
        f"| Average, door to door | {de(trip.avg_overall_kn)} kn |",
        f"| Maximum | {de(trip.max_kn)} kn |",
        fuel_line,
        "",
        fuel_note,
        "",
    ]

    # 3 — where it went
    if trip.passes:
        lines += ["**Closest approach**", "", "| Time | Place | Off | Passed to |",
                  "|---|---|---|---|"]
        for leg in trip.passes:
            side = "alongside" if leg.distance_nm < 0.05 else f"{_side(leg.bearing_deg)} of it"
            lines.append(f"| {leg.time:%H:%M} | {leg.place} "
                         f"| {de(leg.distance_nm, 2)} nm | {side} |")
        lines += ["", "Positions are approximate and are here to name the day — not to "
                  "navigate by.", ""]

    # 4 — the weather, hour by hour
    if trip.weather:
        lines += ["**What it was actually doing** — ERA5 reanalysis, "
                  f"{de(_mean_lat(trip), 3)}°N {de(_mean_lon(trip), 3)}°E, hourly",
                  "", "| Hour | Wind | Gust | Bft | Sea | Air |", "|---|---|---|---|---|---|"]
        for hour in trip.weather:
            sea = f"{de(hour.wave_m, 2)} m" if hour.wave_m is not None else "—"
            lines.append(
                f"| {hour.time:%H:%M} | {de(hour.wind_kn)} kn {hour.wind_name} "
                f"| {de(hour.gust_kn)} kn | {hour.beaufort} | {sea} "
                f"| {de(hour.temp_c)} °C |")
        lines += ["", "Reanalysis on a ~28 km grid: the weather over the bay, not the "
                  "weather on the boat. It will miss a local squall and it will not know "
                  "what the swell felt like at the helm.", ""]

    return "\n".join(lines)


def _mean_lat(trip: Trip) -> float:
    return sum(f.lat for f in trip.fixes) / len(trip.fixes)


def _mean_lon(trip: Trip) -> float:
    return sum(f.lon for f in trip.fixes) / len(trip.fixes)


def _headline(trip: Trip, places: list[Waypoint]) -> str:
    """Farthest named place from home, and the distance — the shape of the day."""
    if trip.passes and places:
        home = places[0]
        place_by_name = {w.name: w for w in places}
        far = max(trip.passes, key=lambda p: _nm(home, place_by_name[p.place]))
        if far.place != home.name:
            return f"{home.name} to {far.place} and back — {de(trip.distance_nm)} nm"
    return f"{de(trip.distance_nm)} nm out and back"


def _weather_prose(trip: Trip) -> str:
    winds = [h.wind_kn for h in trip.weather]
    gusts = [h.gust_kn for h in trip.weather]
    waves = [h.wave_m for h in trip.weather if h.wave_m is not None]
    first, last = trip.weather[0], trip.weather[-1]

    text = (f"The archive says it went out in {de(first.wind_kn)} kn from the "
            f"{first.wind_name} and came home in {de(last.wind_kn)} kn from the "
            f"{last.wind_name} — {de(min(winds))} to {de(max(winds))} kn across the day, "
            f"gusting {de(max(gusts))}")
    if waves:
        text += f", sea {de(min(waves), 1)}–{de(max(waves), 1)} m"
    text += (f". Air {de(min(h.temp_c for h in trip.weather))}–"
             f"{de(max(h.temp_c for h in trip.weather))} °C.")

    # The one line that a forecast could never have given, and the reason for the module.
    build = max(winds) - winds[0]
    if build >= 4:
        text += (f" It built {de(build)} kn between {first.time:%H:%M} and "
                 f"{max(trip.weather, key=lambda h: h.wind_kn).time:%H:%M} — see "
                 "`season.py` for whether that is the pattern most days.")
    return text


# --- the sample -------------------------------------------------------------------------
# A day out that never happened, so that everything above can be exercised with no boat, no
# GPX recorder and no network for the track itself (the weather lookup still needs one).
# Deterministic: same seed, byte-identical track for a given start time.

#: Real, public Plymouth Sound landmarks, used only to demonstrate the "closest approach"
#: narrative in `--make-sample`. Approximate positions, good to a few hundred metres, read
#: off public map data — nowhere near enough to navigate by, and irrelevant to a real track
#: recorded anywhere else. First entry is treated as home, matching the demo profile's berth.
DEMO_PLACES = [
    Waypoint("Queen Anne's Battery", 50.3640, -4.1310),
    Waypoint("Mount Batten", 50.3580, -4.1280),
    Waypoint("Drake's Island", 50.3536, -4.1421),
    Waypoint("Plymouth Breakwater", 50.3350, -4.1430),
]

# (lat, lon, target speed in knots on the way to this point, seconds stopped on arrival)
# A simple out-and-back within Plymouth Sound — open water on any chart of the area, and
# deliberately modest rather than chart-checked in detail: it is a demo, not a route.
DEMO_LEGS = [
    (50.3640, -4.1310, 4.0, 0),        # the berth
    (50.3600, -4.1350, 4.0, 0),        # marina entrance, no wake
    (50.3580, -4.1280, 12.0, 0),       # past Mount Batten, opening up
    (50.3536, -4.1421, 16.0, 0),       # towards Drake's Island
    (50.3400, -4.1430, 18.0, 0),       # out towards the Breakwater
    (50.3300, -4.1470, 6.0, 3600),     # anchored in the Sound — lunch, 1 h
    (50.3400, -4.1430, 16.0, 0),
    (50.3536, -4.1421, 16.0, 0),       # Drake's Island again, homeward
    (50.3580, -4.1280, 12.0, 1200),    # swim stop, 20 min
    (50.3600, -4.1350, 8.0, 0),
    (50.3640, -4.1310, 3.5, 0),        # alongside
]

SAMPLE_STEP_S = 10                    # what a phone or a plotter logs at
SAMPLE_JITTER_M = 2.5                 # one-sigma GPS scatter, flat water
SAMPLE_SEED = 295

# Stamped into the file and read back by the CLI, so a generated track can never be
# mistaken for a recorded one once it is sitting in a folder next to real ones.
SAMPLE_CREATOR = "openboat-os openboat.trip --make-sample"


def make_sample(path: Path, start: datetime | None = None,
                legs: list[tuple[float, float, float, int]] | None = None,
                tz: TzInfo = timezone.utc) -> None:
    """Write a generated track. `start` and the legs are all handled in `tz` throughout —
    never in the machine's own local time — so the same call produces the same track
    regardless of what computer runs it. Leave `start` unset for "a few days ago" at a
    plausible morning hour, in `tz`.
    """
    legs = legs or DEMO_LEGS
    start = start or (datetime.now(tz).replace(hour=8, minute=40, second=0, microsecond=0)
                      - timedelta(days=3))
    rng = random.Random(SAMPLE_SEED)
    fixes: list[tuple[datetime, float, float]] = []

    clock = start
    lat, lon, speed = legs[0][0], legs[0][1], 0.0

    def emit(when, la, lo):
        # Metres -> degrees, locally flat. 1' of latitude is a nautical mile by definition.
        dlat = rng.gauss(0, SAMPLE_JITTER_M) / 1852.0 / 60.0
        dlon = rng.gauss(0, SAMPLE_JITTER_M) / 1852.0 / 60.0 / math.cos(math.radians(la))
        fixes.append((when, la + dlat, lo + dlon))

    for target_lat, target_lon, target_kn, stop_s in legs[1:]:
        while True:
            remaining = _nm(Fix(clock, lat, lon), Fix(clock, target_lat, target_lon))
            if remaining < 0.01:
                break
            # Throttle moves, the boat does not teleport.
            speed += max(-1.5, min(1.5, target_kn - speed))
            step_nm = min(speed * SAMPLE_STEP_S / 3600, remaining)
            fraction = step_nm / remaining
            lat += (target_lat - lat) * fraction
            lon += (target_lon - lon) * fraction
            clock += timedelta(seconds=SAMPLE_STEP_S)
            emit(clock, lat, lon)

        for _ in range(stop_s // SAMPLE_STEP_S):
            # Swinging at anchor: a slow wander, well under the moving threshold.
            lat += rng.gauss(0, 0.35) / 1852.0 / 60.0
            lon += rng.gauss(0, 0.35) / 1852.0 / 60.0
            clock += timedelta(seconds=SAMPLE_STEP_S)
            emit(clock, lat, lon)
        speed = 0.0 if stop_s else speed

    body = "\n".join(
        f'   <trkpt lat="{la:.6f}" lon="{lo:.6f}">'
        f'<ele>{rng.gauss(0.4, 1.2):.1f}</ele>'
        f'<time>{when.astimezone(timezone.utc):%Y-%m-%dT%H:%M:%SZ}</time></trkpt>'
        for when, la, lo in fixes)

    when0 = fixes[0][0] if fixes[0][0].tzinfo else fixes[0][0].replace(tzinfo=timezone.utc)
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<gpx version="1.1" creator="{SAMPLE_CREATOR}"\n'
        '     xmlns="http://www.topografix.com/GPX/1/1">\n'
        f' <metadata><name>Sample track — a day in Plymouth Sound</name>\n'
        f'  <desc>Generated, not recorded. Seed {SAMPLE_SEED}. No boat was present.</desc>\n'
        f'  <time>{when0:%Y-%m-%dT%H:%M:%SZ}</time></metadata>\n'
        ' <trk><name>Plymouth Sound — out and back</name><trkseg>\n'
        f'{body}\n'
        ' </trkseg></trk>\n'
        '</gpx>\n')
    print(f"wrote {path} — {len(fixes)} points, "
          f"{fixes[0][0]:%Y-%m-%d %H:%M} to {fixes[-1][0]:%H:%M} local", file=sys.stderr)


# --- cli --------------------------------------------------------------------------------

def main() -> None:
    from zoneinfo import ZoneInfo

    from .profile import load as load_profile

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("gpx", nargs="?", type=Path, help="a GPX 1.1 track")
    parser.add_argument("--make-sample", type=Path, metavar="OUT",
                        help="write a generated sample track and exit")
    parser.add_argument("-o", "--out", type=Path, help="write markdown here instead of stdout")
    parser.add_argument("--lph", type=float,
                        help="assumed litres per hour under way "
                             "(default: the profile's cruise_burn_lph, if it has one)")
    parser.add_argument("--title", help="override the entry heading")
    parser.add_argument("--no-weather", action="store_true", help="skip the archive lookup")
    parser.add_argument("--no-places", action="store_true",
                        help="do not name any landmarks, even in --make-sample")
    parser.add_argument("--profile", help="path to a boat.toml (default: the usual search)")
    args = parser.parse_args()

    boat = load_profile(args.profile)
    tz = ZoneInfo(boat.timezone)

    if args.make_sample:
        make_sample(args.make_sample, tz=tz)
        return
    if not args.gpx:
        parser.error("give a GPX file, or --make-sample to write one")

    fixes = parse_gpx(args.gpx, tz=tz)
    if not fixes:
        parser.error(f"{args.gpx} holds no <trkpt> with a <time>")

    is_sample = SAMPLE_CREATOR in args.gpx.read_text(errors="ignore")[:600]
    places = [] if args.no_places else (DEMO_PLACES if is_sample else [])
    lph = args.lph if args.lph is not None else boat.vessel.cruise_burn_lph

    trip = analyse(fixes, places=places, with_weather=not args.no_weather, boat=boat)
    text = to_markdown(trip, lph=lph, title=args.title, places=places, sample=is_sample)

    if args.out:
        args.out.write_text(text + "\n")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()
