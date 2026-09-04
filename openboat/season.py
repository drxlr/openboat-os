#!/usr/bin/env python3
"""What is out there actually *like* in October — the question no seven-day forecast answers.

    python3 -m openboat.season                 # the table, on stdout
    python3 -m openboat.season --html out.html  # a page as well
    python3 -m openboat.season --json out.json  # the numbers, for anything else

`openboat.windows` answers "can we go out this weekend" and cannot see past about a week.
Every question one step further out — is it worth keeping the boat in the water through
winter, when does the season really start, is a week booked in a given month a gamble — is
a question about *climate*, and the same free Open-Meteo endpoints answer it if you point
them backwards. ERA5 goes back to 1940.

The one thing that makes the numbers here mean something: **the limits are not invented.**
`openboat.windows.passes()` is imported and run unchanged over ten years of history against
the profile's own limits, so "a good day in October" means exactly what "we can go out"
means everywhere else in this project. Change the limits in your `boat.toml` and every
number below moves with them.

⚠️ A BERTH CELL IS OFTEN A LAND CELL, and this is the finding to carry away from the file.
ERA5 is a ~28 km grid, and a marina a few miles from open water can sit in a cell that is
mostly land — see `docs/FORECAST.md`. That cell reads roughly half the wind the open sea
nearby is actually making, and its gusts read too high, because it is modelling turbulence
over hot ground rather than over water. Both errors point the same way: a climatology built
on the berth cell says the weather is better than it is. So this module always computes
**both points** from the profile — `forecast_point` (the planning number) and `berth` (kept
only to show the size of the error) — exactly why the profile carries two separate points
in the first place.

⚠️ TWO RECORD LENGTHS. The wind archive reaches back to 1940; the wave archive only to
2022-01-01. So the full verdict — wind AND gust AND sea AND rain — rests on a few years,
while the wind-only verdict rests on a full decade. Both are reported side by side: where
they disagree, the shorter sample is the one to distrust.

⚠️ ERA5 has no thunderstorm in it and no gust front. It describes a place, well, over
decades. It does not describe next Tuesday.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from . import windows
from .marine import ForecastUnavailable, Hour
from .profile import Profile, load as load_profile
from .trip import compass, era5_hours, era5_sea_temperature

# Ten full calendar years, ending on the last complete one — relative to today rather than
# a fixed date, so this stays a ten-year window whenever it is run. Ten Octobers is enough
# for a median to stop moving and few enough that the whole request is a few megabytes and
# a few seconds.
_last_complete_year = date.today().year - 1
WIND_FROM, WIND_TO = date(_last_complete_year - 9, 1, 1), date(_last_complete_year, 12, 31)

#: ERA5's grid step in degrees. Coarser than the forecast grid, which matters when the
#: berth and the forecast point are only a few miles apart — see the note in render().
GRID_DEG = 0.25

MONTHS = ["", "January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]
SHORT = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Same default as `windows.find()`. Under three hours is not a day out, it is a fuel stop.
MIN_WINDOW_H = 3
# And this is a day out: leave after breakfast, somewhere for lunch, home in the afternoon.
FULL_DAY_H = 6

# Knots. Cut where the decisions tend to be, not on Beaufort boundaries — override if your
# own limits sit somewhere else; these only shape the descriptive bands, not any verdict.
BANDS = [("calm", 0, 5), ("light", 5, 10), ("workable", 10, 15),
         ("over the limit", 15, 20), ("no", 20, 999)]

MORNING = range(7, 11)      # the flat part of a typical day
AFTERNOON = range(13, 18)   # when a sea breeze, where there is one, has finished building


@dataclass
class MonthStats:
    month: int
    name: str

    # Wind over daylight hours only — the boat is not out at 04:00, and including the
    # nocturnal calm would flatter every month by a knot or two.
    p10: float = 0.0
    p25: float = 0.0
    median: float = 0.0
    p75: float = 0.0
    p90: float = 0.0
    strongest: float = 0.0
    bands: dict = field(default_factory=dict)

    good_pct: float = 0.0        # >=3 h inside the full limits, over the wave record
    good_days: float = 0.0       # ... as days in an average month of this name
    full_day_pct: float = 0.0    # >=6 h — a real day out, not a dash between fronts
    full_days: float = 0.0
    median_window_h: float = 0.0 # the typical best run of the day, hours
    wind_only_pct: float = 0.0   # >=3 h ignoring sea state, over the full wind record

    diurnal: list = field(default_factory=list)   # 24 entries, local hour 0..23
    breeze_build_kn: float = 0.0
    morning_from: str = "--"
    afternoon_from: str = "--"
    constancy: float = 0.0       # 0 = wind from everywhere, 1 = always the same quarter

    # Share of daylight hours over each individual limit. NOT mutually exclusive — one
    # windy wet hour is counted in several — so these say what stops you, not how often.
    fail_wind_pct: float = 0.0
    fail_gust_pct: float = 0.0
    fail_sea_pct: float = 0.0
    fail_rain_pct: float = 0.0
    gust_factor: float = 0.0     # mean gust / mean wind: ~1.4 over water, >2 over land

    air_c: float = 0.0
    sea_c: float | None = None
    wet_days_pct: float = 0.0


# --- the arithmetic ---------------------------------------------------------------------

def _pct(values: list[float], q: float) -> float:
    """Nearest-rank percentile. No numpy in this project, and none needed for one column."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(round(q / 100 * len(ordered))) - 1))]


