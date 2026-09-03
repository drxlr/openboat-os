"""Trend detection: the three things that go wrong slowly enough to be missed.

    python3 -m openboat.engine_health
    python3 -m openboat.engine_health --db /tmp/e.db

A raw temperature chart is close to useless on a raw-water-cooled boat, and it is worth
being precise about why, because that is the whole reason this file is not a line graph.

## 1. Coolant — the seasonal wave has to come out first

A raw-water-cooled engine has no heat exchanger: seawater runs straight through the block,
manifolds and risers. Two things follow.

- **The sea leaks into the reading.** Depending on where a boat lives, the sea can swing
  ten degrees or more between winter and summer. A thermostat regulates part of that away
  and load pushes past it, so the engine feels *some* fraction of the sea — how much is a
  property of the particular engine that nobody has measured. Meanwhile the fault worth
  catching — a partly blocked manifold passage, a tired impeller, debris in a raw-water
  passage — is a couple of degrees. **The confound is bigger than the signal.**
- **Load sets the height.** Coolant temperature is a strong function of rpm, so comparing
  a 2,500 rpm sample against a 3,500 rpm one says nothing at all. Every comparison happens
  inside an rpm band, and the bands below are a reasonable default split for a planing
  motor cruiser — override `RPM_BANDS` with the bands your own boat is actually driven in.

Subtracting the sea temperature would be the obvious fix and it is the wrong one: it
assumes the engine tracks the sea one-for-one, and a thermostat means it does not. So
instead, inside each band, coolant temperature is fitted against **two** variables at once
— days elapsed and sea temperature:

    coolant = a + b₁·days + b₂·sea

`b₁` is then the drift *at constant sea temperature*, which is the number that matters, and
`b₂` falls out as a free diagnostic: it says how much of the sea this engine actually
feels. The charts plot every outing corrected to one reference sea temperature using `b₂`,
which is what makes a two-degree drift visible at all. That reference temperature is not a
guess baked into this file — see `cooling()` — it is the median sea temperature actually
observed in the log, so the correction is interpolation across the boat's own data rather
than extrapolation from somebody else's climate.

What a restriction looks like: the same band, the same sea, warmer month after month.

## 2. Oil pressure — idle against cruise

Cruise pressure is held by the relief valve and stays flat long after something is wrong.
Idle pressure is not: the pump is slow and hot thin oil escapes through whatever clearance
exists, so wear shows up **at idle first and by years**. The useful observation is not
either pressure but the pair — idle falling while cruise holds.

## 3. Battery — at rest, and while charging

Resting voltage, read long enough after shutdown for the surface charge to decay, is the
battery's state of health. Voltage while running is the alternator's. They fail
differently, so they are reported separately.

## What makes a verdict meaningful — the sample-size rule

**One outing is one observation, not three hundred.** Samples ten seconds apart on the
same afternoon share the same sea, the same fouling, the same weather and the same fuel;
treating them as independent would shrink the standard error by a large factor and
manufacture certainty out of one boat trip. So every band is reduced to **one median per
outing** before anything is fitted, and `n` in every verdict below counts outings.

A trend is reported only when all three hold:

- at least **6 outings** in the band,
- spanning at least **60 days**,
- and a coefficient at least **twice its own standard error**.

Below that the honest output is *not enough data yet*, and this file says so rather than
drawing a confident line through four points.
"""

from __future__ import annotations

import argparse
import math
from bisect import bisect_right
from datetime import datetime, timezone, tzinfo as TzInfo
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median

from . import engine_hours as hourmeter
from .engine import DEFAULT_DB, STALE_AFTER_S, connect, meta_get

# A reasonable default split for a planing motor cruiser: below planing, an efficient
# cruise, a comfortable cruise, and a fast cruise. Not measured on any particular boat —
# override with the bands yours is actually driven in.
RPM_BANDS = [
    ("idle",           400, 1000),
    ("1000–2200",     1000, 2200),
    ("2200–2800",     2200, 2800),
    ("2800–3400",     2800, 3400),
    ("3400+",         3400, 6000),
]
CRUISE_MIN_RPM = 2200.0

