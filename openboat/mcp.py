#!/usr/bin/env python3
"""OpenBoat as an MCP server — how an AI assistant reaches the boat.

JSON-RPC over stdin/stdout, stdlib only, no install step. Register it once and any Claude
session, on any machine, can ask about the boat, the weather and the passage:

    claude mcp add openboat -- python3 -m openboat.mcp

Nine tools. Eight are read-only; the ninth, `log_check`, appends a line to the owner's
maintenance log and can do nothing else. Nothing here sends, pays, books or steers, and
that is a property of the design rather than a feature not yet written — there is no route
from this module into `openboat/control/`, and `tests/test_control_gate.py` fails the build
if one appears. See `docs/DISCLAIMER.md`.

`boat_docs` is the one that changes what an assistant can be. Without it a model answers
about the make and model in general; with it, it answers about *this* hull — the riser that
was found clogged, the fitting that is actually where the photo shows it, the invoice that
says what the yard really replaced — quoting the owner's own files with the line each came
from.

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

from . import boat, knowledge, ledger, logbook, notes, papers, windows
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

#: MCP tool annotations. Without them a client has to assume the worst, and ChatGPT does
#: exactly that — it labelled every one of these PUBLIC WRITE, OPEN WORLD and DESTRUCTIVE,
#: including `boat_state`, which does nothing but read a gauge. That is not a cosmetic
#: problem: a client that believes reading the coolant temperature might destroy something
#: will either interrupt the user to confirm it or decline to use it.
#:
#: `openWorldHint` is true only where the tool really does reach the open internet — the
#: forecast and the AIS feed. Everything else stays inside the boat.
READ_ONLY = {"readOnlyHint": True, "destructiveHint": False,
             "idempotentHint": True, "openWorldHint": False}
READ_ONLY_ONLINE = {**READ_ONLY, "openWorldHint": True}

#: The one tool that writes. It appends a line to a maintenance log and can do nothing
#: else — no edit, no delete — so it is not read-only and not destructive either, and it is
#: not idempotent because logging the same check twice records two checks.
APPEND_ONLY = {"readOnlyHint": False, "destructiveHint": False,
               "idempotentHint": False, "openWorldHint": False}

ANNOTATIONS = {
    "boat_docs": READ_ONLY, "boat_specs": READ_ONLY, "checks": READ_ONLY,
    "boat_papers": READ_ONLY, "boat_costs": READ_ONLY, "boat_state": READ_ONLY,
    "plan_route": READ_ONLY,
    "marine_forecast": READ_ONLY_ONLINE, "passage_window": READ_ONLY_ONLINE,
    "ais_targets": READ_ONLY_ONLINE,
    "log_check": APPEND_ONLY,
    "add_note": APPEND_ONLY,
}


def annotate(tools: list) -> list:
    """Attach the annotations, and refuse to ship a tool nobody has classified.

    The failure this guards against is a new tool being added and quietly inheriting the
    worst-case assumption, which is how `boat_state` came to be marked destructive.
    """
    for tool in tools:
        hints = ANNOTATIONS.get(tool["name"])
        if hints is None:
            raise RuntimeError(
                f"tool {tool['name']!r} has no entry in ANNOTATIONS. Say whether it reads "
                f"or writes; a client that is not told assumes it destroys things.")
        tool["annotations"] = {"title": tool["name"].replace("_", " "), **hints}
    return tools


TOOLS = [
    {
        "name": "boat_docs",
        "description": "Search this boat's own papers — the survey, the manual, the yard "
                       "invoices, the owner's working notes — and return the matching "
                       "passages with the file and line they came from. Use this BEFORE "
                       "answering anything specific to the vessel: its history, its "
                       "faults, what has already been replaced, which fitting is where. "
                       "The documents are the authority; quote them rather than "
                       "generalising from the make and model. Searched by word in German "
                       "and English both. Returns nothing if the owner has pointed at no "
                       "documents, which is not an error.",
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string", "description": "what you want to know"},
            "limit": {"type": "integer", "description": "passages to return, default 5"},
        }, "required": ["query"]},
    },
    {
        "name": "boat_specs",
        "description": "The vessel's measured facts as its owner recorded them — length, "
                       "beam, draft, displacement, engine, berth — each with the source it "
                       "came from. A field that is absent is absent on purpose: nobody has "
                       "measured it, and this project would rather say so than guess. "
                       "Never fill such a gap from the make and model; say it is not "
                       "recorded.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "log_check",
        "description": "Record that something on the boat was checked, and what it "
                       "showed. Writes one line to the owner's check log together with "
                       "whatever the boat is reading at this moment, so the entry still "
                       "means something next season. Use it when the owner tells you they "
                       "have looked at something, or when you have walked them through an "
                       "inspection. Ask before logging: it is their maintenance record, "
                       "and it is append-only — nothing can edit or remove a line later.",
        "inputSchema": {"type": "object", "properties": {
            "what": {"type": "string", "description": "what was checked"},
            "found": {"type": "string", "description": "what it showed"},
            "verdict": {"type": "string", "enum": list(logbook.VERDICTS),
                        "description": "ok, watch, act, or noted"},
            "refs": {"type": "array", "items": {"type": "string"},
                     "description": "documents or photos it relates to"},
        }, "required": ["what"]},
    },
    {
        "name": "checks",
        "description": "Read the check log back — what was inspected, when, what it "
                       "showed, and the readings at the time. Use it to answer 'when was "
                       "this last looked at' before recommending a service interval.",
        "inputSchema": {"type": "object", "properties": {
            "what": {"type": "string", "description": "filter by subject"},
            "since": {"type": "string", "description": "ISO date, e.g. 2026-01-01"},
            "limit": {"type": "integer", "description": "most recent N, default 20"},
        }},
    },
    {
        "name": "boat_papers",
        "description": "The vessel's documents that matter because of a date — "
                       "registration, insurance, radio licence, survey, flare expiry — "
                       "with how many days each has left. Check this before advising "
                       "anything about a passage, a charter, a sale or a border "
                       "crossing. Says 'undated' rather than guessing when no expiry is "
                       "recorded.",
        "inputSchema": {"type": "object", "properties": {
            "within_days": {"type": "integer",
                            "description": "only those lapsing within N days"}}},
    },
    {
        "name": "boat_costs",
        "description": "What the boat has cost: totals split into fixed (berth, "
                       "insurance, papers) and variable (fuel, service, parts), per "
                       "currency, and the cost per engine hour when enough engine-hour "
                       "readings exist to compute it honestly. The purchase price is "
                       "recorded but kept out of the running total. Nothing is converted "
                       "between currencies.",
        "inputSchema": {"type": "object", "properties": {
            "year": {"type": "string", "description": "e.g. 2026; omit for all time"}}},
    },
    {
        "name": "add_note",
        "description": "Write down something learned about this boat. Appends to the "
                       "companion's own notes file — it CANNOT edit or delete the boat's "
                       "documents, and must not be described to the user as if it could. "
                       "Use it for something established during a job that would "
                       "otherwise be lost: a measurement taken, a part number read off a "
                       "casting, what a mechanic actually said. Do not use it to record "
                       "your own inference as fact; write what was observed and by whom. "
                       "Ask the owner before writing — it is their record, and nothing "
                       "here can be unwritten.",
        "inputSchema": {"type": "object", "properties": {
            "text": {"type": "string", "description": "the note, in plain words"},
        }, "required": ["text"]},
    },
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

def tool_add_note(text):
    written = notes.add(text, by="assistant")
    return (f"Noted at {written['at']}.\n\nThis went into the companion's notes file, "
            f"which is searchable but is NOT one of the boat's documents and is marked "
            f"unverified. If it matters, the owner should move it into the real file.")


def tool_boat_papers(within_days=None):
    found = (papers.expiring(within=int(within_days)) if within_days is not None
             else papers.load())
    if not found:
        return ("No papers listed for this boat. They go in the profile as [[papers]] "
                "entries with a name and an expiry date. Do not assume a boat's "
                "registration or insurance is current because nothing says otherwise.")
    lines = []
    for paper in found:
        left = paper.days_left
        when = ("no expiry recorded" if left is None
                else f"expires {paper.expires}, {left} days left" if left >= 0
                else f"EXPIRED {paper.expires}, {-left} days ago")
        lines.append(f"{paper.name} ({paper.kind or 'document'}) — {when}"
                     + (f" — {paper.note}" if paper.note else ""))
    return "\n".join(lines)


def tool_boat_costs(year=""):
    report = ledger.summary(year=year)
    if not report["entries"]:
        return "Nothing recorded in the cost ledger yet."
    out = [f"{report['entries']} entries, {report['year']}"
           + (f", {report['engine_hours']} engine hours over the period"
              if report["engine_hours"] else "")]
    for currency, bucket in report["currencies"].items():
        out.append(f"\n{currency}: running {bucket['running']:,.0f} "
                   f"(fixed {bucket['fixed']:,.0f}, variable {bucket['variable']:,.0f})")
        if bucket["purchase"]:
            out.append(f"  purchase {bucket['purchase']:,.0f}, deliberately outside the "
                       "running total")
        if bucket["per_engine_hour"] is not None:
            out.append(f"  cost per engine hour: {bucket['per_engine_hour']:,.2f} {currency}")
        else:
            out.append("  cost per engine hour: not computable — needs at least two "
                       "engine-hour readings. Do not estimate one.")
        out.append("  " + ", ".join(f"{k} {v:,.0f}"
                                    for k, v in bucket["by_category"].items()))
    return "\n".join(out)


def tool_boat_docs(query, limit=5):
    library = knowledge.load()
    if not library.paths:
        return ("This boat has no documents pointed at. The owner can add them under "
                "[knowledge] docs in their profile. Do not substitute general knowledge "
                "about the make and model for the boat's own papers — say they are absent.")
    hits = library.search(query, limit=int(limit))
    if not hits:
        return (f"Nothing in {len(library.paths)} document(s) matches {query!r}. "
                "The search is by word, so try the words the file itself would use.")
    out = [f"{len(hits)} passage(s) from this boat's own papers. Quote them; do not "
           "paraphrase a measurement.\n"]
    for hit in hits:
        out.append(f"--- {hit.heading}  [{hit.where}]\n{hit.text}\n")
    return "\n".join(out)


def tool_boat_specs():
    boat_profile = load()
    vessel = boat_profile.as_dict()["vessel"]
    lines = [f"{vessel.get('name') or 'the boat'} — {vessel.get('kind') or 'vessel'}"]
    for key, value in vessel.items():
        if key in ("name", "kind") or key.endswith("_source") or value in (None, "", 0):
            continue
        source = boat_profile.source_of(key)
        lines.append(f"  {key}: {value}" + (f"   [{source}]" if source else ""))
    missing = boat_profile.unsourced()
    if missing:
        lines.append("\nNot recorded, and therefore not known: " + ", ".join(missing)
                     + ". Do not supply these from the make and model.")
    berth = boat_profile.as_dict()["berth"]
    if berth.get("name"):
        lines.append(f"\nBerth: {berth['name']} ({berth['lat']:.4f}, {berth['lon']:.4f})")
    return "\n".join(lines)


def tool_log_check(what, found="", verdict="noted", refs=None):
    entry = logbook.record(what=what, found=found, verdict=verdict,
                           by="assistant", refs=refs or [])
    live = ", ".join(f"{k} {v}" for k, v in list(entry.readings.items())[:6]) or "none"
    return (f"Logged: {entry.what} — {entry.verdict}"
            f"{' — ' + entry.found if entry.found else ''}\n"
            f"at {entry.at}, readings captured: {live}")


def tool_checks(what="", since="", limit=20):
    rows = logbook.entries(what=what, since=since, limit=int(limit))
    if not rows:
        return "Nothing recorded" + (f" matching {what!r}" if what else "") + " yet."
    out = []
    for row in rows:
        live = ", ".join(f"{k} {v}" for k, v in list((row.get("readings") or {}).items())[:4])
        out.append(f"{row['at'][:16]}  [{row['verdict']}]  {row['what']}"
                   f"{' — ' + row['found'] if row.get('found') else ''}"
                   f"{'  (' + live + ')' if live else ''}")
    return "\n".join(out)


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


TOOLS = annotate(TOOLS)

HANDLERS = {
    "boat_docs": lambda **kw: tool_boat_docs(**kw),
    "boat_specs": lambda **kw: tool_boat_specs(**kw),
    "log_check": lambda **kw: tool_log_check(**kw),
    "checks": lambda **kw: tool_checks(**kw),
    "boat_papers": lambda **kw: tool_boat_papers(**kw),
    "boat_costs": lambda **kw: tool_boat_costs(**kw),
    "add_note": lambda **kw: tool_add_note(**kw),
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
