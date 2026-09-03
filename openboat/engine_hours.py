"""The hour meter. Integrates running time out of the samples, and says what it cannot know.

    python3 -m openboat.engine_hours
    python3 -m openboat.engine_hours --since 2026-01-01
    python3 -m openboat.engine_hours --baseline 412.5 --baseline-at 2026-09-14

## What an hour meter built this way can and cannot say

It can say: **how many hours the engine ran while the logger was watching**, to the
second, with a record of every interval it counted.

It cannot say what the engine did before the logger existed. Whether a rebuilt long-block
has done 50 hours or 500 is not a question logging backwards in time can ever answer — the
only thing that answers it is the number on the helm display, photographed once. Enter it
with `--baseline`, and from then on this reports **display hours at that date + hours
counted since**, a number a surveyor can follow. Without it, every total here is explicitly
*hours since logging began*, never *engine hours*.

## Three buckets, and the third one is the point

Every second between the first and last sample lands in exactly one of:

- **running** — the engine was turning above `IDLE_RPM`
- **stopped** — the engine was measured and was not turning
- **unknown** — nobody was watching

Unknown is not zero. The logger being off, the boat out of network reach, and Signal K
holding a stale value from a dead sender all produce the same thing: time this file has no
evidence about. Rolling that into "stopped" would make every total quietly too low and
every report quietly confident. So it is counted, printed next to the total, and expressed
as a **coverage** percentage. A total with 12 % coverage is a rumour; the same total with
98 % coverage is a record.

## The thresholds, and why

`IDLE_RPM = 400` is a default sitting between typical cranking rpm (a couple of hundred,
for most small petrol or diesel engines) and typical idle rpm (many marine engines idle
somewhere in the 600–900 range): a start is counted from the moment the engine actually
fires, and cranking is not counted as running. **Idling counts as running**, which matches
what a mechanical hour meter does — and on a raw-water-cooled engine, idling is also the
condition most likely to run hot, since flow drops with rpm. If your engine's real numbers
put cranking above 400 or idle below it, override `IDLE_RPM`.

`MAX_GAP_S = 120` assumes a logger polling roughly every 10 s; two minutes is twelve missed
polls. Past that, something stopped, and interpolating across it would be inventing engine
hours. Widen it if you poll less often.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import date, datetime, timezone, tzinfo as TzInfo
from pathlib import Path
from zoneinfo import ZoneInfo

from .engine import DEFAULT_DB, STALE_AFTER_S, connect, meta_get, meta_set

IDLE_RPM = 400.0
MAX_GAP_S = 120.0

#: Fallback when nobody supplies a timezone. `main()` uses the profile's instead — see
#: `Profile.timezone` — so a boat's own months and seasons line up with where it actually
#: is; UTC here just means "an outing was never assigned to the wrong local day".
DEFAULT_TZ: TzInfo = timezone.utc

# Two runs separated by less than this are one outing — a stop at the fuel dock, or an hour
# at anchor with the engine off. Used only for counting outings, never for counting hours.
SAME_OUTING_GAP_S = 3600.0


@dataclass
class Ledger:
    """Seconds in each bucket, plus enough detail to defend the number."""

    running_s: float = 0.0
    stopped_s: float = 0.0
    unknown_s: float = 0.0
    samples: int = 0
    stale_samples: int = 0
    first_t: int | None = None
    last_t: int | None = None
    outings: list[tuple[int, int]] = field(default_factory=list)
    by_month: dict[str, float] = field(default_factory=dict)
    by_season: dict[str, float] = field(default_factory=dict)

    @property
    def running_h(self) -> float:
        return self.running_s / 3600.0

    @property
    def unknown_h(self) -> float:
        return self.unknown_s / 3600.0

    @property
    def span_s(self) -> float:
        return self.running_s + self.stopped_s + self.unknown_s

    @property
    def coverage(self) -> float:
        """Fraction of the logged span the logger actually had eyes on. 0.0 when empty."""
        return 0.0 if self.span_s <= 0 else (self.running_s + self.stopped_s) / self.span_s


def _local(t: int, tz: TzInfo = DEFAULT_TZ) -> datetime:
    return datetime.fromtimestamp(t, tz=timezone.utc).astimezone(tz)


def load(db, since: int | None = None) -> list[tuple[int, float | None]]:
    """(unix second, rpm) in order, with stale readings demoted to unknown.

    A sender that has stopped sending leaves Signal K holding its last value forever. The
    dashboard is entitled to show it; an hour meter is not entitled to integrate it, so
    anything Signal K timestamped more than `STALE_AFTER_S` before the poll becomes NULL.
    """
    sql = "SELECT t, rpm, engine_age_s FROM samples"
    args: tuple = ()
    if since is not None:
        sql += " WHERE t >= ?"
        args = (since,)
    sql += " ORDER BY t"

    rows = []
    for t, rpm, age in db.execute(sql, args):
        if age is not None and age > STALE_AFTER_S:
            rpm = None
        rows.append((t, rpm))
    return rows


def integrate(rows: list[tuple[int, float | None]], tz: TzInfo = DEFAULT_TZ) -> Ledger:
    """Trapezoid between consecutive samples. Anything unwitnessed is unknown, not zero."""
    ledger = Ledger(samples=len(rows))
    if not rows:
        return ledger

    ledger.first_t, ledger.last_t = rows[0][0], rows[-1][0]
    ledger.stale_samples = sum(1 for _, rpm in rows if rpm is None)

    run_start: int | None = None
    run_end: int | None = None

    for (t0, r0), (t1, r1) in zip(rows, rows[1:]):
        dt = float(t1 - t0)
        if dt <= 0:
            continue

        if dt > MAX_GAP_S or r0 is None or r1 is None:
            ledger.unknown_s += dt
            bucket = None
        elif (r0 + r1) / 2.0 > IDLE_RPM:
            ledger.running_s += dt
            bucket = "running"
        else:
            ledger.stopped_s += dt
            bucket = "stopped"

        if bucket == "running":
            stamp = _local(t0, tz)
            month = f"{stamp.year:04d}-{stamp.month:02d}"
            ledger.by_month[month] = ledger.by_month.get(month, 0.0) + dt
            season = str(stamp.year)
            ledger.by_season[season] = ledger.by_season.get(season, 0.0) + dt

            if run_start is None:
                run_start = t0
            elif run_end is not None and t0 - run_end > SAME_OUTING_GAP_S:
                ledger.outings.append((run_start, run_end))
                run_start = t0
            run_end = t1

    if run_start is not None and run_end is not None:
        ledger.outings.append((run_start, run_end))
    return ledger


def summary(db, since: int | None = None, tz: TzInfo = DEFAULT_TZ) -> dict:
    """Everything the report and the CLI need, including the honest unknowns."""
    ledger = integrate(load(db, since), tz)

    baseline = meta_get(db, "baseline_hours")
    baseline_at = meta_get(db, "baseline_at")
    counted_from_baseline = None
    if baseline is not None and baseline_at:
        cutoff = int(datetime.fromisoformat(baseline_at)
                     .replace(tzinfo=tz).timestamp())
        counted_from_baseline = integrate(load(db, cutoff), tz).running_h

    return {
        "synthetic": meta_get(db, "synthetic") == "1",
        "running_h": ledger.running_h,
        "unknown_h": ledger.unknown_h,
        "coverage": ledger.coverage,
        "samples": ledger.samples,
        "stale_samples": ledger.stale_samples,
        "first": _local(ledger.first_t, tz).isoformat() if ledger.first_t else None,
        "last": _local(ledger.last_t, tz).isoformat() if ledger.last_t else None,
        "outings": len(ledger.outings),
        "by_month": {k: v / 3600.0 for k, v in sorted(ledger.by_month.items())},
        "by_season": {k: v / 3600.0 for k, v in sorted(ledger.by_season.items())},
        "baseline_hours": float(baseline) if baseline is not None else None,
        "baseline_at": baseline_at,
        "engine_hours_total": (float(baseline) + counted_from_baseline
                               if baseline is not None and counted_from_baseline is not None
                               else None),
        "outing_list": [(_local(a, tz).isoformat(), (b - a) / 3600.0) for a, b in ledger.outings],
    }


def hours_since(db, when: date, tz: TzInfo = DEFAULT_TZ) -> float:
    """Running hours since a date, in local time. For 'how much since the service'."""
    cutoff = int(datetime(when.year, when.month, when.day, tzinfo=tz).timestamp())
    return integrate(load(db, cutoff), tz).running_h


def render(data: dict) -> str:
    lines = []
    if data["synthetic"]:
        lines.append("⚠  SYNTHETIC DATA — this log was fabricated by engine_seed.py. "
                     "No engine ran.")
        lines.append("")

    if not data["samples"]:
        return "\n".join(lines + ["No samples. Nothing has been logged yet."])

    lines.append(f"Logged      {data['first'][:16]} → {data['last'][:16]}")
    lines.append(f"Samples     {data['samples']:,}"
                 + (f"  ({data['stale_samples']:,} with no usable engine reading)"
                    if data["stale_samples"] else ""))
    lines.append("")
    lines.append(f"RUNNING     {data['running_h']:8.1f} h   over {data['outings']} outings")
    lines.append(f"UNKNOWN     {data['unknown_h']:8.1f} h   nobody was watching")
    lines.append(f"COVERAGE    {data['coverage'] * 100:8.1f} %   of the logged span was witnessed")
    lines.append("")

    if data["engine_hours_total"] is not None:
        lines.append(f"ENGINE HOURS ≈ {data['engine_hours_total']:.1f} h "
                     f"({data['baseline_hours']:.1f} h read off the display on "
                     f"{data['baseline_at']}, plus counted since)")
    else:
        lines.append("ENGINE HOURS  unknown. This meter only knows hours since logging began.")
        lines.append("              Photograph the helm hour display and record it:")
        lines.append("                  engine_hours.py --baseline <hours> "
                     "--baseline-at YYYY-MM-DD")

    if data["by_season"]:
        lines.append("")
        lines.append("Per season")
        for season, value in data["by_season"].items():
            lines.append(f"  {season}   {value:7.1f} h")

    if data["by_month"]:
        lines.append("")
        lines.append("Per month")
        for month, value in data["by_month"].items():
            bar = "█" * min(40, int(value * 2))
            lines.append(f"  {month}  {value:6.1f} h  {bar}")

    return "\n".join(lines)


def main() -> None:
    from .profile import load as load_profile

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--profile", help="path to a boat.toml (default: the usual search)")
    parser.add_argument("--since", help="YYYY-MM-DD — count only from this date")
    parser.add_argument("--baseline", type=float,
                        help="hours read off the helm display; records it and exits")
    parser.add_argument("--baseline-at", help="YYYY-MM-DD the display was photographed")
    args = parser.parse_args()

    tz = ZoneInfo(load_profile(args.profile).timezone)
    db = connect(args.db)

    if args.baseline is not None:
        if not args.baseline_at:
            parser.error("--baseline needs --baseline-at: an hour count with no date is useless")
        meta_set(db, "baseline_hours", args.baseline)
        meta_set(db, "baseline_at", args.baseline_at)
        print(f"recorded: {args.baseline} h on the display at {args.baseline_at}")

    since = None
    if args.since:
        since = int(datetime.fromisoformat(args.since).replace(tzinfo=tz).timestamp())
        print(f"(counting from {args.since} only)\n")

    print(render(summary(db, since, tz)))
    db.close()


if __name__ == "__main__":
    main()
