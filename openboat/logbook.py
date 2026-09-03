#!/usr/bin/env python3
"""What was checked, when, and what it read.

    python3 -m openboat.logbook                    # what has been checked lately
    python3 -m openboat.logbook --since 2026-01-01

A boat's real maintenance record is not the service schedule. It is the accumulated answer
to "did anyone look at this, and what did it say" — and on most boats that record does not
exist, because the moment you are holding a torch in a bilge is the worst possible moment to
open a spreadsheet.

So this is deliberately the cheapest thing that can still be true: one append-only JSONL
file, one line per check, written by whatever is at hand — the companion on the tablet, a
model talking to the MCP server, or `python3 -m openboat.logbook --add` from a phone over
ssh. It is a log, not a database: nothing here edits or deletes a past line, because a
maintenance record you can quietly revise is not evidence of anything.

Each entry carries what was checked, what was found, and — this is the part that makes it
worth having — the live readings at that moment, captured automatically. "Impeller looks
fine" is worth little in a year. "Impeller looks fine, 412.6 engine hours, coolant 84 °C,
2026-09-04" is the line that tells you, next season, whether it is due.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

#: What a check concluded. Deliberately four words and no numeric severity: a scale invites
#: an argument about whether something is a 3 or a 4, and the only decision that follows
#: from a check is whether the boat goes out.
VERDICTS = ("ok", "watch", "act", "noted")

#: The readings a maintenance entry is usually about, in the order a person would read them
#: out. Position is recorded too, just not first.
FIRST = ("rpm", "coolant_c", "oil_bar", "volts", "fuel_pct", "water_c", "depth_m")


@dataclass
class Entry:
    """One check. `readings` is whatever the boat was saying at the time, if anything was."""

    what: str
    found: str = ""
    verdict: str = "noted"
    at: str = ""
    by: str = ""
    readings: dict = field(default_factory=dict)
    refs: list = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.verdict not in VERDICTS:
            raise ValueError(f"verdict must be one of {VERDICTS}, not {self.verdict!r}")
        if not self.what.strip():
            raise ValueError("a check with no subject records nothing")
        self.at = self.at or datetime.now(timezone.utc).isoformat(timespec="seconds")

    def line(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


def path_for(boat=None) -> Path:
    """The log file, resolved beside the profile rather than inside the package."""
    from .profile import load as load_profile
    boat = boat or load_profile()
    p = Path(boat.logbook).expanduser()
    if p.is_absolute():
        return p
    return (boat.path.parent if boat.path else Path.cwd()) / p


def record(what: str, found: str = "", verdict: str = "noted", by: str = "",
           refs: list | None = None, boat=None, live: bool = True) -> Entry:
    """Append one check. Captures the live readings unless told not to.

    The boat being unreachable is not a reason to refuse the entry — most checks happen with
    the ignition off and Signal K asleep. It records what it could get, which is sometimes
    nothing, and never fails the write because of it.
    """
    readings: dict = {}
    if live:
        try:
            from . import boat as boat_module
            state = boat_module.state()
            numbers = {k: v for k, v in state.items()
                       if isinstance(v, (int, float)) and v is not None}
            # Ordered, not just collected. A check is almost always about the machinery, and
            # a summary that leads with latitude buries the number that mattered. Everything
            # is still recorded; only the order changes.
            readings = {k: numbers[k] for k in FIRST if k in numbers}
            readings.update({k: v for k, v in numbers.items() if k not in readings})
        except Exception:
            readings = {}

    entry = Entry(what=what, found=found, verdict=verdict, by=by,
                  readings=readings, refs=list(refs or []))
    target = path_for(boat)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(entry.line() + "\n")
    return entry


def entries(boat=None, since: str = "", what: str = "", limit: int = 0) -> list[dict]:
    """Read the log back, newest last. A corrupt line is skipped, never fatal."""
    target = path_for(boat)
    if not target.exists():
        return []
    out = []
    for raw in target.read_text(errors="ignore").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if since and row.get("at", "") < since:
            continue
        if what and what.casefold() not in row.get("what", "").casefold():
            continue
        out.append(row)
    return out[-limit:] if limit else out


def last(what: str, boat=None) -> dict | None:
    """The most recent check matching a subject — "when was the impeller last looked at"."""
    found = entries(boat=boat, what=what)
    return found[-1] if found else None


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--add" in argv:
        i = argv.index("--add")
        subject = argv[i + 1] if len(argv) > i + 1 else ""
        note = argv[i + 2] if len(argv) > i + 2 else ""
        e = record(subject, note, by="cli")
        print(f"logged: {e.what} — {e.verdict} at {e.at}")
        raise SystemExit(0)

    since = ""
    if "--since" in argv:
        since = argv[argv.index("--since") + 1]
    rows = entries(since=since)
    if not rows:
        print(f"Nothing logged yet. {path_for()}")
        raise SystemExit(0)
    for row in rows:
        live = row.get("readings") or {}
        tail = ("  · " + ", ".join(f"{k} {v}" for k, v in list(live.items())[:3])) if live else ""
        print(f"{row['at'][:16]}  [{row['verdict']:>5}]  {row['what']}"
              f"{' — ' + row['found'] if row.get('found') else ''}{tail}")
