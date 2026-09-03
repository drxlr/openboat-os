"""What the engine is owed, counted in the hours it actually ran.

A maintenance schedule kept in a notebook is a schedule kept in calendar months, and a boat
engine does not age in months. It ages in running hours, and — if it is raw-water cooled — in
the number of times it has been shut down full of salt water.

This module joins the two: the service record you keep here, and the running hours the engine
log already measures. It answers one question, "what is due", and it answers it in the units
that caused the wear.

    python3 -m openboat.maintenance                       # what is due
    python3 -m openboat.maintenance --did flush           # record one, now
    python3 -m openboat.maintenance --did impeller --on 2026-08-14 --note "Ancor 06-2026"
    python3 -m openboat.maintenance --history

## The fresh-water flush

For a raw-water cooled engine this is the item that matters most and the one most easily
forgotten, so it is treated specially.

This module only *records* flushes and tells you when one is owed. Actually pushing fresh
water through the engine is a valve opening, which is a write to the boat, and this
repository has none by construction. That lives in a separate, separately installed
project — see DISCLAIMER.md — which writes its service record here when a flush completes.

A raw-water engine pumps the sea through itself and then sits full of it. Salt left standing
in a hot block crystallises out of solution, and the crystals build up in exactly the narrow
passages the cooling depends on — the exhaust riser and elbow first. That is not corrosion
over years; it is deposition over a season, and it ends as an overheat somewhere inconvenient.
A flush is ten minutes with a hose and a set of muffs, and it is the difference between an
engine that lasts and one that needs a riser at the worst moment.

So a flush is counted **per salt-water outing**, not per hour and not per month. One trip out
means one flush owed, and this module reads those trips from the engine log rather than asking
you to remember them.

## No invented intervals

Every other interval comes from your profile's `[maintenance]` table, which means from your
engine's own manual. This module ships none. "Every 100 hours" is true of some engines and
wrong for others, and a schedule that is confidently wrong is worse than no schedule: it gets
followed. Where you have not set an interval, an item is recorded and its running total shown,
with no verdict attached.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .engine import DEFAULT_DB, connect, meta_get
from .engine_hours import integrate, load as load_samples
from .profile import Profile, load as load_profile

SCHEMA = """
CREATE TABLE IF NOT EXISTS service (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    t     INTEGER NOT NULL,          -- unix seconds UTC, when the work was done
    item  TEXT NOT NULL,             -- 'flush', 'oil', 'impeller', 'anodes', or your own
    hours REAL,                      -- running hours at the time, NULL if not known
    note  TEXT
);
CREATE INDEX IF NOT EXISTS service_item_t ON service (item, t);
"""

#: The one item this module knows about without being told, because it is a property of the
#: cooling arrangement rather than of a particular engine. Everything else is your manual's.
FLUSH = "flush"

#: A run that never left the berth and never exceeded this speed is a candidate flush: the
#: engine ran, the boat did not go anywhere. Used only to *suggest*, never to record.
FLUSH_MAX_SOG_KN = 0.7


@dataclass
class Due:
    item: str
    description: str
    last: datetime | None
    hours_since: float | None
    days_since: int | None
    outings_since: int | None
    interval_hours: float | None
    interval_months: int | None
    per_outing: bool
    verdict: str                     # 'due' | 'soon' | 'ok' | 'unknown'
    why: str

    @property
    def symbol(self) -> str:
        return {"due": "▲", "soon": "·", "ok": " ", "unknown": "?"}[self.verdict]


def ensure(db: sqlite3.Connection) -> None:
    db.executescript(SCHEMA)
    db.commit()


def record(db: sqlite3.Connection, item: str, when: datetime | None = None,
           hours: float | None = None, note: str = "") -> int:
    """Write one service event. Returns its id."""
    when = when or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    cursor = db.execute(
        "INSERT INTO service (t, item, hours, note) VALUES (?, ?, ?, ?)",
        (int(when.timestamp()), item.strip().lower(), hours, note.strip() or None))
    db.commit()
    return int(cursor.lastrowid)


def history(db: sqlite3.Connection, item: str | None = None, limit: int = 50) -> list[dict]:
    ensure(db)
    sql = "SELECT id, t, item, hours, note FROM service"
    args: tuple = ()
    if item:
        sql += " WHERE item = ?"
        args = (item.strip().lower(),)
    sql += " ORDER BY t DESC LIMIT ?"
    rows = db.execute(sql, args + (limit,)).fetchall()
    return [{"id": r[0], "when": datetime.fromtimestamp(r[1], timezone.utc),
             "item": r[2], "hours": r[3], "note": r[4]} for r in rows]


def _last(db: sqlite3.Connection, item: str) -> tuple[datetime, float | None] | None:
    row = db.execute("SELECT t, hours FROM service WHERE item = ? ORDER BY t DESC LIMIT 1",
                     (item,)).fetchone()
    return (datetime.fromtimestamp(row[0], timezone.utc), row[1]) if row else None


def _outings_since(db: sqlite3.Connection, since: datetime | None,
                   exclude_stationary: bool = True) -> tuple[int, datetime | None]:
    """Count engine runs after `since`, and return the last one's start.

    With `exclude_stationary`, a run during which the boat never exceeded FLUSH_MAX_SOG_KN
    is not counted: the engine ran but no salt water was drawn through it at sea. That is a
    warm-up or a flush itself, and neither owes a flush.
    """
    cutoff = int(since.timestamp()) if since else None
    ledger = integrate(load_samples(db, cutoff))

    counted, last_start = 0, None
    for start, end in ledger.outings:
        if exclude_stationary:
            row = db.execute(
                "SELECT MAX(sog_kn) FROM samples WHERE t BETWEEN ? AND ?", (start, end)
            ).fetchone()
            peak = row[0] if row and row[0] is not None else None
            if peak is not None and peak <= FLUSH_MAX_SOG_KN:
                continue                      # never left the berth: owes nothing
        counted += 1
        last_start = datetime.fromtimestamp(start, timezone.utc)
    return counted, last_start


def _running_hours(db: sqlite3.Connection, since: datetime | None) -> float:
    cutoff = int(since.timestamp()) if since else None
    return integrate(load_samples(db, cutoff)).running_h


def due(db: sqlite3.Connection, boat: Profile | None = None) -> list[Due]:
    """Everything the profile knows about, plus the flush, each with a verdict or an honest
    'unknown' where no interval has been set."""
    ensure(db)
    boat = boat or load_profile()
    schedule: dict = dict(getattr(boat, "maintenance", {}) or {})

    # The flush is always assessed, whether or not the profile mentions it, because it is a
    # property of raw-water cooling rather than of one engine. Its settings are still yours.
    flush_cfg = dict(schedule.pop(FLUSH, {}) or {})
    items: list[Due] = [_assess_flush(db, flush_cfg)]

    for item, cfg in sorted(schedule.items()):
        items.append(_assess_interval(db, item, dict(cfg or {})))

    order = {"due": 0, "soon": 1, "unknown": 2, "ok": 3}
    return sorted(items, key=lambda d: (order[d.verdict], d.item))


def _assess_flush(db: sqlite3.Connection, cfg: dict) -> Due:
    description = str(cfg.get("description")
                      or "Fresh-water flush of the raw-water circuit")
    last = _last(db, FLUSH)
    last_when = last[0] if last else None
    outings, _ = _outings_since(db, last_when)
    days = (datetime.now(timezone.utc) - last_when).days if last_when else None

    if last_when is None:
        verdict = "unknown"
        why = ("no flush has ever been recorded, so there is nothing to count from. "
               "Record the next one with --did flush")
    elif outings == 0:
        verdict = "ok"
        why = "no salt-water outing since the last flush"
    elif outings == 1:
        verdict = "due"
        why = "one outing since the last flush"
    else:
        verdict = "due"
        why = f"{outings} outings since the last flush"

    return Due(item=FLUSH, description=description, last=last_when,
               hours_since=_running_hours(db, last_when) if last_when else None,
               days_since=days, outings_since=outings, interval_hours=None,
               interval_months=None, per_outing=True, verdict=verdict, why=why)


def _assess_interval(db: sqlite3.Connection, item: str, cfg: dict) -> Due:
    description = str(cfg.get("description") or item.replace("_", " ").capitalize())
    interval_h = cfg.get("hours")
    interval_m = cfg.get("months")
    interval_h = float(interval_h) if interval_h else None
    interval_m = int(interval_m) if interval_m else None

    last = _last(db, item)
    last_when = last[0] if last else None
    hours_since = _running_hours(db, last_when) if last_when else _running_hours(db, None)
    days = (datetime.now(timezone.utc) - last_when).days if last_when else None

    if last_when is None:
        return Due(item, description, None, hours_since, None, None, interval_h, interval_m,
                   False, "unknown",
                   "never recorded — the total above is the whole log, not an interval")

    if interval_h is None and interval_m is None:
        return Due(item, description, last_when, hours_since, days, None, None, None, False,
                   "unknown",
                   "no interval set in the profile, so no verdict — the counters are yours "
                   "to read")

    reasons, worst = [], "ok"
    if interval_h is not None:
        fraction = hours_since / interval_h if interval_h else 0
        reasons.append(f"{hours_since:.1f} of {interval_h:.0f} h")
        worst = "due" if fraction >= 1 else ("soon" if fraction >= 0.85 else worst)
    if interval_m is not None and days is not None:
        limit_days = interval_m * 30.44
        reasons.append(f"{days} of about {limit_days:.0f} days")
        if days >= limit_days:
            worst = "due"
        elif days >= limit_days * 0.85 and worst == "ok":
            worst = "soon"

    return Due(item, description, last_when, hours_since, days, None, interval_h, interval_m,
               False, worst, " and ".join(reasons))


def suggest_unrecorded_flushes(db: sqlite3.Connection, limit: int = 5) -> list[datetime]:
    """Engine runs that look like a flush but were never recorded as one.

    A run where the boat never moved is very likely a flush on the hose — or a warm-up, or
    someone charging a battery. The module will not guess between them, so this only ever
    offers a list for a person to confirm. Nothing here writes a service record.
    """
    ensure(db)
    ledger = integrate(load_samples(db, None))
    known = {row[0] for row in db.execute("SELECT t FROM service WHERE item = ?", (FLUSH,))}

    found: list[datetime] = []
    for start, end in reversed(ledger.outings):
        row = db.execute("SELECT MAX(sog_kn) FROM samples WHERE t BETWEEN ? AND ?",
                         (start, end)).fetchone()
        peak = row[0] if row and row[0] is not None else None
        if peak is None or peak > FLUSH_MAX_SOG_KN:
            continue
        if any(abs(k - start) < 6 * 3600 for k in known):
            continue                                   # already recorded near this run
        found.append(datetime.fromtimestamp(start, timezone.utc))
        if len(found) >= limit:
            break
    return found


def render(items: list[Due], db: sqlite3.Connection | None = None) -> str:
    lines: list[str] = []
    if db is not None and meta_get(db, "synthetic") == "1":
        lines.append("⚠  SYNTHETIC DATA — this log was fabricated by engine_seed. "
                     "No engine ran.")
        lines.append("")

    if not items:
        return "Nothing to report: no maintenance items and no flush record."

    lines.append(f"{'':2}{'item':<14}{'last':<13}{'since':<26}{'why'}")
    for d in items:
        last = f"{d.last:%d.%m.%Y}" if d.last else "never"
        if d.per_outing:
            since = (f"{d.outings_since} outing(s), {d.hours_since:.1f} h"
                     if d.hours_since is not None else "—")
        elif d.hours_since is not None:
            since = f"{d.hours_since:.1f} h" + (f", {d.days_since} days" if d.days_since
                                                is not None else "")
        else:
            since = "—"
        lines.append(f"{d.symbol} {d.item:<14}{last:<13}{since:<26}{d.why}")

    overdue = [d for d in items if d.verdict == "due"]
    unknown = [d for d in items if d.verdict == "unknown"]
    lines.append("")
    if overdue:
        lines.append("▲ " + ", ".join(d.description for d in overdue))
    if unknown:
        lines.append("? " + ", ".join(f"{d.item}: {d.why}" for d in unknown))
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="What the engine is owed, counted in the hours it actually ran.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--profile")
    parser.add_argument("--did", metavar="ITEM",
                        help="record that this was done (e.g. flush, oil, impeller)")
    parser.add_argument("--on", metavar="YYYY-MM-DD",
                        help="the date it was done, if not today")
    parser.add_argument("--note", default="", help="what was used, who did it")
    parser.add_argument("--history", action="store_true", help="what has been done")
    parser.add_argument("--suggest", action="store_true",
                        help="engine runs that look like an unrecorded flush")
    args = parser.parse_args()

    boat = load_profile(args.profile) if args.profile else load_profile()
    db = connect(args.db)
    ensure(db)

    if args.did:
        when = (datetime.fromisoformat(args.on).replace(tzinfo=timezone.utc)
                if args.on else datetime.now(timezone.utc))
        hours = _running_hours(db, None)
        record(db, args.did, when, hours, args.note)
        print(f"recorded: {args.did} on {when:%d.%m.%Y} at {hours:.1f} running hours"
              + (f" — {args.note}" if args.note else ""))
        print()

    if args.history:
        rows = history(db)
        if not rows:
            print("nothing recorded yet")
            return
        for r in rows:
            hours = f"{r['hours']:.1f} h" if r["hours"] is not None else "hours unknown"
            print(f"  {r['when']:%d.%m.%Y}  {r['item']:<12}{hours:<16}{r['note'] or ''}")
        return

    if args.suggest:
        found = suggest_unrecorded_flushes(db)
        if not found:
            print("no engine run looks like an unrecorded flush")
            return
        print("These runs never left the berth, so each may have been a flush. Confirm one")
        print("with --did flush --on YYYY-MM-DD; nothing is recorded for you.")
        for when in found:
            print(f"  {when:%d.%m.%Y %H:%M}")
        return

    print(render(due(db, boat), db))


if __name__ == "__main__":
    main()