def _daylight(hour: Hour, limits: dict) -> bool:
    low, high = limits["daylight"]
    return low <= hour.time.hour < high


def _vector_mean(hours: list[Hour]) -> tuple[float, float]:
    """Speed-weighted mean direction the wind blows FROM, and how constant it is.

    Averaging 350° and 10° arithmetically gives 180°, which is the opposite of the truth,
    so directions are summed as vectors. Weighting by speed is the point: a sea-breeze
    climatology should be dominated by the hours the wind was actually doing something.
    Constancy is the resultant length over the scalar sum — 0.8 means the afternoon wind is
    the same wind every day, which is exactly the planning fact worth having.
    """
    if not hours:
        return 0.0, 0.0
    x = sum(h.wind_kn * math.sin(math.radians(h.wind_deg)) for h in hours)
    y = sum(h.wind_kn * math.cos(math.radians(h.wind_deg)) for h in hours)
    scalar = sum(h.wind_kn for h in hours)
    return math.degrees(math.atan2(x, y)) % 360, (math.hypot(x, y) / scalar if scalar else 0.0)


def _day_runs(hours: list[Hour], limits: dict) -> list[int]:
    """The longest unbroken run of acceptable hours in each calendar day, one per day.

    The run scan mirrors `windows.find()` rather than calling it, because that function
    fetches its own forecast. The *test* is `windows.passes()` itself, unmodified — that is
    the part that has to stay shared, so history and dashboard cannot drift apart.
    """
    by_day: dict[date, list[Hour]] = defaultdict(list)
    for hour in hours:
        by_day[hour.time.date()].append(hour)

    runs = []
    for day in by_day.values():
        best = run = 0
        for hour in sorted(day, key=lambda h: h.time):
            run = run + 1 if windows.passes(hour, limits) else 0
            best = max(best, run)
        runs.append(best)
    return runs


