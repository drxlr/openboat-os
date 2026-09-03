#!/usr/bin/env python3
"""What the boat costs, and what that works out to per hour of using it.

    python3 -m openboat.ledger                      # the picture
    python3 -m openboat.ledger --add 1250 service "impeller and manifold gaskets"
    python3 -m openboat.ledger --year 2026

Boats are bought on the price and owned on everything else. The berth, the insurance, the
lift-out, the antifoul, the impeller, the thing that broke in August — each is a defensible
number on its own, and almost nobody adds them up, because the adding up happens across a
bank statement, a folder of invoices and a memory.

This is the adding up. One append-only file, the same shape as the check log, with one
number computed from it that is hard to get any other way and changes how people feel about
their boat:

    **cost per engine hour.**

Twelve thousand a year is an abstraction. Four hundred euro every time you leave the berth
is a decision. Both are the same fact. The second one is the one that tells you whether to
use the boat more or sell it, and it needs exactly two things this project already has: the
ledger, and the engine hours out of the boat's own log.

## What it is not

Not accounting. There is no VAT handling, no double entry, no depreciation, no currency
conversion — amounts are recorded in the currency they were paid in and totalled per
currency, because a made-up exchange rate is a made-up number and this project does not
make up numbers. If you need books, this is the raw material for them, not a substitute.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

#: What money on a boat is actually spent on. Deliberately short: a taxonomy nobody can
#: remember gets used as "other" for everything, and then it tells you nothing.
CATEGORIES = ("purchase", "berth", "insurance", "fuel", "service", "parts",
              "registration", "improvement", "other")

#: The ones that arrive whether or not the boat ever leaves the berth. Separating these is
#: the whole point of the summary: they are the cost of *owning*, not of *going out*.
FIXED = ("berth", "insurance", "registration")


@dataclass
class Item:
    amount: float
    category: str = "other"
    what: str = ""
    currency: str = "EUR"
    on: str = ""
    vendor: str = ""
    hours: float | None = None
    at: str = ""
    refs: list = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.category not in CATEGORIES:
            raise ValueError(f"category must be one of {CATEGORIES}, not {self.category!r}")
        try:
            self.amount = float(self.amount)
        except (TypeError, ValueError):
            raise ValueError(f"{self.amount!r} is not an amount") from None
        self.on = self.on or date.today().isoformat()
        try:
            date.fromisoformat(self.on)
        except ValueError:
            raise ValueError(f"{self.on!r} is not a date; use YYYY-MM-DD") from None
        self.currency = (self.currency or "EUR").upper()[:3]
        self.at = self.at or datetime.now(timezone.utc).isoformat(timespec="seconds")

    def line(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


def path_for(boat=None) -> Path:
    from .profile import load as load_profile
    boat = boat or load_profile()
    p = Path(getattr(boat, "ledger", "ledger.jsonl")).expanduser()
    if p.is_absolute():
        return p
    return (boat.path.parent if boat.path else Path.cwd()) / p


def record(amount, category="other", what="", currency="EUR", on="", vendor="",
           hours=None, refs=None, boat=None) -> Item:
    """Append one cost. `hours` is the engine hour reading at the time, if it is known."""
    item = Item(amount=amount, category=category, what=what, currency=currency,
                on=on, vendor=vendor, hours=hours, refs=list(refs or []))
    target = path_for(boat)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(item.line() + "\n")
    return item


def items(boat=None, year: str = "", category: str = "") -> list[dict]:
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
        if year and not str(row.get("on", "")).startswith(str(year)):
            continue
        if category and row.get("category") != category:
            continue
        out.append(row)
    return sorted(out, key=lambda r: r.get("on", ""))


def summary(boat=None, year: str = "") -> dict:
    """Totals per currency, split fixed against variable, and the per-hour figure.

    The purchase price is excluded from the running total on purpose. It is a real cost and
    it is in the file, but averaging it into "what this year cost" makes the first year look
    catastrophic and every later one look free, and neither is the number anybody wants.
    """
    rows = items(boat=boat, year=year)
    if not rows:
        return {"entries": 0, "year": year or "all", "currencies": {}}

    per_currency: dict[str, dict] = {}
    for row in rows:
        cur = row.get("currency", "EUR")
        bucket = per_currency.setdefault(cur, {
            "total": 0.0, "running": 0.0, "fixed": 0.0, "variable": 0.0,
            "purchase": 0.0, "by_category": {}})
        amount = float(row.get("amount", 0) or 0)
        category = row.get("category", "other")
        bucket["total"] += amount
        bucket["by_category"][category] = bucket["by_category"].get(category, 0.0) + amount
        if category == "purchase":
            bucket["purchase"] += amount
            continue
        bucket["running"] += amount
        bucket["fixed" if category in FIXED else "variable"] += amount

    hours = _hours_used(rows, boat)
    for bucket in per_currency.values():
        for key in ("total", "running", "fixed", "variable", "purchase"):
            bucket[key] = round(bucket[key], 2)
        bucket["by_category"] = {k: round(v, 2) for k, v in
                                 sorted(bucket["by_category"].items(), key=lambda kv: -kv[1])}
        bucket["per_engine_hour"] = (round(bucket["running"] / hours, 2)
                                     if hours else None)
    return {"entries": len(rows), "year": year or "all",
            "engine_hours": hours, "currencies": per_currency}


def _hours_used(rows: list[dict], boat) -> float | None:
    """Engine hours across the period, from whichever source actually knows.

    Two sources, and neither is invented: the readings recorded against ledger entries, and
    the boat's own check log, which captures the hour meter with every check. If the span
    cannot be established from real readings, this returns None and the per-hour figure is
    simply absent — a cost per hour built on a guessed denominator is worse than no cost per
    hour at all.
    """
    readings = [float(r["hours"]) for r in rows if r.get("hours") is not None]
    try:
        from . import logbook
        readings += [float(e["readings"]["engine_hours"])
                     for e in logbook.entries(boat=boat)
                     if (e.get("readings") or {}).get("engine_hours") is not None]
    except Exception:
        pass
    if len(readings) < 2:
        return None
    span = max(readings) - min(readings)
    return round(span, 1) if span > 0 else None


def _fmt(value, currency="EUR") -> str:
    return f"{value:,.0f} {currency}"


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--add" in argv:
        i = argv.index("--add")
        rest = argv[i + 1:]
        if len(rest) < 2:
            print("usage: --add AMOUNT CATEGORY [what] [--on YYYY-MM-DD]", file=sys.stderr)
            raise SystemExit(2)
        on = ""
        if "--on" in rest:
            j = rest.index("--on")
            on = rest[j + 1] if len(rest) > j + 1 else ""
            rest = rest[:j]
        item = record(rest[0], rest[1], " ".join(rest[2:]), on=on)
        print(f"logged {_fmt(item.amount, item.currency)} — {item.category}"
              f"{' — ' + item.what if item.what else ''} on {item.on}")
        raise SystemExit(0)

    year = argv[argv.index("--year") + 1] if "--year" in argv else ""
    report = summary(year=year)
    if not report["entries"]:
        print(f"Nothing recorded. {path_for()}")
        raise SystemExit(0)

    print(f"{report['entries']} entries, {report['year']}"
          + (f", {report['engine_hours']} engine hours" if report["engine_hours"] else ""))
    for currency, bucket in report["currencies"].items():
        print(f"\n  running cost   {_fmt(bucket['running'], currency)}")
        print(f"    fixed        {_fmt(bucket['fixed'], currency)}   berth, insurance, papers")
        print(f"    variable     {_fmt(bucket['variable'], currency)}   fuel, service, parts")
        if bucket["purchase"]:
            print(f"  purchase       {_fmt(bucket['purchase'], currency)}   (not in the running total)")
        if bucket["per_engine_hour"] is not None:
            print(f"\n  \033[1mper engine hour  {_fmt(bucket['per_engine_hour'], currency)}\033[0m")
        else:
            print("\n  per engine hour  not known — needs two engine-hour readings "
                  "(the check log captures them)")
        print("\n  " + "  ".join(f"{k} {v:,.0f}" for k, v in bucket["by_category"].items()))
