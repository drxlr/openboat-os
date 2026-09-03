"""Fabricate a plausible multi-season log, so the rest of this module can be proved today.

    python3 -m openboat.engine_seed --db /tmp/engine-demo.db

⚠️  **EVERY NUMBER THIS FILE PRODUCES IS INVENTED.** No engine has run, no sender has been
    read, and nothing here is a measurement of any real boat. Each row is written with
    `source = 'synthetic'`, the database is stamped `synthetic = 1` in `meta`, and both
    `engine_hours.py` and the HTML report refuse to print a figure without saying so. That
    stamp is the only thing standing between a test fixture and a fabricated service
    record, so do not remove it, and never point this at a real log.

## Why it exists

Most people who clone this project will not have an engine sender wired up on day one, so
there is no real engine data to test `engine_health.py` against — and the arithmetic in
that file is the part most likely to be wrong, because hour meters fail at gaps and trend
detectors fail at confounds, neither of which shows up in a five-minute live test.

So this generates a boat's worth of behaviour: a couple of seasons, outings clustered in
the warmer months, a logger that is off between trips, the odd dropout mid-passage, and a
**slow cooling degradation** buried under a seasonal swing in seawater temperature. If
`engine_health.py` can find that, it can find a manifold silting up.

## The model, so nobody mistakes it for physics

Shapes chosen to be *plausible and awkward*, not accurate, and pitched at a temperate sea —
adjust `SEA_MEAN_C` and `SEA_SWING_C` if your own waters run warmer or colder:

| | |
|---|---|
| Seawater | sine, roughly 8 °C in late winter to 18 °C in late summer, invented for the demo |
| Coolant | thermostat floor, plus load, plus **0.55 °C per °C of sea** — a thermostat regulating away part of the sea, which is exactly the confound `engine_health.py` has to solve |
| Coolant lag | first-order, τ = 180 s, so a cold start looks like a cold start |
| The fault | from month 10, a restriction adding ~0.8 °C per month, scaled by load |
| Oil pressure | rises with rpm to a relief-valve ceiling; idle falls ~0.28 bar/year, cruise does not |
| Battery | 14.1 V charging, resting falling ~0.18 V/year from 12.75 V |
| Outings | 3–6 a month in season, 0–2 otherwise; rpm drawn from invented cruise bands |
| Logger | on ~35 min before start and ~50 min after shutdown, off in between, with a 1-in-20 mid-passage dropout |
"""

from __future__ import annotations

import argparse
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .engine import connect, meta_set

SAMPLE_S = 10                     # matches engine.py's default poll interval

# The season window is relative to today rather than a fixed calendar date, so a demo run
# years from now still looks current instead of quietly going stale.
SEASON_LENGTH_DAYS = 880           # a bit over two years

# --- the invented boat -----------------------------------------------------------------
SEA_MEAN_C, SEA_SWING_C, SEA_PEAK_DOY = 13.0, 5.0, 235
THERMO_FLOOR_C = 58.0             # raw-water engines run a low thermostat, ~60 °C
LOAD_RISE_C_PER_RPM = 0.0060      # coolant rise per rpm above idle
SEA_COUPLING = 0.55               # °C of coolant per °C of sea — the confound under test
TEMP_TAU_S = 180.0                # first-order thermal lag

FAULT_STARTS_MONTH = 10           # months into the record before the restriction begins
FAULT_C_PER_MONTH = 0.8           # at full load; scaled down at low rpm

OIL_BASE_BAR, OIL_PER_RPM, OIL_CEILING_BAR = 1.0, 0.0013, 4.5
OIL_IDLE_WEAR_BAR_PER_YEAR = 0.28
OIL_PER_C_ABOVE_70 = -0.006       # hot oil is thin oil

VOLTS_CHARGING, VOLTS_IDLE_CHARGING = 14.15, 13.90
VOLTS_REST_START, VOLTS_REST_PER_YEAR = 12.75, -0.18

# Invented cruise rpm choices for the demo — not measured on any boat. Pass your own
# boat's real cruise rpm range if you adapt this generator for a dry run of your own log.
CRUISE_RPM_CHOICES = [2400, 2600, 2900, 3100, 3300, 3600]
IDLE_RPM_TARGET = 700
HARBOUR_RPM = 1250