def climatology(lat: float, lon: float, boat: Profile | None = None,
                refresh: bool = False) -> list[MonthStats]:
    """One `MonthStats` per calendar month, from ten years of ERA5 at one point."""
    boat = boat or load_profile()
    limits = boat.limits.as_dict()

    try:
        hours = era5_hours(WIND_FROM, WIND_TO, lat, lon, boat=boat, waves=True, refresh=refresh)
    except ForecastUnavailable as exc:
        # Degrade with a clear stop, not a raw traceback — a climatology genuinely cannot
        # be computed with no archive data, so this is the one place in the module where
        # "degrade" means "fail cleanly" rather than "carry on with less".
        raise SystemExit(f"the archive did not answer — check the network ({exc})") from exc
    if not hours:
        raise SystemExit("the archive returned nothing — check the network")

    try:
        sst = era5_sea_temperature(WIND_FROM, WIND_TO, lat, lon, refresh=refresh)
    except ForecastUnavailable:
        # Sea temperature is a secondary figure here — wind and rain still answer most of
        # the question without it, so this degrades to "no sea reading" rather than losing
        # the whole climatology over one endpoint.
        sst = {}

    by_month: dict[int, list[Hour]] = defaultdict(list)
    for hour in hours:
        by_month[hour.time.month].append(hour)

    out = []
    for month in range(1, 13):
        every = by_month[month]
        day = [h for h in every if _daylight(h, limits)]
        speeds = [h.wind_kn for h in day]

        stats = MonthStats(month=month, name=MONTHS[month])
        stats.p10, stats.p25 = _pct(speeds, 10), _pct(speeds, 25)
        stats.median = _pct(speeds, 50)
        stats.p75, stats.p90 = _pct(speeds, 75), _pct(speeds, 90)
        stats.strongest = max(speeds) if speeds else 0.0
        stats.bands = {name: round(100 * sum(low <= s < high for s in speeds) / len(speeds), 1)
                       for name, low, high in BANDS} if speeds else {}

        # Two verdicts over two record lengths — see the module docstring.
        with_sea = [h for h in every if h.wave_m is not None]
        runs = _day_runs(with_sea, limits)
        years = max(1, len({h.time.year for h in with_sea})) if with_sea else 1
        length = len(runs) / years          # days in an average month of this name

        stats.good_pct = 100 * sum(r >= MIN_WINDOW_H for r in runs) / len(runs) if runs else 0
        stats.full_day_pct = 100 * sum(r >= FULL_DAY_H for r in runs) / len(runs) if runs else 0
        stats.median_window_h = statistics.median(runs) if runs else 0
        stats.good_days = stats.good_pct / 100 * length
        stats.full_days = stats.full_day_pct / 100 * length

        # Same test with the sea state removed, over the whole record. `windows.passes()`
        # already skips the wave check when there is no wave, so this needs no second rule.
        blind = [Hour(**{**vars(h), "wave_m": None, "wave_s": None, "wave_deg": None})
                 for h in every]
        long_runs = _day_runs(blind, limits)
        stats.wind_only_pct = (100 * sum(r >= MIN_WINDOW_H for r in long_runs)
                               / len(long_runs) if long_runs else 0)

        # The diurnal cycle, which is the whole reason for fetching in UTC and converting
        # with a real timezone: an hour of smear here would hide a sea breeze entirely.
        for local_hour in range(24):
            slot = [h for h in every if h.time.hour == local_hour]
            direction, _ = _vector_mean(slot)
            stats.diurnal.append({
                "hour": local_hour,
                "wind_kn": round(statistics.fmean(h.wind_kn for h in slot), 2) if slot else 0,
                "gust_kn": round(statistics.fmean(h.gust_kn for h in slot), 2) if slot else 0,
                "from_deg": round(direction),
                "from": compass(direction),
            })

        morning = [h for h in every if h.time.hour in MORNING]
        afternoon = [h for h in every if h.time.hour in AFTERNOON]
        morning_dir, _ = _vector_mean(morning)
        afternoon_dir, constancy = _vector_mean(afternoon)
        stats.morning_from, stats.afternoon_from = compass(morning_dir), compass(afternoon_dir)
        stats.constancy = round(constancy, 3)
        stats.breeze_build_kn = round(statistics.fmean(h.wind_kn for h in afternoon)
                                      - statistics.fmean(h.wind_kn for h in morning), 2)

        wet = [h for h in day if h.wave_m is not None]
        stats.fail_wind_pct = 100 * sum(h.wind_kn > limits["max_wind_kn"] for h in day) / len(day)
        stats.fail_gust_pct = 100 * sum(h.gust_kn > limits["max_gust_kn"] for h in day) / len(day)
        stats.fail_rain_pct = 100 * sum(h.rain_mm > limits["max_rain_mm"] for h in day) / len(day)
        stats.fail_sea_pct = (100 * sum(h.wave_m > limits["max_wave_m"] for h in wet) / len(wet)
                              if wet else 0.0)
        stats.gust_factor = round(statistics.fmean(h.gust_kn for h in day)
                                  / statistics.fmean(h.wind_kn for h in day), 2)

        stats.air_c = round(statistics.fmean(h.temp_c for h in day), 1) if day else 0.0
        month_sst = [v for stamp, v in sst.items() if int(stamp[5:7]) == month]
        stats.sea_c = round(statistics.fmean(month_sst), 1) if month_sst else None

        rain_by_day: dict[date, float] = defaultdict(float)
        for hour in every:
            rain_by_day[hour.time.date()] += hour.rain_mm
        stats.wet_days_pct = round(
            100 * sum(total >= 1.0 for total in rain_by_day.values()) / len(rain_by_day), 1)

        for name in ("p10", "p25", "median", "p75", "p90", "strongest", "good_pct",
                     "good_days", "full_day_pct", "full_days", "median_window_h",
                     "wind_only_pct", "fail_wind_pct", "fail_gust_pct",
                     "fail_sea_pct", "fail_rain_pct"):
            setattr(stats, name, round(getattr(stats, name), 1))
        out.append(stats)

    return out