# A cold engine climbing to thermostat temperature is not a data point about cooling.
# Ten minutes from the moment it fires; the thermostat is open well before that.
WARMUP_S = 600.0

# Surface charge takes a while to fall off. A voltage read sooner than this after shutdown
# is the alternator's leftovers, not the battery's state.
REST_AFTER_STOP_S = 1800.0

MIN_OUTINGS = 6
MIN_SPAN_DAYS = 60.0
SIGNIFICANT_T = 2.0        # a coefficient must be at least twice its own standard error

# Thresholds on the fitted drift, per 30 days. Deliberately modest: on a raw-water engine
# a degree a month is a great deal, because nothing about the cooling should change at all.
COOLING_WATCH_C_PER_MONTH = 0.4
COOLING_BAD_C_PER_MONTH = 1.0
OIL_WATCH_BAR_PER_YEAR = -0.20
OIL_BAD_BAR_PER_YEAR = -0.50
VOLTS_WATCH_PER_YEAR = -0.15


@dataclass
class Fit:
    """A least-squares fit and enough about it to know whether to believe it."""

    n: int
    slope: float = 0.0              # y units per day, holding any covariate constant
    intercept: float = 0.0
    stderr: float = float("inf")    # standard error of the slope
    span_days: float = 0.0
    scatter: float = 0.0            # residual standard deviation, in y units
    covariate: float | None = None  # b₂ — how much of the sea the engine feels

    @property
    def significant(self) -> bool:
        return (self.n >= MIN_OUTINGS
                and self.span_days >= MIN_SPAN_DAYS
                and self.stderr not in (0.0, float("inf"))
                and abs(self.slope) >= SIGNIFICANT_T * self.stderr)

    @property
    def why_not(self) -> str:
        if self.n < MIN_OUTINGS:
            return f"only {self.n} outing{'s' if self.n != 1 else ''} here, {MIN_OUTINGS} needed"
        if self.span_days < MIN_SPAN_DAYS:
            return f"only {self.span_days:.0f} days of history, {MIN_SPAN_DAYS:.0f} needed"
        return "the scatter between outings is as large as the drift"


@dataclass
class Finding:
    """One question answered in one sentence, with the numbers behind it."""

    title: str
    verdict: str               # good | watch | bad | unknown
    headline: str
    detail: list[str] = field(default_factory=list)
    series: dict = field(default_factory=dict)   # label -> [(YYYY-MM-DD, value), ...]
    unit: str = ""


def _solve(matrix: list[list[float]], vector: list[float]) -> tuple[list[float], list[list[float]]]:
    """Gauss-Jordan: returns the solution and the inverse. Small, dense, stdlib-only.

    The inverse is not a luxury — the standard error of each coefficient comes out of its
    diagonal, and without a standard error a slope is a decoration.
    """
    size = len(matrix)
    work = [row[:] + [1.0 if i == j else 0.0 for j in range(size)] + [vector[i]]
            for i, row in enumerate(matrix)]

    for column in range(size):
        pivot = max(range(column, size), key=lambda r: abs(work[r][column]))
        if abs(work[pivot][column]) < 1e-12:
            raise ZeroDivisionError("singular — the inputs do not vary independently")
        work[column], work[pivot] = work[pivot], work[column]
        scale = work[column][column]
        work[column] = [v / scale for v in work[column]]
        for row in range(size):
            if row == column:
                continue
            factor = work[row][column]
            if factor:
                work[row] = [a - factor * b for a, b in zip(work[row], work[column])]

    solution = [work[i][-1] for i in range(size)]
    inverse = [work[i][size:size + size] for i in range(size)]
    return solution, inverse


