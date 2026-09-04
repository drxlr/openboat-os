"""Poll Signal K for engine data and append it to a SQLite file. Nothing else.

    python3 -m openboat.engine                      # poll every 10 s, forever
    python3 -m openboat.engine --once                # one sample, for cron or a test
    python3 -m openboat.engine --db /tmp/e.db --interval 30

Why a file and not a plugin: the question this answers — *how many hours has the engine
run, and is it running hotter than last year* — is a question about years, and it has to
survive the server being reinstalled, the Pi being reimaged and the boat being sold. A
single SQLite file that anyone can open with any tool is the format most likely to still
be readable in ten years.

## Units — read this before trusting a number

Signal K is strict SI, and every one of these is a trap if read as a display value:

| Signal K path | Signal K unit | Stored here as |
|---|---|---|
| `propulsion.*.revolutions` | **Hz** (revolutions per *second*) | rpm — × 60 |
| `propulsion.*.temperature` | **K** | °C — − 273.15 |
| `propulsion.*.oilPressure` | **Pa** | kPa — ÷ 1000 |
| `electrical.batteries.*.voltage` | V | V |
| `navigation.speedOverGround` | m/s | kn — × 1.94384 |
| `environment.water.temperature` | **K** | °C — − 273.15 |

Sea temperature is in that list on purpose. A raw-water-cooled engine — seawater straight
through the block, manifolds and risers, no heat exchanger — reports coolant temperature
that is really the seawater temperature plus whatever the engine adds. A sea that swings
ten degrees or more between winter and summer is far larger than any fault would show.
Without the sea temperature logged alongside it, a coolant chart is a chart of the season,
not of the engine — see `engine_health.py`.

## Not double-counting

`t` is the primary key: an integer unix second. Two polls landing in the same second
collapse into one row, a restart cannot replay yesterday, and re-running `--once` in a
loop cannot inflate the hour meter. Writes are `INSERT OR IGNORE` and each is committed
immediately, so `kill -9` costs at most the sample in flight.

## When the boat is offline

Which is most of the time. `boat.Offline` is the normal case: it is caught, counted, and
nothing is written. **A gap is the honest record of a gap** — writing a zero would tell
`engine_hours.py` the engine was stopped, and it does not know that. Same rule one level
down: when Signal K answers but has no `propulsion` branch at all (no engine sender wired
up yet, or a simulator that sends GPS and depth only), the engine columns are stored NULL
and the navigation columns are still stored. NULL is not zero anywhere downstream.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

DEFAULT_DB = Path(os.environ.get("OPENBOAT_ENGINE_DB", "engine-log.db"))

KELVIN = 273.15
MS_TO_KN = 1.94384

# Beyond this the value Signal K is holding is a memory, not a measurement. A sender that
# reports once a second going quiet for a minute means the sender, the adapter or the NMEA
# connection has stopped, and an hour meter must not integrate across that.
STALE_AFTER_S = 60.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    t            INTEGER PRIMARY KEY,   -- unix seconds UTC; PK makes re-polling harmless
    rpm          REAL,                  -- NULL = not measured. Never write 0 for unknown
    temp_c       REAL,                  -- coolant, °C
    oil_kpa      REAL,                  -- oil pressure, kPa
    volts        REAL,                  -- house/start battery, V
    sea_c        REAL,                  -- seawater, °C — the baseline this engine runs on
    sog_kn       REAL,
    engine_age_s REAL,                  -- how stale Signal K's rpm value was when polled
    source       TEXT NOT NULL          -- 'signalk' | 'synthetic'
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def connect(path: Path = DEFAULT_DB) -> sqlite3.Connection:
    """Open (and if needed create) the log. WAL so a report can read while ingest writes."""
    db = sqlite3.connect(path, timeout=10.0)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    db.commit()
    return db


def meta_get(db: sqlite3.Connection, key: str, default=None):
    row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def meta_set(db: sqlite3.Connection, key: str, value) -> None:
    db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, str(value)))
    db.commit()


def _leaf(tree: dict, *path: str) -> dict:
    """Walk to a Signal K leaf and return it whole — value *and* timestamp.

    `boat._value` throws the timestamp away, which is right for a dashboard and wrong
    here: a stale value repeated for an hour would otherwise look like an hour of data.
    """
    node = tree
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return {}
        node = node[key]
    return node if isinstance(node, dict) else {"value": node}


def _age(leaf: dict, now: float) -> float | None:
    """Seconds between Signal K's timestamp on a value and now. None if it carries none."""
    stamp = leaf.get("timestamp")
    if not isinstance(stamp, str):
        return None
    try:
        return now - datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _first_branch(tree: dict, *path: str) -> dict:
    """The first child of a Signal K container branch — `propulsion.*`, `batteries.*`.

    Fallback only. `read()` honours `[paths]` first; this hunt runs when the default
    path is missing. Name the instance in the profile when the boat has more than one.
    """
    node = _leaf(tree, *path)
    for key, child in node.items():
        if isinstance(child, dict) and not key.startswith("$") and key != "meta":
            return child
    return {}