def season(months: list[MonthStats], threshold: float = 70.0) -> list[int]:
    """The months worth owning a boat in: a proper day out on most days.

    Measured against `full_day_pct` — six hours inside the limits — not against the
    three-hour test. Three usable hours in a twelve-hour day is a month you can *sometimes*
    get out in; six is a month you can plan in. `threshold` is a judgement, and it is the
    only judgement in this file. Every other number is measured.
    """
    return [m.month for m in months if m.full_day_pct >= threshold]


# --- output ------------------------------------------------------------------------------

def table(offshore: list[MonthStats], berth: list[MonthStats], boat: Profile) -> str:
    limits = boat.limits.as_dict()
    point_name = boat.forecast_point_name or f"{boat.forecast_point[0]:.4f},{boat.forecast_point[1]:.4f}"
    berth_name = boat.berth_name or f"{boat.berth[0]:.4f},{boat.berth[1]:.4f}"
    lines = [
        f"{point_name} — ERA5 reanalysis, wind {WIND_FROM:%Y}–{WIND_TO:%Y}, "
        f"sea {max(WIND_FROM.year, 2022)}–{WIND_TO:%Y}",
        f"Planning point {boat.forecast_point[0]}°N {boat.forecast_point[1]}°E — the "
        f"profile's forecast point, chosen to be open water.",
        f"The berth cell ({berth_name}, {boat.berth[0]}/{boat.berth[1]}) is kept only to "
        f"show the size of the land-cell error.",
        f"A good day = {MIN_WINDOW_H} unbroken hours inside the profile's limits "
        f"(<={limits['max_wind_kn']:.0f} kn, gust <={limits['max_gust_kn']:.0f}, "
        f"sea <={limits['max_wave_m']} m, rain <={limits['max_rain_mm']} mm, "
        f"{limits['daylight'][0]}–{limits['daylight'][1]}h);",
        f"a full day = {FULL_DAY_H} such hours.",
        "",
        f"{'':<5}{'full days':>12}{'any window':>12}{'typ':>5}"
        f"{'   daylight wind, kn':<26}{'breeze':>8}{'pm':>5}{'air':>7}{'sea':>6}{'wet':>6}",
        f"{'':<5}{'≥6 h':>12}{'≥3 h':>12}{'win':>5}"
        f"{'p10':>6}{'med':>6}{'p90':>6}{'max':>7}{'build':>8}{'from':>5}"
        f"{'°C':>7}{'°C':>6}{'days':>6}",
        "-" * 94,
    ]

    for m in offshore:
        sea = f"{m.sea_c:.1f}" if m.sea_c is not None else "  --"
        lines.append(
            f"{SHORT[m.month]:<5}"
            f"{m.full_days:>6.1f} {m.full_day_pct:>4.0f}%"
            f"{m.good_days:>7.1f} {m.good_pct:>3.0f}%"
            f"{m.median_window_h:>4.0f}h"
            f"{m.p10:>6.1f}{m.median:>6.1f}{m.p90:>6.1f}{m.strongest:>7.0f}"
            f"{m.breeze_build_kn:>+8.1f}{m.afternoon_from:>5}"
            f"{m.air_c:>7.1f}{sea:>6}{m.wet_days_pct:>5.0f}%")

    good = season(offshore)
    lines += ["", f"The season, at 70 % of days with a full six-hour window: "
                  f"{', '.join(MONTHS[m] for m in good) if good else 'no month clears it'}."]
    best = max(offshore, key=lambda m: m.full_day_pct)
    worst = min(offshore, key=lambda m: m.full_day_pct)
    lines.append(f"Best {best.name} ({best.full_day_pct:.0f} %), "
                 f"worst {worst.name} ({worst.full_day_pct:.0f} %).")

    lines += ["", "The size of the land-cell error — same code, same limits, two points:",
              f"{'':<5}{'offshore ≥6 h':>15}{'berth ≥6 h':>13}"
              f"{'offshore med':>14}{'berth med':>11}"]
    for off, near in zip(offshore, berth):
        lines.append(f"{SHORT[off.month]:<5}{off.full_day_pct:>14.0f}%{near.full_day_pct:>12.0f}%"
                     f"{off.median:>13.1f}{near.median:>11.1f}")

    # Identical columns are not a bug and must not be left looking like one.
    #
    # The reanalysis grid is coarser than the forecast grid — a quarter of a degree against
    # an eighth — so two points that fall in DIFFERENT forecast cells can still share one
    # reanalysis cell, and then this table is the same series printed twice. That says
    # nothing about whether the land-cell effect exists at your harbour; it says the
    # climatology cannot resolve it. The live forecast still can, which is the reason the
    # profile keeps the two points apart in the first place. See docs/FORECAST.md.
    if all(abs(o.median - n.median) < 0.05 and abs(o.full_day_pct - n.full_day_pct) < 0.5
           for o, n in zip(offshore, berth)):
        lines += ["",
                  "⚠️ Both columns are the same because your berth and your forecast point "
                  "fall in the same",
                  f"   reanalysis cell — the archive grid is {GRID_DEG}°, about "
                  f"{GRID_DEG * 60:.0f} nm, and they are closer together than that.",
                  "   That is a limit of this table, not a finding about your harbour. To "
                  "see the effect here,",
                  "   the two points have to be in different cells; to see it in a live "
                  "forecast, they need only be",
                  "   in different forecast cells, which is a finer grid."]
    return "\n".join(lines)