def fit(rows: list[tuple[float, ...]], columns: int) -> Fit:
    """Least squares of the last column on an intercept plus the first `columns` columns.

    `columns == 1` is an ordinary trend line; `columns == 2` adds sea temperature as a
    covariate so the reported slope is drift at constant sea.
    """
    n = len(rows)
    terms = columns + 1
    if n <= terms:
        return Fit(n=n)

    design = [[1.0] + list(row[:columns]) for row in rows]
    values = [row[columns] for row in rows]

    xtx = [[sum(design[k][i] * design[k][j] for k in range(n)) for j in range(terms)]
           for i in range(terms)]
    xty = [sum(design[k][i] * values[k] for k in range(n)) for i in range(terms)]

    try:
        beta, inverse = _solve(xtx, xty)
    except ZeroDivisionError:
        return Fit(n=n)

    residuals = [values[k] - sum(beta[i] * design[k][i] for i in range(terms)) for k in range(n)]
    variance = sum(r * r for r in residuals) / (n - terms)
    scatter = math.sqrt(variance)
    stderr = math.sqrt(max(variance * inverse[1][1], 0.0))

    days = [row[0] for row in rows]
    return Fit(n=n, slope=beta[1], intercept=beta[0], stderr=stderr,
               span_days=max(days) - min(days), scatter=scatter,
               covariate=beta[2] if columns >= 2 else None)


def _local(t: float, tz: TzInfo) -> datetime:
    return datetime.fromtimestamp(t, tz=timezone.utc).astimezone(tz)


def _day(t: float, tz: TzInfo) -> str:
    return _local(t, tz).strftime("%Y-%m-%d")


def outing_medians(db, tz: TzInfo = hourmeter.DEFAULT_TZ) -> list[dict]:
    """One row per outing, each figure a median over the warmed-up part of that outing.

    This is the sample-size argument made concrete: everything downstream fits lines
    through *these* rows and never through raw samples.
    """
    ledger = hourmeter.integrate(hourmeter.load(db), tz)
    if not ledger.outings:
        return []

    starts = [start for start, _ in ledger.outings]
    buckets: list[dict] = [{"start": start, "end": end, "rows": []}
                           for start, end in ledger.outings]

    sql = ("SELECT t, rpm, temp_c, oil_kpa, volts, sea_c FROM samples "
           "WHERE (engine_age_s IS NULL OR engine_age_s <= ?) ORDER BY t")
    for t, rpm, temp_c, oil_kpa, volts, sea_c in db.execute(sql, (STALE_AFTER_S,)):
        index = bisect_right(starts, t) - 1
        if index < 0 or t > buckets[index]["end"]:
            continue                                   # between outings — handled elsewhere
        if t - buckets[index]["start"] < WARMUP_S or rpm is None:
            continue                                   # still warming up, or nothing measured
        buckets[index]["rows"].append(
            {"rpm": rpm, "temp_c": temp_c, "oil_kpa": oil_kpa, "volts": volts, "sea_c": sea_c})

    outings = []
    for bucket in buckets:
        rows = bucket["rows"]
        if not rows:
            continue
        entry = {"start": bucket["start"], "bands": {}}
        for name, low, high in RPM_BANDS:
            inside = [r for r in rows if low <= r["rpm"] < high]
            if not inside:
                continue
            temps = [r["temp_c"] for r in inside if r["temp_c"] is not None]
            seas = [r["sea_c"] for r in inside if r["sea_c"] is not None]
            oils = [r["oil_kpa"] for r in inside if r["oil_kpa"] is not None]
            entry["bands"][name] = {
                "n": len(inside),
                "temp_c": median(temps) if temps else None,
                "sea_c": median(seas) if seas else None,
                "oil_kpa": median(oils) if oils else None,
            }
        charging = [r["volts"] for r in rows if r["volts"] is not None and r["rpm"] > 1200]
        entry["charging_v"] = median(charging) if charging else None
        outings.append(entry)
    return outings


def resting_volts(db) -> list[tuple[float, float]]:
    """(unix second, volts) for samples taken well after the engine last stopped.

    'At rest' is doing work here: a battery read two minutes after shutdown reads the
    alternator's surface charge and looks healthy whatever its true state.
    """
    rows = db.execute(
        "SELECT t, rpm, volts FROM samples "
        "WHERE volts IS NOT NULL AND (engine_age_s IS NULL OR engine_age_s <= ?) ORDER BY t",
        (STALE_AFTER_S,)).fetchall()

    last_running = None
    resting = []
    for t, rpm, volts in rows:
        if rpm is not None and rpm > hourmeter.IDLE_RPM:
            last_running = t
            continue
        if last_running is not None and t - last_running < REST_AFTER_STOP_S:
            continue
        resting.append((float(t), volts))
    return resting


