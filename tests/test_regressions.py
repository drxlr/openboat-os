#!/usr/bin/env python3
"""Silent wrong answers that shipped once, each held down by a test.

Run it the way every test here runs:

    python3 tests/test_regressions.py

No pytest, no network, no Docker. Every case below failed before its fix and passes after,
and every one of them produced a *plausible wrong answer* rather than a crash. That is the
class of bug this project says it is not allowed to have, because a crash gets fixed and a
confident wrong number gets believed.

Do not weaken an assertion here to make a change pass. Each one is the only thing standing
between a fixed bug and its return.
"""

from __future__ import annotations

import socket
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "arduino"))

PASS, FAIL = "  ok  ", "  FAIL"
results: list[tuple[bool, str]] = []


def check(condition: bool, what: str) -> None:
    results.append((bool(condition), what))
    print(f"{PASS if condition else FAIL}  {what}")


# --------------------------------------------------------------------------------------
# 1. The forecast must not offer hours that are already over.
#
# Open-Meteo's hourly series starts at 00:00 today, so by the afternoon a third of it is
# history. "Can we go out?" pointed at windows that had ended before lunch.
# --------------------------------------------------------------------------------------
def test_forecast_drops_past_hours() -> None:
    from openboat import marine, windows

    offset = 3 * 3600                                  # a point at UTC+3
    now_local = datetime.now(timezone.utc) + timedelta(seconds=offset)
    start = now_local.replace(tzinfo=None, minute=0, second=0, microsecond=0) - timedelta(hours=30)
    stamps = [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(72)]
    n = len(stamps)

    def fake_get(url, params, timeout=30.0):
        if "marine" in url:
            # The wave endpoint has no data for many points; wind-only must still work.
            raise marine.ForecastUnavailable("no marine data at this point")
        return {"utc_offset_seconds": offset,
                "hourly": {"time": stamps, "wind_speed_10m": [5.0] * n,
                           "wind_gusts_10m": [8.0] * n, "wind_direction_10m": [90.0] * n,
                           "temperature_2m": [27.0] * n, "precipitation": [0.0] * n}}

    real_get, marine._get = marine._get, fake_get
    try:
        raw = marine.forecast(days=3, include_past=True)
        future = marine.forecast(days=3)
        check(len(raw) - len(future) == 30, "30 past hours dropped from a 72-hour series")
        check(future[0].time.hour == now_local.hour, "the hour in progress is kept")
        check(all(w.end >= future[0].time for w in windows.find(days=3)),
              "no window that has already ended is offered")
        check(len(marine.forecast(days=3, include_past=True)) == 72,
              "include_past=True still returns the raw series")
    finally:
        marine._get = real_get