#: Where the dashboard serves it from. Keep the two in step.
REPORT = Path("reports") / "season.html"


def main() -> None:
    from .profile import load as load_profile

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    # The default is reports/season.html because that is exactly where the dashboard
    # serves it from. It used to default to season.html in the working directory while the
    # server looked in reports/, so the Season tab could never show anything but its own
    # "not generated yet" placeholder — a whole feature quietly missing over a path.
    parser.add_argument("--html", nargs="?", const=str(REPORT), metavar="FILE",
                        default=str(REPORT),
                        help=f"write the report page (default {REPORT})")
    parser.add_argument("--no-html", action="store_true",
                        help="print the table only, write nothing")
    parser.add_argument("--json", metavar="FILE", help="write the raw numbers")
    parser.add_argument("--refresh", action="store_true", help="bypass the disk cache")
    parser.add_argument("--profile", help="path to a boat.toml (default: the usual search)")
    args = parser.parse_args()

    boat = load_profile(args.profile)
    offshore = climatology(*boat.forecast_point, boat=boat, refresh=args.refresh)
    berth = climatology(*boat.berth, boat=boat, refresh=args.refresh)
    print(table(offshore, berth, boat))

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"offshore": [asdict(m) for m in offshore],
             "berth": [asdict(m) for m in berth]}, indent=1))
        print(f"wrote {args.json}", file=sys.stderr)

    if args.html and not args.no_html:
        from . import season_report
        out = Path(args.html)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(season_report.render(offshore, berth, boat))
        print(f"wrote {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