def _daily(points: list[tuple[float, float]], tz: TzInfo) -> list[tuple[float, float]]:
    """One median per local day — the same independence argument as outings."""
    days: dict[str, list[tuple[float, float]]] = {}
    for t, value in points:
        days.setdefault(_day(t, tz), []).append((t, value))
    return [(min(t for t, _ in group), median([v for _, v in group]))
            for _, group in sorted(days.items())]


def _observed_sea_reference(outings: list[dict]) -> float | None:
    """Median sea temperature actually seen in the log — the correction target.

    Not a constant baked into this file: 23 °C is a reasonable reference for one sea and a
    wrong one for another. Using the log's own median means the correction in `cooling()`
    is interpolation across data this boat actually recorded, never extrapolation from a
    climate this file has never been told about.
    """
    seas = [band["sea_c"] for outing in outings for band in outing["bands"].values()
            if band["sea_c"] is not None]
    return median(seas) if seas else None


def cooling(outings: list[dict], sea_reference_c: float | None = None) -> list[Finding]:
    """Coolant drift at constant sea temperature, band by band. The headline check here.

    `sea_reference_c` defaults to the median sea temperature observed across all outings —
    see `_observed_sea_reference`. Pass an explicit value to pin every chart to the same
    reference across repeated calls, or if you want the correction anchored somewhere
    specific (a survey date's sea temperature, say).
    """
    if sea_reference_c is None:
        sea_reference_c = _observed_sea_reference(outings)

    findings = []
    for name, _, _ in RPM_BANDS:
        if name == "idle":
            continue    # at idle the reading is dominated by how long she sat, not by flow

        rows, raw = [], []
        for outing in outings:
            band = outing["bands"].get(name)
            if not band or band["temp_c"] is None:
                continue
            rows.append((outing["start"] / 86400.0, band["sea_c"], band["temp_c"]))
            raw.append((_day(outing["start"], hourmeter.DEFAULT_TZ), round(band["temp_c"], 2)))
        if len(rows) < 3:
            continue

        origin = min(row[0] for row in rows)
        have_sea = all(row[1] is not None for row in rows) and sea_reference_c is not None

        if have_sea:
            model = fit([(row[0] - origin, row[1], row[2]) for row in rows], columns=2)
        else:
            model = fit([(row[0] - origin, row[2]) for row in rows], columns=1)

        # The chart: every outing pulled to one reference sea temperature. Without this the
        # seasonal wave is the only thing on the page.
        corrected = []
        if have_sea and model.covariate is not None:
            for (t, sea, temp), (stamp, _) in zip(rows, raw):
                corrected.append((stamp, round(temp - model.covariate * (sea - sea_reference_c), 2)))

        per_month = model.slope * 30.0
        if not model.significant:
            verdict = "unknown"
            headline = f"No verdict at {name} rpm — {model.why_not}."
        elif per_month >= COOLING_BAD_C_PER_MONTH:
            verdict = "bad"
            headline = (f"At {name} rpm the engine runs {per_month:.1f} °C hotter every month "
                        f"at the same sea temperature. That is the shape of a raw-water "
                        f"restriction, not of a season.")
        elif per_month >= COOLING_WATCH_C_PER_MONTH:
            verdict = "watch"
            headline = (f"At {name} rpm coolant is creeping up {per_month:.1f} °C a month at "
                        f"constant sea temperature. Small, but it only goes one way — worth a "
                        f"look at the impeller and the manifold passages.")
        elif per_month <= -COOLING_WATCH_C_PER_MONTH:
            verdict = "good"
            headline = (f"At {name} rpm coolant has fallen {abs(per_month):.1f} °C a month — "
                        f"what you would expect after cooling work.")
        else:
            verdict = "good"
            headline = (f"At {name} rpm coolant is flat to within {abs(per_month):.2f} °C a "
                        f"month across {model.n} outings.")

        detail = [f"{model.n} outings over {model.span_days:.0f} days",
                  f"latest median: {raw[-1][1]:.1f} °C",
                  f"scatter between outings after correction: ±{model.scatter:.1f} °C"]
        if have_sea and model.covariate is not None:
            detail.append(f"this engine follows the sea at {model.covariate:.2f} °C per °C — "
                          f"a thermostat regulating part of it away")
        else:
            detail.append("⚠ No seawater temperature logged, so this is uncorrected and carries "
                          "the full annual swing. Treat any trend here with suspicion.")

        series = ({f"corrected to a {sea_reference_c:.0f} °C sea": corrected}
                  if corrected else {})
        series["as measured"] = raw

        findings.append(Finding(title=f"Cooling at {name} rpm", verdict=verdict,
                                headline=headline, detail=detail, series=series, unit="°C"))
    return findings