def read(vessel_tree: dict | None = None, boat=None) -> dict:
    """One sample from Signal K, in human units.

    Pass `vessel_tree` — the raw `vessels/self` document — to parse data you already have.
    Left as `None`, this fetches it fresh from `openboat.boat`, imported lazily so that
    parsing and unit conversion can be tested with no boat, no network and no Signal K
    server running. Raises `boat.Offline` when a fetch is needed and the boat does not
    answer.

    Instance names come from the profile's `[paths]`. Without an override this still
    hunts for the first engine and the first battery bank, which is right for one of
    each and wrong for two.
    """
    now = time.time()
    from . import boat as boatmod  # noqa: PLC0415 — imported lazily, see openboat.track.record()
    if vessel_tree is None:
        vessel_tree = boatmod._get("vessels/self")
    if boat is None:
        from .profile import load
        boat = load()

    revs = boatmod.path_leaf(
        vessel_tree, "engine_revolutions", boat,
        fallback=lambda: _leaf(_first_branch(vessel_tree, "propulsion"), "revolutions"))
    temp = boatmod.path_leaf(
        vessel_tree, "engine_temperature", boat,
        fallback=lambda: _leaf(_first_branch(vessel_tree, "propulsion"), "temperature"))
    oil = boatmod.path_leaf(
        vessel_tree, "engine_oil_pressure", boat,
        fallback=lambda: _leaf(_first_branch(vessel_tree, "propulsion"), "oilPressure"))
    volts = boatmod.path_leaf(
        vessel_tree, "battery_voltage", boat,
        fallback=lambda: _leaf(_first_branch(vessel_tree, "electrical", "batteries"), "voltage"))
    sea = boatmod.path_leaf(
        vessel_tree, "water_temperature", boat,
        fallback=lambda: _leaf(vessel_tree, "environment", "water", "temperature"))
    sog = boatmod.path_leaf(
        vessel_tree, "speed_over_ground", boat,
        fallback=lambda: _leaf(vessel_tree, "navigation", "speedOverGround"))

    hz = revs.get("value")
    kelvin = temp.get("value")
    pascals = oil.get("value")
    volt = volts.get("value")
    sea_k = sea.get("value")
    ms = sog.get("value")

    return {
        "t": int(now),
        "rpm": round(hz * 60.0, 1) if isinstance(hz, (int, float)) else None,
        "temp_c": round(kelvin - KELVIN, 2) if isinstance(kelvin, (int, float)) else None,
        "oil_kpa": round(pascals / 1000.0, 2) if isinstance(pascals, (int, float)) else None,
        "volts": round(volt, 3) if isinstance(volt, (int, float)) else None,
        "sea_c": round(sea_k - KELVIN, 2) if isinstance(sea_k, (int, float)) else None,
        "sog_kn": round(ms * MS_TO_KN, 2) if isinstance(ms, (int, float)) else None,
        "engine_age_s": _age(revs, now),
        "source": "signalk",
    }


def store(db: sqlite3.Connection, sample: dict) -> bool:
    """Append one sample. False when this second is already logged — not an error."""
    cursor = db.execute(
        "INSERT OR IGNORE INTO samples "
        "(t, rpm, temp_c, oil_kpa, volts, sea_c, sog_kn, engine_age_s, source) "
        "VALUES (:t, :rpm, :temp_c, :oil_kpa, :volts, :sea_c, :sog_kn, :engine_age_s, :source)",
        sample,
    )
    db.commit()
    return cursor.rowcount == 1


def describe(sample: dict) -> str:
    def unit(key, suffix, digits=1):
        value = sample.get(key)
        return f"{value:.{digits}f}{suffix}" if value is not None else f"--{suffix}"

    stale = ""
    if sample.get("engine_age_s") is not None and sample["engine_age_s"] > STALE_AFTER_S:
        stale = f"  ⚠ engine data {sample['engine_age_s']:.0f}s old"
    return (f"{unit('rpm', ' rpm', 0)}  {unit('temp_c', '°C')}  {unit('oil_kpa', ' kPa')}  "
            f"{unit('volts', ' V', 2)}  sea {unit('sea_c', '°C')}  {unit('sog_kn', ' kn')}{stale}")


def poll(db: sqlite3.Connection, interval: float, once: bool, quiet: bool) -> None:
    """Loop until interrupted. Offline is normal; it is reported once, not every 10 seconds."""
    from . import boat  # noqa: PLC0415

    offline_since = None
    while True:
        try:
            sample = read()
            if offline_since is not None:
                gap = time.time() - offline_since
                print(f"boat back after {gap / 60:.0f} min offline — that gap stays unknown")
                offline_since = None
            fresh = store(db, sample)
            if not quiet:
                print(f"{time.strftime('%H:%M:%S')}  {describe(sample)}"
                      + ("" if fresh else "  (duplicate second, ignored)"))
        except boat.Offline as exc:
            if offline_since is None:
                offline_since = time.time()
                print(f"boat offline — logging nothing until it answers ({exc})")
        if once:
            return
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--interval", type=float, default=10.0, help="seconds between polls")
    parser.add_argument("--once", action="store_true", help="one sample and exit")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    from . import boat  # noqa: PLC0415

    db = connect(args.db)
    print(f"logging to {args.db}  ←  {boat.SIGNALK_URL or '(profile default)'}")
    try:
        poll(db, args.interval, args.once, args.quiet)
    except KeyboardInterrupt:
        print()
    finally:
        rows = db.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        print(f"{rows} samples in the log")
        db.close()


if __name__ == "__main__":
    main()