# --------------------------------------------------------------------------------------
# 2. A 200 that is not JSON is the boat being unreachable, not a crash.
#
# Marina captive portals and Signal K's own restart page both answer 200 with HTML. The
# JSONDecodeError escaped and killed the anchor watch in the middle of a night watch.
# --------------------------------------------------------------------------------------
def test_non_json_two_hundred_is_offline() -> None:
    import io
    import urllib.request

    from openboat import boat

    class Page(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    real_open, urllib.request.urlopen = urllib.request.urlopen, \
        lambda url, timeout=5: Page(b"<html>Marina wifi: please log in</html>")
    try:
        try:
            boat._get("vessels/self")
            check(False, "a non-JSON 200 raises Offline")
        except boat.Offline:
            check(True, "a non-JSON 200 raises Offline")
        except Exception as exc:                        # noqa: BLE001 - that is the point
            check(False, f"a non-JSON 200 raises Offline (got {type(exc).__name__})")
    finally:
        urllib.request.urlopen = real_open


# --------------------------------------------------------------------------------------
# 3. A dead sender must go quiet.
#
# The bridge re-sent whatever it last held once a second with a fresh timestamp, so a dead
# Arduino looked like a healthy engine at constant rpm and the hour meter kept counting.
# Phantom engine hours are worse than no engine hours.
# --------------------------------------------------------------------------------------
def test_stale_sender_goes_quiet() -> None:
    import xdr_bridge

    xdr_bridge.MAX_AGE_S = 2.0
    body = "IIXDR,C,82.6,C,ENGTEMP"
    checksum = 0
    for character in body:
        checksum ^= ord(character)
    sentence = f"${body}*{checksum:02X}"

    def source():
        yield sentence                                  # one reading, then the sender dies
        while True:
            time.sleep(0.2)

    port = 11907
    threading.Thread(target=xdr_bridge.serve, args=(source(), "127.0.0.1", port),
                     daemon=True).start()
    time.sleep(0.6)

    client = socket.create_connection(("127.0.0.1", port), timeout=5)
    client.settimeout(0.5)
    began, last_at = time.monotonic(), None
    while time.monotonic() - began < 6.0:
        try:
            if client.recv(8192):
                last_at = time.monotonic() - began
        except socket.timeout:
            pass
    client.close()

    check(last_at is not None, "the bridge sends a fresh reading")
    check(last_at is not None and last_at < 3.0,
          f"the bridge falls silent once the sender is stale (last at {last_at:.1f}s)")


# --------------------------------------------------------------------------------------
# 4. The aisstream subscription key is plural. The singular is refused by the service.
# --------------------------------------------------------------------------------------
def test_aisstream_subscription_key() -> None:
    source = (ROOT / "openboat" / "ais.py").read_text()
    check('"BoundingBoxes"' in source, "the subscription sends BoundingBoxes")
    check('"BoundingBox":' not in source, "the refused singular is gone")


# --------------------------------------------------------------------------------------
# 5. The dashboard must ship NO default alarm band on an engine reading.
#
# What counts as hot depends on the engine: raw-water cooling runs a thermostat near 60 °C,
# freshwater cooling near 85 °C. A default pair is wrong for half of all boats, and being
# wrong in this direction leaves the panel green through a real overheat. Bands come from
# the profile or they do not exist.
# --------------------------------------------------------------------------------------
def test_no_default_engine_bands() -> None:
    page = (ROOT / "openboat" / "web" / "index.html").read_text()
    temp = page[page.index("'propulsion.engine_1.temperature'"):][:400]
    oil = page[page.index("'propulsion.engine_1.oilPressure'"):][:400]
    check("bands:" not in temp.split("},")[0],
          "engine temperature ships with no default band")
    check("bands:" not in oil.split("},")[0],
          "oil pressure ships with no default band")
    check("obApplyBands" in page, "profile bands are applied to the signal table")
    from openboat.profile import Profile
    check("bands" in Profile().as_dict(),
          "/api/profile actually carries [bands] — the dashboard cannot apply what it is not sent")


# --------------------------------------------------------------------------------------
# 6. The package must not carry a boat fact.
#
# The whole public/private split rests on this. A measurement that creeps back into a
# module is a measurement that describes somebody else's boat in everybody's calculation.
# --------------------------------------------------------------------------------------
def test_no_boat_facts_in_the_package() -> None:
    from openboat.profile import Profile, ProfileError

    empty = Profile()
    for fact in ("length_m", "beam_m", "draft_m", "cruise_burn_lph"):
        check(getattr(empty.vessel, fact) is None,
              f"{fact} has no default value in the code")
    try:
        empty.require("length_m")
        check(False, "require() refuses rather than guessing a missing measurement")
    except ProfileError:
        check(True, "require() refuses rather than guessing a missing measurement")


if __name__ == "__main__":
    print(__doc__.splitlines()[0])
    print("-" * 78)
    for case in (test_forecast_drops_past_hours, test_non_json_two_hundred_is_offline,
                 test_stale_sender_goes_quiet, test_aisstream_subscription_key,
                 test_no_default_engine_bands, test_no_boat_facts_in_the_package):
        case()
    print("-" * 78)
    failed = [what for ok, what in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} checks pass"
          + (f" — FAILED: {failed}" if failed else ""))
    sys.exit(1 if failed else 0)