def oil(outings: list[dict]) -> Finding:
    """Idle pressure against cruise pressure. Idle is the one that moves first."""
    idle_rows, cruise_rows, idle_series, cruise_series = [], [], [], []
    for outing in outings:
        band = outing["bands"].get("idle")
        if band and band["oil_kpa"] is not None:
            bar = band["oil_kpa"] / 100.0
            idle_rows.append((outing["start"] / 86400.0, bar))
            idle_series.append((_day(outing["start"], hourmeter.DEFAULT_TZ), round(bar, 2)))
        cruise = [outing["bands"][name]["oil_kpa"] for name, low, _ in RPM_BANDS
                  if low >= CRUISE_MIN_RPM and name in outing["bands"]
                  and outing["bands"][name]["oil_kpa"] is not None]
        if cruise:
            bar = median(cruise) / 100.0
            cruise_rows.append((outing["start"] / 86400.0, bar))
            cruise_series.append((_day(outing["start"], hourmeter.DEFAULT_TZ), round(bar, 2)))

    if not idle_rows and not cruise_rows:
        return Finding(title="Oil pressure", verdict="unknown",
                       headline="No oil pressure in the log at all.",
                       detail=["The sender is not wired, or Signal K carries no "
                               "`propulsion.*.oilPressure` path."])

    origin = min(row[0] for row in idle_rows + cruise_rows)
    idle_fit = fit([(t - origin, v) for t, v in idle_rows], columns=1)
    cruise_fit = fit([(t - origin, v) for t, v in cruise_rows], columns=1)
    per_year = idle_fit.slope * 365.0

    if not idle_fit.significant:
        verdict = "unknown"
        headline = f"No verdict on idle oil pressure — {idle_fit.why_not}."
    elif per_year <= OIL_BAD_BAR_PER_YEAR:
        verdict = "bad"
        headline = (f"Idle oil pressure is falling {abs(per_year):.2f} bar a year. Confirm it "
                    f"against a mechanical gauge before believing the sender, then look at "
                    f"bearing clearances.")
    elif per_year <= OIL_WATCH_BAR_PER_YEAR:
        verdict = "watch"
        headline = (f"Idle oil pressure is drifting down {abs(per_year):.2f} bar a year while "
                    f"cruise pressure holds — the ordinary shape of wear, worth watching.")
    else:
        verdict = "good"
        headline = (f"Idle oil pressure is steady to within {abs(per_year):.2f} bar a year "
                    f"across {idle_fit.n} outings.")

    detail = []
    if idle_series:
        detail.append(f"latest idle {idle_series[-1][1]:.2f} bar "
                      f"({idle_series[-1][1] * 14.5038:.0f} psi) over {idle_fit.n} outings")
    if cruise_series:
        detail.append(f"latest cruise {cruise_series[-1][1]:.2f} bar "
                      f"({cruise_series[-1][1] * 14.5038:.0f} psi), trend "
                      f"{cruise_fit.slope * 365.0:+.2f} bar/year")
    detail.append("Cruise pressure is held by the relief valve and moves last; idle moves first.")

    return Finding(title="Oil pressure", verdict=verdict, headline=headline, detail=detail,
                   series={"idle": idle_series, "cruise": cruise_series}, unit="bar")