def sea_temperature(when: datetime) -> float:
    """A temperate annual sine. Invented, but a plausible size and phase for many coasts."""
    doy = when.timetuple().tm_yday
    return SEA_MEAN_C + SEA_SWING_C * math.cos(2 * math.pi * (doy - SEA_PEAK_DOY) / 365.0)


def fault_offset(months_in: float, rpm: float) -> float:
    """The restriction: nothing for the first months, then a linear climb, load-scaled."""
    if months_in < FAULT_STARTS_MONTH:
        return 0.0
    load = max(0.0, min(1.0, (rpm - 700) / 2800.0))
    return FAULT_C_PER_MONTH * (months_in - FAULT_STARTS_MONTH) * load


def plan_outings(rng: random.Random, season_start: datetime, season_end: datetime) -> list[datetime]:
    """Start times: busier in the warmer months, thinner the rest of the year.

    The 4–10 in-season window is the common northern-hemisphere boating season. If your
    own season runs on a different calendar — year-round in the tropics, say — adjust the
    `in_season` test below.
    """
    starts, cursor = [], season_start
    while cursor < season_end:
        in_season = 4 <= cursor.month <= 10
        for _ in range(rng.randint(3, 6) if in_season else rng.randint(0, 2)):
            day = rng.randint(1, 27)
            hour = rng.randint(9, 15)
            when = cursor.replace(day=day, hour=hour, minute=rng.choice([0, 15, 30, 45]))
            if season_start <= when < season_end:
                starts.append(when)
        cursor = (cursor.replace(day=1) + timedelta(days=32)).replace(day=1)
    return sorted(starts)


def rpm_profile(rng: random.Random) -> list[tuple[int, float]]:
    """(seconds, target rpm) for one outing, engine off represented as 0.

    A real trip and not a test ramp: warm-up at the berth, out through the harbour at no-wake
    speed, a passage, an anchor stop with the engine off, the passage back, and a cool-down
    on the lines.
    """
    return [
        (rng.randint(35, 45) * 60, 0.0),                       # logger on, engine cold
        (rng.randint(6, 10) * 60, IDLE_RPM_TARGET),            # warm-up
        (rng.randint(5, 8) * 60, HARBOUR_RPM),                 # out through the harbour
        (rng.randint(30, 70) * 60, rng.choice(CRUISE_RPM_CHOICES)),
        (rng.randint(25, 50) * 60, 0.0),                       # anchor, swim, engine off
        (rng.randint(30, 70) * 60, rng.choice(CRUISE_RPM_CHOICES)),
        (rng.randint(5, 8) * 60, HARBOUR_RPM),                 # back in through the harbour
        (rng.randint(4, 7) * 60, IDLE_RPM_TARGET),             # cool-down on the lines
        (rng.randint(40, 70) * 60, 0.0),                       # logger still on at the berth
    ]


