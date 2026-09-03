"""When can we go out.

The forecast tells you the weather. This tells you the answer to the only question that
actually gets asked on a Thursday evening: which hours between now and Sunday are good
enough to take the boat out, and how long do they run for.

A window is a contiguous run of hours that all pass the skipper's limits. Longest first,
because the question is usually "have we got an afternoon", not "is 14:00 alright".

The limits come from the boat profile and belong to the skipper, not the boat. They are
comfort limits: the cost of a missed nice day is nothing, and the cost of a frightened
passenger is the rest of the season.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .marine import Hour, forecast
from .profile import Profile, load


@dataclass
class Window:
    start: datetime
    end: datetime
    hours: list[Hour]

    @property
    def length_h(self) -> int:
        return len(self.hours)

    @property
    def worst_wind_kn(self) -> float:
        return max(h.wind_kn for h in self.hours)

    @property
    def worst_gust_kn(self) -> float:
        return max(h.gust_kn for h in self.hours)

    @property
    def worst_wave_m(self) -> float | None:
        waves = [h.wave_m for h in self.hours if h.wave_m is not None]
        return max(waves) if waves else None

    def __str__(self) -> str:
        wave = f", sea {self.worst_wave_m:.1f} m" if self.worst_wave_m is not None else ""
        return (f"{self.start:%a %d.%m %H:%M}-{self.end:%H:%M} "
                f"({self.length_h} h, up to {self.worst_wind_kn:.0f} kn "
                f"gusting {self.worst_gust_kn:.0f}{wave})")


def passes(hour: Hour, limits: dict) -> bool:
    """One hour against one set of limits.

    Wave height is only tested when there *is* a wave figure. A missing sea state is not a
    calm sea — it means the marine model has no data for this point — so it must not
    silently turn into a pass on a day the wind alone would have failed.
    """
    low, high = limits["daylight"]
    if not low <= hour.time.hour < high:
        return False
    if hour.wind_kn > limits["max_wind_kn"] or hour.gust_kn > limits["max_gust_kn"]:
        return False
    if hour.rain_mm > limits["max_rain_mm"]:
        return False
    if hour.wave_m is not None and hour.wave_m > limits["max_wave_m"]:
        return False
    return True


def find(lat: float | None = None, lon: float | None = None, days: int = 7,
         min_hours: int = 3, limits: dict | None = None,
         boat: Profile | None = None) -> list[Window]:
    """Contiguous runs of acceptable hours, longest first.

    `limits` overrides individual keys of the profile's limits, so a caller can ask "and
    what if I accepted 20 knots" without editing anything.
    """
    boat = boat or load()
    limits = {**boat.limits.as_dict(), **(limits or {})}

    windows: list[Window] = []
    run: list[Hour] = []

    for hour in forecast(lat, lon, days=days, boat=boat):
        if passes(hour, limits):
            run.append(hour)
            continue
        if len(run) >= min_hours:
            windows.append(Window(run[0].time, run[-1].time, list(run)))
        run = []

    if len(run) >= min_hours:
        windows.append(Window(run[0].time, run[-1].time, list(run)))

    return sorted(windows, key=lambda w: (-w.length_h, w.start))
