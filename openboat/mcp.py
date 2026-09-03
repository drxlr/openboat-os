#!/usr/bin/env python3
"""OpenBoat as an MCP server — how an AI assistant reaches the boat.

JSON-RPC over stdin/stdout, stdlib only, no install step. Register it once and any Claude
session, on any machine, can ask about the boat, the weather and the passage:

    claude mcp add openboat -- python3 -m openboat.mcp

Five tools, all read-only. Nothing here sends, pays, books or steers, and that is a
property of the design rather than a feature not yet written — see `docs/DISCLAIMER.md`.

The interesting one is `plan_route`. A forecast for the harbour is not a forecast for the
passage: a leg leaving at 08:00 in a flat calm can arrive at 13:00 in a sea breeze, so each
leg is sampled at its own midpoint and its own hour. That is the question a chart plotter
does not answer and an assistant can.

`boat_state` is built to fail well. A boat is offline most of the year, so being unable to
reach it is an ordinary answer given in a sentence, not an error.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime

from . import boat, windows
from .marine import forecast
from .profile import load
from .route import Waypoint, plan


def _compass(degrees) -> str:
    points = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
              "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")
    if degrees is None:
        return "?"
    return points[int((degrees % 360) / 22.5 + 0.5) % 16]

PROTOCOL = "2025-06-18"

TOOLS = [
    {
        "name": "marine_forecast",
        "description": "Wind, gusts and sea state hour by hour for a point at sea. "
                       "Defaults to the boat profile's forecast point. Free Open-Meteo data, no key.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "lat": {"type": "number", "description": "Latitude. Defaults to the profile's forecast point"},
                "lon": {"type": "number", "description": "Longitude. Defaults to the profile's forecast point"},
                "hours": {"type": "integer", "description": "How many hours ahead, max 168"},
            },
        },
    },
    {
        "name": "passage_window",
        "description": "When it is actually good enough to go out: contiguous runs of hours "
                       "inside the skipper's wind, gust, sea and rain limits, longest first.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "lat": {"type": "number"},
                "lon": {"type": "number"},
                "days": {"type": "integer", "description": "Forecast horizon, 1-7"},
                "min_hours": {"type": "integer", "description": "Shortest useful window"},
                "max_wind_kn": {"type": "number"},
                "max_gust_kn": {"type": "number"},
                "max_wave_m": {"type": "number"},
            },
        },
    },
    {
        "name": "plan_route",
        "description": "Distance, bearing, ETA and fuel for a route, plus the weather each leg "
                       "will meet at the hour it is under way. Does NOT check for land or depth.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "waypoints": {
                    "type": "array",
                    "description": "Two or more points in order",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "lat": {"type": "number"},
                            "lon": {"type": "number"},
                        },
                        "required": ["lat", "lon"],
                    },
                },
                "speed_kn": {"type": "number", "description": "Planning speed, default 20"},
                "depart": {"type": "string", "description": "ISO local time, default next hour"},
                "litres_per_hour": {"type": "number", "description": "Fuel burn, see docs"},
            },
            "required": ["waypoints"],
        },
    },
    {
        "name": "boat_state",
        "description": "Live position, speed, heading and depth from the boat's Signal K server. "
                       "Reports plainly when the boat is offline, which is most of the time.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "ais_targets",
        "description": "Other vessels the boat currently sees on AIS. Needs an AIS receiver aboard.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer"}},
        },
    },
]


# --- tool implementations -------------------------------------------------------------

def tool_marine_forecast(lat=None, lon=None, hours=48):
    boat_profile = load()
    lat = boat_profile.forecast_point[0] if lat is None else lat
    lon = boat_profile.forecast_point[1] if lon is None else lon
    days = max(1, min(7, -(-int(hours) // 24)))

    lines = [f"Forecast for {lat:.4f}, {lon:.4f} — wind in knots, sea in metres", ""]
    for hour in forecast(lat, lon, days)[:int(hours)]:
        wave = f"{hour.wave_m:.1f} m/{hour.wave_s:.0f}s" if hour.wave_m is not None else "--"
        lines.append(f"{hour.time:%a %d.%m %H:%M}  {hour.wind_kn:5.1f} kn "
                     f"gust {hour.gust_kn:5.1f}  {hour.wind_name:3} Bft{hour.beaufort}  "
                     f"sea {wave}  {hour.temp_c:4.1f}°C")
    return "\n".join(lines)


def tool_passage_window(lat=None, lon=None, days=7, min_hours=3, **limits):
    limits = {k: v for k, v in limits.items() if v is not None}
    found = windows.find(lat, lon, days=int(days), min_hours=int(min_hours), limits=limits)
    if not found:
        return (f"No window of {min_hours} h or more in the next {days} days "
                f"inside the limits {load().limits.as_dict() | limits}.")
    header = f"{len(found)} window(s), longest first. Limits: {load().limits.as_dict() | limits}"
    return "\n".join([header, ""] + [f"  {w}" for w in found])


def tool_plan_route(waypoints, speed_kn=None, depart=None, litres_per_hour=None):
    points = [Waypoint(w.get("name") or f"WP{i+1}", w["lat"], w["lon"])
              for i, w in enumerate(waypoints)]
    when = datetime.fromisoformat(depart) if depart else None
    passage = plan(points,
                   speed_kn=None if speed_kn is None else float(speed_kn), depart=when,
                   litres_per_hour=None if litres_per_hour is None else float(litres_per_hour))

    lines = [f"{passage.distance_nm:.1f} nm at {passage.speed_kn:.0f} kn "
             f"= {passage.hours:.1f} h" +
             (f", {passage.fuel_litres:.0f} L fuel" if passage.litres_per_hour else ""),
             "⚠️ No land, depth or restricted-area check. Verify on a chart.", ""]
    for leg in passage.legs:
        weather = leg.weather
        wx = (f"{weather.wind_kn:.0f} kn {weather.wind_name} Bft{weather.beaufort}"
              + (f", sea {weather.wave_m:.1f} m" if weather.wave_m is not None else "")
              ) if weather else "no forecast"
        lines.append(f"{leg.frm.name} → {leg.to.name}: {leg.distance_nm:.1f} nm "
                     f"{leg.bearing_deg:.0f}° {leg.bearing_name}, "
                     f"{leg.depart:%d.%m %H:%M}–{leg.arrive:%H:%M} — {wx}")
    return "\n".join(lines)


def tool_boat_state():
    try:
        state = boat.state()
    except boat.Offline as exc:
        return (f"Boat offline — {exc}\n"
                "This is the normal case: a boat is in its berth most of the year. It "
                "means nobody could reach the Signal K server, not that anything is wrong.")
    if state["lat"] is None:
        return f"Signal K is up but reports no position yet. Raw: {state}"
    return (f"{state['name'] or 'the boat'} at {state['lat']:.5f}, {state['lon']:.5f}\n"
            f"SOG {state['sog_kn']} kn, COG {state['cog_deg']}° "
            f"({_compass(state['cog_deg'])}), heading {state['heading_deg']}°, "
            f"depth {state['depth_m']} m")


def tool_ais_targets(limit=20):
    try:
        targets = boat.ais(int(limit))
    except boat.Offline as exc:
        return f"Boat offline — {exc}"
    if not targets:
        return "Signal K is up but sees no AIS targets. Is there an AIS receiver aboard?"
    return "\n".join(f"{t['name']} ({t['mmsi']}) {t['lat']:.4f},{t['lon']:.4f} "
                     f"{t['sog_kn']} kn" for t in targets)


HANDLERS = {
    "marine_forecast": tool_marine_forecast,
    "passage_window": tool_passage_window,
    "plan_route": tool_plan_route,
    "boat_state": tool_boat_state,
    "ais_targets": tool_ais_targets,
}


# --- JSON-RPC plumbing ----------------------------------------------------------------

def handle(request: dict) -> dict | None:
    method = request.get("method")
    request_id = request.get("id")

    if method == "initialize":
        wanted = request.get("params", {}).get("protocolVersion", PROTOCOL)
        return reply(request_id, {
            "protocolVersion": wanted,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "openboat", "version": "0.1.0"},
        })

    if method == "tools/list":
        return reply(request_id, {"tools": TOOLS})

    if method == "tools/call":
        params = request.get("params", {})
        handler = HANDLERS.get(params.get("name"))
        if handler is None:
            return error(request_id, -32602, f"unknown tool {params.get('name')!r}")
        try:
            text = handler(**(params.get("arguments") or {}))
        except Exception as exc:  # surface the failure to Claude, never to stdout
            return reply(request_id, {
                "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                "isError": True,
            })
        return reply(request_id, {"content": [{"type": "text", "text": text}]})

    if request_id is None:
        return None  # a notification; nothing to answer

    return error(request_id, -32601, f"unknown method {method!r}")


def reply(request_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def error(request_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle(request)
        if response is not None:
            print(json.dumps(response), flush=True)


if __name__ == "__main__":
    main()