def generate(rng: random.Random, season_start: datetime, season_end: datetime) -> list[tuple]:
    """Every sample of every outing, ready for one executemany."""
    rows: list[tuple] = []

    for start in plan_outings(rng, season_start, season_end):
        months_in = (start - season_start).days / 30.44
        years_in = (start - season_start).days / 365.25
        sea = sea_temperature(start) + rng.gauss(0, 0.4)

        # An outing-level offset: one afternoon's fuel, fouling, trim and swell. Without it
        # every outing would sit exactly on the model line and the significance test in
        # engine_health.py would never have to earn its verdict.
        day_effect = rng.gauss(0, 0.9)
        rest_volts = VOLTS_REST_START + VOLTS_REST_PER_YEAR * years_in + rng.gauss(0, 0.04)

        temp = sea + rng.gauss(0, 0.5)          # cold engine sits at seawater temperature
        clock = start
        dropout_at = None
        if rng.random() < 0.05:                 # 1 in 20: the logger falls over mid-passage
            dropout_at = rng.randint(40, 140) * 60

        elapsed = 0
        for duration, target in rpm_profile(rng):
            for _ in range(0, duration, SAMPLE_S):
                elapsed += SAMPLE_S
                clock += timedelta(seconds=SAMPLE_S)

                if dropout_at is not None and dropout_at <= elapsed < dropout_at + 1800:
                    continue                    # 30 minutes of nothing: a real gap, not a zero

                rpm = 0.0 if target == 0 else max(0.0, target + rng.gauss(0, 25))

                wanted = (THERMO_FLOOR_C
                          + LOAD_RISE_C_PER_RPM * max(0.0, rpm - IDLE_RPM_TARGET)
                          + SEA_COUPLING * (sea - SEA_MEAN_C)
                          + fault_offset(months_in, rpm)
                          + day_effect) if rpm > 0 else sea
                temp += (wanted - temp) * (SAMPLE_S / TEMP_TAU_S)

                if rpm > 0:
                    bar = min(OIL_CEILING_BAR, OIL_BASE_BAR + OIL_PER_RPM * rpm)
                    idleness = 1.0 - max(0.0, min(1.0, (rpm - 700) / 1300.0))
                    bar -= OIL_IDLE_WEAR_BAR_PER_YEAR * years_in * idleness
                    bar += OIL_PER_C_ABOVE_70 * max(0.0, temp - 70.0) + rng.gauss(0, 0.03)
                    volts = (VOLTS_CHARGING if rpm > 1200 else VOLTS_IDLE_CHARGING) + rng.gauss(0, 0.04)
                else:
                    bar = None
                    volts = rest_volts + rng.gauss(0, 0.02)

                rows.append((
                    int(clock.timestamp()),
                    round(rpm, 1),
                    round(temp + rng.gauss(0, 0.25), 2),
                    round(bar * 100.0, 1) if bar is not None else None,
                    round(volts, 3),
                    round(sea + rng.gauss(0, 0.1), 2),
                    round(max(0.0, rpm / 110.0) + rng.gauss(0, 0.3), 2) if rpm > 1200 else 0.0,
                    1.0,                        # engine_age_s — Signal K one poll behind
                    "synthetic",
                ))
    return rows


def main() -> None:
    from .profile import load as load_profile

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, required=True,
                        help="where to write. Never the real log")
    parser.add_argument("--profile", help="path to a boat.toml (default: the usual search)")
    parser.add_argument("--seed", type=int, default=295, help="RNG seed; fixed for repeatability")
    args = parser.parse_args()

    profile = load_profile(args.profile)
    tz = ZoneInfo(profile.timezone)

    if args.db.exists():
        args.db.unlink()
    for suffix in ("-wal", "-shm"):
        stale = args.db.with_name(args.db.name + suffix)
        if stale.exists():
            stale.unlink()

    season_end = datetime.now(tz).replace(hour=12, minute=0, second=0, microsecond=0)
    season_start = season_end - timedelta(days=SEASON_LENGTH_DAYS)

    rng = random.Random(args.seed)
    rows = generate(rng, season_start, season_end)

    db = connect(args.db)
    db.executemany(
        "INSERT OR IGNORE INTO samples "
        "(t, rpm, temp_c, oil_kpa, volts, sea_c, sog_kn, engine_age_s, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    db.commit()

    meta_set(db, "synthetic", "1")
    meta_set(db, "synthetic_note",
             f"Fabricated by engine_seed.py. No engine ran. "
             f"Not a measurement of {profile.vessel.name}.")
    meta_set(db, "synthetic_seed", args.seed)
    meta_set(db, "synthetic_generated", datetime.now(tz).isoformat(timespec="seconds"))

    first, last, count = db.execute("SELECT MIN(t), MAX(t), COUNT(*) FROM samples").fetchone()
    print(f"⚠ SYNTHETIC — {count:,} samples written to {args.db}")
    print(f"  {datetime.fromtimestamp(first, tz):%Y-%m-%d} → "
          f"{datetime.fromtimestamp(last, tz):%Y-%m-%d}, seed {args.seed}")
    print("  meta.synthetic = 1 — engine_hours.py and the report will say so on every page")
    db.close()


if __name__ == "__main__":
    main()
