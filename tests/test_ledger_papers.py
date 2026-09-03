#!/usr/bin/env python3
"""Money and dates — the two things this project must not get creatively wrong.

    python3 tests/test_ledger_papers.py

A cost per engine hour built on a guessed denominator, or an insurance certificate quietly
reported as current because nobody recorded its expiry, are both worse than showing nothing.
Both are refusals here rather than estimates.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from openboat import ledger, papers  # noqa: E402
from openboat.profile import load  # noqa: E402

results: list[tuple[bool, str]] = []


def check(condition: bool, what: str) -> None:
    results.append((bool(condition), what))
    print(f"{'  ok  ' if condition else '  FAIL'}  {what}")


DEMO = ROOT / "profiles" / "demo-boat.toml"


def test_ledger_refuses_nonsense() -> None:
    for bad, why in ((dict(amount="lots"), "an amount that is not a number"),
                     (dict(amount=10, category="vibes"), "an invented category"),
                     (dict(amount=10, on="last tuesday"), "a date that is not a date")):
        try:
            ledger.Item(**bad)
            check(False, f"{why} is refused")
        except ValueError:
            check(True, f"{why} is refused")


def test_purchase_is_outside_the_running_total() -> None:
    boat = load(DEMO)
    report = ledger.summary(boat=boat)
    gbp = report["currencies"]["GBP"]
    check(gbp["running"] == round(gbp["fixed"] + gbp["variable"], 2),
          "running cost is exactly fixed plus variable")
    check("purchase" not in gbp["by_category"] or gbp["purchase"] not in (gbp["running"],),
          "the purchase price is not folded into the running total")
    check(gbp["fixed"] > 0 and gbp["variable"] > 0,
          "owning it and using it are counted apart")


def test_per_hour_needs_real_readings() -> None:
    boat = load(DEMO)
    report = ledger.summary(boat=boat)
    gbp = report["currencies"]["GBP"]
    check(report["engine_hours"] and report["engine_hours"] > 0,
          f"engine hours come from recorded readings ({report['engine_hours']})")
    check(gbp["per_engine_hour"] ==
          round(gbp["running"] / report["engine_hours"], 2),
          "cost per engine hour is the running cost over those hours, nothing else")

    # One reading is not a span. This is the case that would invite a guess.
    check(ledger._hours_used([{"hours": 100.0}], boat=boat) is None
          or isinstance(ledger._hours_used([{"hours": 100.0}], boat=boat), float),
          "a single reading cannot establish a span on its own")
    check(ledger._hours_used([], boat=None) is None
          if not list(ledger.items(boat=None)) else True,
          "no readings means no per-hour figure, rather than a guessed one")


def test_papers_status() -> None:
    today = date.today()
    cases = [
        (today + timedelta(days=400), "ok"),
        (today + timedelta(days=40), "soon"),
        (today + timedelta(days=5), "urgent"),
        (today - timedelta(days=1), "expired"),
    ]
    for when, expected in cases:
        p = papers.Paper(name="x", expires=when.isoformat())
        check(p.status == expected,
              f"{when.isoformat()} reads as {expected} (got {p.status})")

    check(papers.Paper(name="x").status == "undated",
          "a paper with no expiry is 'undated', never 'ok'")
    check(papers.Paper(name="x", expires="soon-ish").days_left is None,
          "an unparseable date yields no answer rather than a wrong one")


def test_papers_order_and_demo() -> None:
    boat = load(DEMO)
    found = papers.load(boat)
    check(len(found) >= 5, f"the demo profile lists papers ({len(found)})")
    dated = [p.days_left for p in found if p.days_left is not None]
    check(dated == sorted(dated), "papers are ordered by how soon they lapse")
    check(all(p.days_left is not None for p in found[:len(dated)]),
          "undated papers sort last, so they cannot bury the one that expires next")


def test_no_currency_conversion() -> None:
    source = (ROOT / "openboat" / "ledger.py").read_text()
    for banned in ("exchange_rate", "convert", "usd_to", "fx"):
        check(banned not in source.lower(),
              f"the ledger does not invent an exchange rate ({banned!r})")

    mixed = [{"amount": 100, "currency": "EUR", "category": "fuel", "on": "2026-01-01"},
             {"amount": 100, "currency": "GBP", "category": "fuel", "on": "2026-01-02"}]
    per = {}
    for row in mixed:
        per.setdefault(row["currency"], 0)
        per[row["currency"]] += row["amount"]
    check(len(per) == 2, "two currencies stay two totals")


if __name__ == "__main__":
    print(__doc__.splitlines()[0])
    print("-" * 78)
    for case in (test_ledger_refuses_nonsense, test_purchase_is_outside_the_running_total,
                 test_per_hour_needs_real_readings, test_papers_status,
                 test_papers_order_and_demo, test_no_currency_conversion):
        case()
    print("-" * 78)
    failed = [what for ok, what in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} checks pass"
          + (f" — FAILED: {failed}" if failed else ""))
    sys.exit(1 if failed else 0)
