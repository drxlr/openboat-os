#!/usr/bin/env python3
"""OpenBoat as an MCP server — how an AI assistant reaches the boat.

JSON-RPC over stdin/stdout, stdlib only, no install step. Register it once and any Claude
session, on any machine, can ask about the boat, the weather and the passage:

    claude mcp add openboat -- python3 -m openboat.mcp

Most of the tools read. Five write, and each writes to a file of the companion's own:
`log_check` and `add_note` append a line to the maintenance log or the notes; `add_document`
records a transcription; `add_link` and `fetch_document` put something into the boat's
*inbox*, which is not the boat's documents and is never searched or quoted — a person moves
things from there into the library, and `docs/INTAKE.md` says why that step exists. Nothing
here sends, pays, books or steers, and that is a property of the design rather than a
feature not yet written — there is no route from this module into `openboat/control/`, and
`tests/test_control_gate.py` fails the build if one appears. See `docs/DISCLAIMER.md`.

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

import base64
import contextvars
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from datetime import datetime

from . import boat, documents, intake, knowledge, ledger, logbook, notes, papers, windows
from . import engine_health, engine_hours, maintenance, snag
from .engine import DEFAULT_DB, connect as connect_engine_data
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

#: Which assistant is on the other end of this request.
#:
#: Over a pipe there is exactly one and it is whoever launched the process. Over HTTP there
#: can be several — `OPENBOAT_MCP_TOKENS` gives each its own token — and the name behind the
#: token is set here for the duration of the request. It matters because everything an
#: assistant puts into the boat's inbox is stamped with it, and "who brought this document"
#: is the first question a person deciding whether to accept it will ask.
#:
#: A `ContextVar` rather than a global: `mcp_http` serves on a thread per connection, and a
#: plain module attribute would have one request's caller name land on another's write.
CALLER: contextvars.ContextVar[str] = contextvars.ContextVar("caller", default="assistant")


def caller() -> str:
    return CALLER.get() or "assistant"

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

#: A write that also reaches the open internet: `fetch_document` opens a connection to a URL
#: somebody handed it. Both halves have to be declared — a client told only "it writes"
#: cannot warn that the boat is about to talk to a stranger, and one told only "it is
#: online" will treat it as a search.
APPEND_ONLY_ONLINE = {**APPEND_ONLY, "openWorldHint": True}

ANNOTATIONS = {
    "boat_docs": READ_ONLY, "boat_specs": READ_ONLY, "checks": READ_ONLY,
    "boat_papers": READ_ONLY, "boat_costs": READ_ONLY, "boat_state": READ_ONLY,
    "plan_route": READ_ONLY,
    "marine_forecast": READ_ONLY_ONLINE, "passage_window": READ_ONLY_ONLINE,
    "ais_targets": READ_ONLY_ONLINE,
    "log_check": APPEND_ONLY,
    "add_note": APPEND_ONLY,
    "add_document": APPEND_ONLY,
    "boat_files": READ_ONLY,
    "boat_tasks": READ_ONLY,
    "engine_data": READ_ONLY,
    "snag_photo": READ_ONLY,
    "add_link": APPEND_ONLY,
    "fetch_document": APPEND_ONLY_ONLINE,
    "boat_inbox": READ_ONLY,
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
                       "recorded. Answers about the boat this companion is pinned to "
                       "unless `boat` names another one on the same machine; the answer "
                       "always says which boat it describes.",
        "inputSchema": {"type": "object", "properties": {
            "boat": {"type": "string", "description":
                     "Key of another boat on this machine, e.g. 'demo-boat'. Omit for the "
                     "boat this companion is pinned to."},
        }},
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
        "name": "add_document",
        "description": "Take in a document the owner has photographed — registration, "
                       "insurance certificate, a CE or engine plate, an invoice, a "
                       "handwritten quote — by recording what you can READ on it.\n\n"
                       "TRANSCRIBE, DO NOT INTERPRET. Hull numbers, engine serials, MMSI, "
                       "policy numbers and dates are strings where one wrong character is "
                       "worse than a blank. Copy exactly what is visible. Where a "
                       "character cannot be made out write '?' and say so in `unclear` — "
                       "never complete it from context, from the make and model, or from "
                       "another document. If the photograph is too poor to read a field, "
                       "say that instead of producing a plausible value.\n\n"
                       "This is stored as a transcription, not as a source: it does not "
                       "modify the boat's own documents, and the owner promotes it by "
                       "hand once they have checked it against the photograph.",
        "inputSchema": {"type": "object", "properties": {
            "title": {"type": "string", "description": "what the document is"},
            "read": {"type": "string",
                     "description": "the text as it appears, field by field"},
            "source": {"type": "string",
                       "description": "what it was read from — a filename, or e.g. "
                                      "'photo sent in chat, 4 September 2026'"},
            "kind": {"type": "string", "enum": list(documents.KINDS)},
            "unclear": {"type": "string",
                        "description": "anything illegible or uncertain. Empty means you "
                                       "are claiming every character was legible."},
        }, "required": ["title", "read"]},
    },
    {
        "name": "boat_files",
        "description": "List the photographs and PDFs stored with this boat's documents — "
                       "names, paths and sizes. Use it to find an existing image to refer "
                       "to, or to check whether something has already been photographed. "
                       "It lists files; it cannot open, move or delete them.",
        "inputSchema": {"type": "object", "properties": {
            "contains": {"type": "string", "description": "filter by filename"}}},
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
        "name": "boat_tasks",
        "description": "What the boat is owed right now: the open faults somebody "
                       "photographed at the pontoon (snags, each with a status of open, "
                       "review or fixed, who filed it, where on the boat, and every "
                       "follow-up written since) and the service items due, counted in "
                       "the engine's own running hours and calendar days against the "
                       "intervals in the owner's profile. Use it for any question about "
                       "the status of a task, a fault, a snag, what is outstanding, what "
                       "is in review, what was fixed, or when a service is due. A "
                       "service item with no verdict is one that has never been "
                       "recorded — say so rather than guessing a date. Read-only: to "
                       "change a status the owner uses the console.",
        "inputSchema": {"type": "object", "properties": {
            "boat": {"type": "string",
                     "description": "boat key when this machine serves several boats; "
                                    "omit for the boat this server was started on"},
            "status": {"type": "string",
                       "description": "filter snags: open | review | fixed | all "
                                      "(default open, which includes review)"},
            "what": {"type": "string",
                     "description": "a word to filter both lists by"},
        }},
    },
    {
        "name": "snag_photo",
        "description": "Show the photograph(s) somebody took of a snag. `boat_tasks` lists "
                       "each snag's photo file names; pass one as `name`, or pass the "
                       "snag's timestamp as `when` to get every photo of that snag (up to "
                       "four). Returns the pictures themselves, downscaled, so you can look "
                       "at the fault, the part, the label, the damage. Use it whenever the "
                       "owner asks what a snag looks like, what the photo shows, or to read "
                       "a model number off a plate they photographed.",
        "inputSchema": {"type": "object", "properties": {
            "boat": {"type": "string", "description": "boat key, as for boat_tasks"},
            "name": {"type": "string", "description": "one photo file name from boat_tasks"},
            "when": {"type": "string",
                     "description": "a snag's timestamp from boat_tasks, for all its photos"},
        }},
    },
    {
        "name": "engine_data",
        "description": "The engine as the log has measured it: running hours and how "
                       "much of the time the log was actually watching, outings, the "
                       "most recent runs, and the health findings — cooling trend per "
                       "rpm band against sea temperature, oil pressure, resting battery "
                       "voltage — each marked good, watch, bad or unknown with the "
                       "evidence behind it. Use it for anything about engine data, "
                       "engine hours, overheating, oil pressure, the battery, or how the "
                       "engine has been behaving. Says when the log is too short to "
                       "judge instead of judging anyway.",
        "inputSchema": {"type": "object", "properties": {
            "days": {"type": "integer",
                     "description": "only the last N days of the log; default all"},
        }},
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
    {
        "name": "add_link",
        "description": "Suggest a web page the owner might want — a manual on the "
                       "manufacturer's site, a service bulletin, a parts diagram. The link "
                       "is written into the boat's INBOX and is NOT opened, now or ever, by "
                       "anything here. It is not one of the boat's documents and nothing "
                       "will answer from it until the owner reads it and accepts it in the "
                       "console. Say plainly to the owner that you have put a suggestion in "
                       "their inbox rather than implying the boat now knows what is on the "
                       "page. Use this for anything that is not a PDF.",
        "inputSchema": {"type": "object", "properties": {
            "url": {"type": "string", "description": "the http(s) address"},
            "title": {"type": "string", "description": "what it is, in a few words"},
            "reason": {"type": "string",
                       "description": "why this boat wants it — the owner reads this when "
                                      "deciding"},
            "boat": {"type": "string",
                     "description": "boat key when this machine serves several boats; omit "
                                    "for the boat this server was started on"},
        }, "required": ["url"]},
    },
    {
        "name": "fetch_document",
        "description": "Download one PDF into the boat's INBOX for the owner to look at — "
                       "a manual, a datasheet, a service bulletin. This reaches the "
                       "internet from the owner's own machine.\n\n"
                       "WHAT IT IS NOT: the file does NOT become one of the boat's "
                       "documents. Nothing searches it, quotes it or answers from it while "
                       "it sits in the inbox. A person opens it, reads it and accepts it in "
                       "the console, and only then does the library pick it up. Tell the "
                       "owner exactly that, and never claim the boat now knows what the "
                       "document says.\n\n"
                       "Refused, in a sentence saying which: anything that is not https, "
                       "any address that is not on the public internet, more than three "
                       "redirects, more than 25 MB, and anything whose body is not a PDF — "
                       "for those use add_link. A PDF carrying JavaScript, an embedded file "
                       "or an open action is kept and labelled, and the owner is shown the "
                       "labels.",
        "inputSchema": {"type": "object", "properties": {
            "url": {"type": "string", "description": "an https URL ending in a PDF"},
            "title": {"type": "string", "description": "what the document is"},
            "reason": {"type": "string",
                       "description": "why this boat wants it — the owner reads this when "
                                      "deciding"},
            "boat": {"type": "string", "description": "boat key, as for add_link"},
        }, "required": ["url"]},
    },
    {
        "name": "boat_inbox",
        "description": "What is waiting in the boat's inbox: links and PDFs an assistant "
                       "has put there, who put each one and why, whether a PDF carries "
                       "active content, and what the owner has already accepted or "
                       "rejected — with the reason they gave. Read it before suggesting "
                       "something again: an item the owner rejected should not be "
                       "re-submitted, and saying 'you turned this down in March because it "
                       "was the wrong engine variant' is more use than fetching it twice. "
                       "Read-only; accepting and rejecting is done by a person in the "
                       "console.",
        "inputSchema": {"type": "object", "properties": {
            "boat": {"type": "string", "description": "boat key, as for add_link"},
        }},
    },
]


# --- tool implementations -------------------------------------------------------------

def tool_add_note(text):
    written = notes.add(text, by="assistant")
    return (f"Noted at {written['at']}.\n\nThis went into the companion's notes file, "
            f"which is searchable but is NOT one of the boat's documents and is marked "
            f"unverified. If it matters, the owner should move it into the real file.")


def tool_add_document(title, read, source="", kind="other", unclear=""):
    entry = documents.take_in(title=title, read=read, source=source, kind=kind,
                              unclear=unclear)
    warn = ("" if unclear else
            "\n\nNote: you left `unclear` empty, which records a claim that every "
            "character was legible. If anything was a guess, say so now and it can be "
            "added as a correction.")
    return (f"Taken in as a transcription at {entry['at']}: {entry['title']}.\n"
            f"Read from: {entry['source'] or 'not stated'}.\n\n"
            f"This is searchable but is NOT one of the boat's documents, and nothing was "
            f"changed in them. Before anybody relies on a number in it, they should look "
            f"at the photograph.{warn}")


def tool_boat_files(contains=""):
    found = documents.files(contains=contains)
    if not found:
        return ("No image or PDF files found beside this boat's documents"
                + (f" matching {contains!r}" if contains else "") + ".")
    return "\n".join(f"{f['name']}  ({f['kb']} kB)  {f['path']}" for f in found)


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


def tool_boat_specs(boat=""):
    """The pinned boat's measured facts, or another boat's when asked by key.

    Reading a second boat's specifications is not the same permission as reading its
    documents, its faults or its logbook, and this tool grants only the first: a profile
    is a hull's dimensions, not anybody's business. What it must never do is answer about
    one hull while sounding like it answered about another, so the name leads every reply
    and an unknown key is refused with the list rather than quietly falling back to the
    boat this companion happens to be pinned to.
    """
    boat_profile = load()
    key = (boat or "").strip()
    if key:
        known = snag.boats()
        keys = [b["key"] for b in known]
        if key not in keys:
            return (f"No boat {key!r} here. Boats this machine knows: "
                    f"{', '.join(keys) or 'none'}.")
        here = boat_profile.path.resolve() if boat_profile.path else None
        other = Path(next(b["profile"] for b in known if b["key"] == key))
        if not (here and other.resolve() == here):
            boat_profile = load(other)
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
    # A boat with no berth recorded has `berth = None`, not an empty table — and a second
    # boat on this machine was exactly that, so this tool raised instead of answering.
    berth = boat_profile.as_dict().get("berth") or {}
    if berth.get("name"):
        lines.append(f"\nBerth: {berth['name']} ({berth['lat']:.4f}, {berth['lon']:.4f})")
    # A machine with more than one hull on it: name the others, once, so a question about
    # "the other boat" is answered with a key to ask for rather than with a guess.
    here = boat_profile.path.resolve() if boat_profile.path else None
    others = [b for b in snag.boats()
              if not (here and Path(b["profile"]).resolve() == here)]
    if others:
        lines.append("\nAlso on this machine: "
                     + ", ".join(f"{b['name']} ({b['key']})" for b in others)
                     + " — pass boat=<key> for its specifications.")
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


def tool_boat_tasks(boat="", status="open", what=""):
    """Snags and service due, as text a model can quote. Two sources, both read-only."""
    profile = load()
    known = snag.boats()
    keys = [b["key"] for b in known]
    here = profile.path.resolve() if profile.path else None
    mine = next((b["key"] for b in known
                 if here and Path(b["profile"]).resolve() == here), keys[0] if keys else "")
    key = (boat or mine).strip()
    if key not in keys:
        return (f"No boat {key!r} here. Boats this machine knows: {', '.join(keys) or 'none'}."
                if key else "No boat is configured for snags on this machine.")
    want = (status or "open").strip().lower()
    needle = (what or "").strip().lower()
    hit = lambda *bits: not needle or any(needle in str(b or "").lower() for b in bits)

    out = [f"Boat: {next(b['name'] for b in known if b['key'] == key)} ({key})", ""]
    entries = snag.read_snags(key)
    if want == "all":
        shown = entries
    elif want == "open":
        shown = [e for e in entries if e["open"]]
    else:
        shown = [e for e in entries if e["status"].lower().startswith(want)]
    shown = [e for e in shown if hit(e["title"], e["where"], e["body"])]
    out.append(f"SNAGS — {len(shown)} shown of {len(entries)} filed, "
               f"{sum(1 for e in entries if e['open'])} open in all")
    if not shown:
        out.append("  none")
        # A machine serving several boats: say where the snags are, so a question about
        # "the tasks" is not answered with silence about the wrong hull.
        others = [(b["key"], sum(1 for e in snag.read_snags(b["key"]) if e["open"]))
                  for b in known if b["key"] != key]
        others = [f"{k} ({n} open)" for k, n in others if n]
        if others:
            out.append("  other boats on this machine with open snags: " + ", ".join(others)
                       + " — pass boat=<key> to read them")
    for e in shown:
        out.append(f"  [{e['status'] or 'open'}] {e['when']}  {e['title']}"
                   f"{'  — ' + e['where'] if e['where'] else ''}"
                   f"{'  (by ' + e['by'] + ')' if e['by'] else ''}")
        if e["body"]:
            first = e["body"].splitlines()[0]
            if first != e["title"]:
                out.append(f"      {first[:200]}")
        if e.get("photos"):
            out.append(f"      photos: {', '.join(Path(x).name for x in e['photos'])}"
                       "  (snag_photo shows them)")
        for u in e.get("updates", []):
            # A follow-up's status is only worth printing when it changed something; the
            # recorder's old default `open` on a plain follow-up is not news.
            said = (u["status"] or "").strip().lower()
            out.append(f"      ↳ {u['when']}{' [' + u['status'] + ']' if said and said != 'open' else ''}"
                       f"{' ' + u['by'] if u['by'] else ''}: "
                       f"{(u['body'] or u['title']).splitlines()[0][:200]}")
    out.append("")

    if boat and key != mine:
        out.append("SERVICE — the service schedule is per profile, and this server was "
                   f"started on {mine or 'another boat'}; run it on {key}'s profile for "
                   "its service items.")
        return "\n".join(out)
    if not DEFAULT_DB.exists():
        out.append(f"SERVICE — no engine log at {DEFAULT_DB}; nothing has been counted.")
        return "\n".join(out)
    db = connect_engine_data(DEFAULT_DB)
    try:
        items = maintenance.due(db, profile)
        meter = engine_hours.summary(db)
    finally:
        db.close()
    items = [d for d in items if hit(d.item, d.description, d.why)]
    out.append(f"SERVICE — {len(items)} item(s); "
               + (f"{meter['running_h']:.1f} h counted by the log over "
                  f"{meter['coverage'] * 100:.0f} % coverage" if meter["samples"]
                  else "the engine log holds no samples yet"))
    if meter.get("synthetic"):
        out.append("  ⚠ SYNTHETIC DATA — no engine ran.")
    for d in items:
        every = " / ".join(x for x in (
            f"{d.interval_hours:.0f} h" if d.interval_hours else "",
            f"{d.interval_months} months" if d.interval_months else "",
            f"{d.interval_days} days" if d.interval_days else "",
            "each salt-water outing" if d.per_outing else "") if x) or "no interval set"
        last = f"{d.last:%Y-%m-%d}" if d.last else "never recorded"
        out.append(f"  [{d.verdict}] {d.item}: {d.description}")
        out.append(f"      due every {every}; last {last}; {d.why}")
    return "\n".join(out)


PHOTO_NAME = re.compile(r"[A-Za-z0-9._-]{1,120}\.jpe?g", re.I)
PHOTO_MAX_PX = 1024


def _small_jpeg(path: Path) -> tuple[bytes, str]:
    """The photograph, downscaled for a model to look at — or as it is, if it cannot be.

    A phone photograph is a megabyte; sixteen of them in one answer is a conversation that
    stops working. The package is stdlib-only and the standard library cannot resize a
    JPEG, so this leans on the platform's own tool where there is one (`sips` on macOS)
    and otherwise sends the original and says so. Never a silent failure: the caller is
    told which it got.
    """
    tool = shutil.which("sips")
    if tool:
        with tempfile.TemporaryDirectory() as raw:
            out = Path(raw) / "small.jpg"
            done = subprocess.run([tool, "-Z", str(PHOTO_MAX_PX), "-s", "format", "jpeg",
                                   "-s", "formatOptions", "60", str(path), "--out", str(out)],
                                  capture_output=True, timeout=30)
            if done.returncode == 0 and out.exists():
                return out.read_bytes(), f"downscaled to {PHOTO_MAX_PX} px"
    return path.read_bytes(), "original size — nothing here could resize it"


def tool_snag_photo(boat="", name="", when=""):
    """The pictures of a snag, as image content the model can see."""
    profile = load()
    known = snag.boats()
    keys = [b["key"] for b in known]
    here = profile.path.resolve() if profile.path else None
    mine = next((b["key"] for b in known
                 if here and Path(b["profile"]).resolve() == here), keys[0] if keys else "")
    key = (boat or mine).strip()
    if key not in keys:
        return f"No boat {key!r} here. Boats this machine knows: {', '.join(keys) or 'none'}."
    folder = next(b["profile"] for b in known if b["key"] == key).parent / "photos" / "snags"

    wanted: list[str] = []
    if name:
        wanted = [Path(name).name]
    elif when:
        entry = next((e for e in snag.read_snags(key) if e["when"] == when.strip()), None)
        if entry is None:
            return f"No snag filed at {when!r} on {key}. boat_tasks lists the timestamps."
        wanted = [Path(x).name for x in entry.get("photos", [])][:4]
        if not wanted:
            return f"The snag filed at {when} has no photograph."
    else:
        return "Say which: `name` (one photo file from boat_tasks) or `when` (a snag's timestamp)."

    content: list[dict] = []
    notes: list[str] = []
    for leaf in wanted:
        # Never join a name from the network to a directory as given: the leaf must look
        # like a file the snag service wrote, and must exist under that one folder.
        if not PHOTO_NAME.fullmatch(leaf):
            notes.append(f"{leaf}: not a snag photograph name")
            continue
        path = folder / leaf
        if not path.is_file():
            notes.append(f"{leaf}: not on disk")
            continue
        blob, how = _small_jpeg(path)
        content.append({"type": "image", "data": base64.b64encode(blob).decode("ascii"),
                        "mimeType": "image/jpeg"})
        notes.append(f"{leaf}: {len(blob) // 1024} kB, {how}")
    content.append({"type": "text", "text": "\n".join(notes)})
    return {"content": content}


# --- the inbox: an assistant submits, a person decides -----------------------------------

INBOX_SAID = ("\n\nThis is in the boat's INBOX. It is NOT one of the boat's documents: "
              "nothing searches it, quotes it or answers from it until the owner reads it "
              "and accepts it in the console. Say that to the owner rather than implying "
              "the boat now knows what it says.")


def tool_add_link(url, title="", reason="", boat=""):
    try:
        entry = intake.add_link(url, title=title, reason=reason, by=caller(), boat=boat)
    except intake.Refused as exc:
        return f"Not recorded: {exc}"
    return (f"Recorded as {entry['id']}: {entry['title'] or entry['url']}\n"
            f"by {entry['by']} at {entry['when']}\n"
            f"The link was NOT opened — nothing here fetched or read that page." + INBOX_SAID)


def tool_fetch_document(url, title="", reason="", boat=""):
    try:
        entry = intake.fetch_document(url, title=title, reason=reason, by=caller(),
                                      boat=boat)
    except intake.Refused as exc:
        return f"Not fetched: {exc}"
    if entry.get("duplicate"):
        return (f"That is byte-for-byte the document already in the inbox as "
                f"{entry['id']}, brought by {entry.get('by') or 'somebody'} on "
                f"{(entry.get('fetched') or '')[:10]}, status {entry.get('status')}. "
                f"Nothing was written twice.")
    flags = entry.get("flags") or []
    lines = [f"Fetched into the inbox as {entry['id']}.",
             f"  from      {entry['url']}",
             f"  size      {entry['bytes']} bytes, {entry['content_type']}",
             f"  sha256    {entry['sha256']}",
             f"  by        {entry['by']} at {entry['fetched']}"]
    if flags:
        lines.append("  ⚠ ACTIVE CONTENT: this PDF contains " + ", ".join(flags)
                     + ". It is kept and labelled, not opened. Tell the owner.")
    return "\n".join(lines) + INBOX_SAID


def tool_boat_inbox(boat=""):
    try:
        found = intake.items(boat)
    except intake.Refused as exc:
        return str(exc)
    if not found:
        return ("This boat's inbox is empty. Nothing has been suggested or fetched for it "
                "yet.")
    waiting = [e for e in found if e["status"] == "inbox"]
    out = [f"{len(waiting)} waiting of {len(found)} in the inbox. Nothing here is one of "
           f"the boat's documents.", ""]
    for e in found:
        head = (f"  [{e['status']}] {e['kind']} {e['id']}  "
                f"{e['title'] or e['url'] or '(untitled)'}")
        out.append(head)
        out.append(f"      by {e['by'] or '?'} at {e['when'] or '?'}"
                   + (f"  ({e['bytes']} bytes)" if e.get("bytes") else ""))
        if e.get("url") and e["title"]:
            out.append(f"      {e['url']}")
        if e.get("reason"):
            out.append(f"      reason: {e['reason']}")
        if e.get("flags"):
            out.append("      ⚠ active content: " + ", ".join(e["flags"]))
        if e["status"] == "rejected":
            out.append(f"      rejected by {e.get('decided_by') or 'the owner'}"
                       f"{' — ' + e['decided_why'] if e.get('decided_why') else ''}. "
                       f"Do not submit it again.")
        elif e["status"] == "accepted":
            out.append(f"      accepted by {e.get('decided_by') or 'the owner'}"
                       f"{' — now ' + e['document'] if e.get('document') else ''}")
    return "\n".join(out)


def tool_engine_data(days=None):
    if not DEFAULT_DB.exists():
        return (f"No engine log at {DEFAULT_DB}. Nothing has been measured; start one with "
                "python3 -m openboat.engine on the boat's network.")
    since = None
    if days:
        since = int(datetime.now().timestamp()) - int(days) * 86400
    db = connect_engine_data(DEFAULT_DB)
    try:
        hours = engine_hours.summary(db, since)
        health = engine_health.analyse(db)
    finally:
        db.close()
    parts = [engine_hours.render(hours), "", "HEALTH", engine_health.render(health)]
    return "\n".join(parts)


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
    "add_document": lambda **kw: tool_add_document(**kw),
    "boat_files": lambda **kw: tool_boat_files(**kw),
    "boat_tasks": lambda **kw: tool_boat_tasks(**kw),
    "engine_data": lambda **kw: tool_engine_data(**kw),
    "snag_photo": lambda **kw: tool_snag_photo(**kw),
    "add_link": lambda **kw: tool_add_link(**kw),
    "fetch_document": lambda **kw: tool_fetch_document(**kw),
    "boat_inbox": lambda **kw: tool_boat_inbox(**kw),
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
        return reply(request_id, as_result(text))

    if request_id is None:
        return None  # a notification; nothing to answer

    return error(request_id, -32601, f"unknown method {method!r}")


def as_result(value) -> dict:
    """A tool answers with a string, or — when it has a picture to show — with a ready
    `{"content": [...]}` holding image parts. Both become the same shape here."""
    if isinstance(value, dict) and "content" in value:
        return value
    return {"content": [{"type": "text", "text": str(value)}]}


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