def battery(db, outings: list[dict], tz: TzInfo = hourmeter.DEFAULT_TZ) -> Finding:
    """Resting voltage is the battery; charging voltage is the alternator."""
    resting = _daily(resting_volts(db), tz)
    charging = [(o["start"], o["charging_v"]) for o in outings if o["charging_v"] is not None]
    charge_series = [(_day(t, tz), round(v, 2)) for t, v in charging]

    if not resting:
        return Finding(
            title="Battery", verdict="unknown",
            headline="No resting voltage in the log.",
            detail=[f"A reading counts as at rest only {REST_AFTER_STOP_S / 60:.0f} min after "
                    f"the engine stops — before that it is the alternator's surface charge. "
                    f"Either the logger only runs under way, or there is no voltage sender."],
            series={"charging": charge_series}, unit="V")

    origin = min(t for t, _ in resting) / 86400.0
    model = fit([(t / 86400.0 - origin, v) for t, v in resting], columns=1)
    per_year = model.slope * 365.0
    latest = resting[-1][1]

    if latest < 12.2:
        verdict = "bad"
        headline = (f"Resting voltage is {latest:.2f} V. Below about 12.2 V a lead-acid bank is "
                    f"sitting at half charge or worse, and it will not survive that for long.")
    elif not model.significant:
        verdict = "good" if latest >= 12.5 else "watch"
        headline = f"Resting voltage is {latest:.2f} V. No trend yet — {model.why_not}."
    elif per_year <= VOLTS_WATCH_PER_YEAR:
        verdict = "watch"
        headline = (f"Resting voltage is falling {abs(per_year):.2f} V a year and now reads "
                    f"{latest:.2f} V. That is a bank ageing, not a bad day.")
    else:
        verdict = "good"
        headline = f"Resting voltage is {latest:.2f} V and flat over {model.n} days."

    detail = [f"{model.n} days with a genuine at-rest reading, spanning {model.span_days:.0f} days"]
    if charging:
        detail.append(f"charging while running: median {median([v for _, v in charging]):.2f} V "
                      f"(a healthy alternator holds roughly 13.8–14.4 V)")
    detail.append("12.7 V ≈ full, 12.5 V ≈ 75 %, 12.2 V ≈ 50 % for a resting lead-acid bank.")

    return Finding(title="Battery", verdict=verdict, headline=headline, detail=detail,
                   series={"resting": [(_day(t, tz), round(v, 2)) for t, v in resting],
                           "charging": charge_series}, unit="V")


def analyse(db, tz: TzInfo = hourmeter.DEFAULT_TZ, sea_reference_c: float | None = None) -> dict:
    """Every finding, plus the context needed to judge them."""
    outings = outing_medians(db, tz)
    findings: list[Finding] = []
    if outings:
        findings.extend(cooling(outings, sea_reference_c))
        findings.append(oil(outings))
    findings.append(battery(db, outings, tz))

    return {
        "synthetic": meta_get(db, "synthetic") == "1",
        "outings": len(outings),
        "findings": findings,
        "rules": {"min_outings": MIN_OUTINGS, "min_span_days": MIN_SPAN_DAYS,
                  "warmup_s": WARMUP_S,
                  "sea_reference_c": (sea_reference_c if sea_reference_c is not None
                                      else _observed_sea_reference(outings))},
    }


def render(result: dict) -> str:
    mark = {"good": "✓", "watch": "▲", "bad": "✗", "unknown": "·"}
    lines = []
    if result["synthetic"]:
        lines += ["⚠  SYNTHETIC DATA — fabricated by engine_seed.py. No engine ran.", ""]
    lines.append(f"{result['outings']} outings analysed — one median per outing per rpm band")
    lines.append("")
    for finding in result["findings"]:
        lines.append(f"{mark[finding.verdict]} {finding.title.upper()}")
        lines.append(f"  {finding.headline}")
        for line in finding.detail:
            lines.append(f"    · {line}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    from .profile import load as load_profile
    from zoneinfo import ZoneInfo

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--profile", help="path to a boat.toml (default: the usual search)")
    args = parser.parse_args()
    tz = ZoneInfo(load_profile(args.profile).timezone)
    db = connect(args.db)
    print(render(analyse(db, tz)))
    db.close()


if __name__ == "__main__":
    main()
