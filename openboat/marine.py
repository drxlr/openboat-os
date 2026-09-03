"""Wind and sea, hour by hour, from Open-Meteo. No API key, no account, no dependencies.

Two free endpoints, joined on the hour:

    api.open-meteo.com/v1/forecast        wind, gusts, direction, temperature, rain
    marine-api.open-meteo.com/v1/marine   wave height, period, direction

This is the bottom of the stack. `windows` and `route` are built on it, and both faces of
the project — the dashboard and the MCP server — reach the boat's weather through here.

Open-Meteo is free for non-commercial use and asks for no key. Read its terms before you
build a business on it: https://open-meteo.com/en/terms

## Two things worth knowing before you trust a number out of this file

**The hourly series starts at 00:00 today**, not at the current hour. By six in the evening,
three quarters of the first day is history. Every question this project asks is
forward-looking, so `forecast()` drops hours that are already over and computes "now" from
the offset the response carries rather than from the local machine's clock — the boat and
the sofa are often in different time zones. Pass `include_past=True` if you genuinely want
the raw series.

**The grid cell containing a marina is not the sea.** Open-Meteo's coastal grid is coarse
enough that every point within a few miles of a harbour can land in the same cell, and that
cell is influenced by the land in it. It under-reads the wind a boat will meet outside and
over-reads the gusts, and since a passage window is tested against wind *and* gusts, the two
errors do not cancel — good windows get thrown away on gusts that only exist ashore. That is
why a profile carries a `berth` and a separate `forecast_point`, and why nothing in this
project has a name meaning both. See `docs/FORECAST.md`.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .profile import Profile, load

WIND_URL = "https://api.open-meteo.com/v1/forecast"
WAVE_URL = "https://marine-api.open-meteo.com/v1/marine"

WIND_VARS = "wind_speed_10m,wind_gusts_10m,wind_direction_10m,temperature_2m,precipitation"
WAVE_VARS = "wave_height,wave_period,wave_direction"

_COMPASS = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")

#: Beaufort upper bounds in knots. Force 12 is open-ended.
_BEAUFORT = (1, 3, 6, 10, 16, 21, 27, 33, 40, 47, 55, 63)


class ForecastUnavailable(Exception):
    """The forecast service did not answer. Like the boat being offline, this is a normal
    condition on a boat — cache what you had, say how old it is, and carry on."""


@dataclass
class Hour:
    """One hour of forecast at one point. Wind in knots, waves in metres.

    `time` is naive local time **at the forecast point**, not on the machine asking.
    """

    time: datetime
    wind_kn: float
    gust_kn: float
    wind_deg: float
    temp_c: float
    rain_mm: float
    wave_m: float | None
    wave_s: float | None
    wave_deg: float | None

    @property
    def wind_name(self) -> str:
        """Compass point the wind is coming *from*."""
        return _COMPASS[int((self.wind_deg % 360) / 22.5 + 0.5) % 16]

    @property
    def beaufort(self) -> int:
        for force, upper in enumerate(_BEAUFORT):
            if self.wind_kn <= upper:
                return force
        return 12

    @property
    def gust_factor(self) -> float | None:
        """Gust over sustained wind. Around 1.4–1.5 is normal over open water; much above
        that is usually the signature of land in the forecast cell rather than of weather."""
        return None if self.wind_kn <= 0 else self.gust_kn / self.wind_kn


def _get(url: str, params: dict, timeout: float = 30.0) -> dict:
    query = urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(f"{url}?{query}", timeout=timeout) as response:
            return json.load(response)
    except Exception as exc:                                   # noqa: BLE001
        raise ForecastUnavailable(f"{url}: {exc}") from exc


def forecast(lat: float | None = None, lon: float | None = None, days: int = 7,
             include_past: bool = False, boat: Profile | None = None) -> list[Hour]:
    """Fetch both endpoints and join them hour by hour.

    With no coordinates, asks about the profile's `forecast_point` — the water the boat
    runs in, not the berth it sits in.

    Raises `ForecastUnavailable` rather than returning an empty list, so a caller can tell
    "no answer" from "answered, and the weather is bad".
    """
    boat = boat or load()
    if lat is None or lon is None:
        lat, lon = boat.forecast_point

    common = {"latitude": lat, "longitude": lon, "forecast_days": days, "timezone": "auto"}
    wind = _get(WIND_URL, {**common, "hourly": WIND_VARS, "wind_speed_unit": "kn"})
    hourly = wind["hourly"]

    # The marine endpoint has no data for inland, shadowed or very shallow points.
    # Degrade to wind-only rather than failing: a lake boat still wants a forecast.
    try:
        waves = _get(WAVE_URL, {**common, "hourly": WAVE_VARS})["hourly"]
    except ForecastUnavailable:
        waves = {}

    def wave(key: str, index: int) -> float | None:
        series = waves.get(key)
        if not series or index >= len(series):
            return None
        return series[index]

    hours = [
        Hour(time=datetime.fromisoformat(stamp),
             wind_kn=hourly["wind_speed_10m"][i],
             gust_kn=hourly["wind_gusts_10m"][i],
             wind_deg=hourly["wind_direction_10m"][i],
             temp_c=hourly["temperature_2m"][i],
             rain_mm=hourly["precipitation"][i],
             wave_m=wave("wave_height", i),
             wave_s=wave("wave_period", i),
             wave_deg=wave("wave_direction", i))
        for i, stamp in enumerate(hourly["time"])
    ]

    if include_past:
        return hours

    offset = wind.get("utc_offset_seconds", 0)
    now_at_point = datetime.now(timezone.utc) + timedelta(seconds=offset)
    # The hour in progress is still usable, so cut at the top of the current hour.
    cutoff = now_at_point.replace(tzinfo=None, minute=0, second=0, microsecond=0)
    return [h for h in hours if h.time >= cutoff]
