#!/usr/bin/env python3
"""GET /api/maintenance, GET /api/snags, and the one-POST-route invariant.

    python3 tests/test_jobs.py

`openboat.server` is read-only by construction — `do_POST` matches exactly one path,
`/api/logbook`, and 404s everything else on purpose (see its docstring). The two GET routes
here are the newest read-only surface: `/api/maintenance` joins the engine log to the
service record and answers "what is due", and `/api/snags` reads back the write-only snag
service's own file. Both have to survive the one case a real boat hits before either of
them: a fresh clone, with no engine log and no snag list yet, that must answer honestly
rather than 500.

No network. No pytest fixtures beyond what stdlib + pytest already give the other test
files — see test_ws.py, test_snag.py, test_paths.py and test_control_gate.py for the same
idiom: a `check()` helper that never raises, collected into `results` and only turned into a
process exit code in `__main__` (pytest itself only notices an uncaught exception, same as
every other file here).

**Do not weaken the last check in this file.** `test_only_one_post_route_exists` is the one
that makes it impossible to quietly grow a second write route on this dashboard.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS, FAIL = "  ok  ", "  FAIL"
results: list[tuple[bool, str]] = []


def check(condition: bool, what: str) -> None:
    results.append((bool(condition), what))
    print(f"{PASS if condition else FAIL}  {what}")


DEMO = ROOT / "profiles" / "demo-boat.toml"
VERDICTS = {"due", "soon", "ok", "unknown"}


# --------------------------------------------------------------------------------------
# A real dashboard, on an ephemeral port, for the life of a `with` block — so every
# assertion below sees exactly what a browser or a phone would: a status code and a JSON
# body that went through `do_GET`/`do_POST`, not a function called directly.
# --------------------------------------------------------------------------------------
class Dashboard:
    def __init__(self):
        from openboat.server import OpenBoat
        self.httpd = HTTPServer(("127.0.0.1", 0), OpenBoat)
        self.port = self.httpd.server_port
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.thread.join(timeout=5)
        self.httpd.server_close()

    def request(self, method: str, path: str, body: bytes | None = None):
        """(status, json-or-None). An HTTPError is a status code here, not an exception —
        a 404 is exactly what several of these calls are supposed to get back."""
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=body,
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                payload = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                payload = None
            return exc.code, payload


class Env:
    """Set and restore a handful of environment variables for one `with` block."""

    def __init__(self, **kv):
        self.kv = kv
        self.old: dict = {}

    def __enter__(self):
        for k, v in self.kv.items():
            self.old[k] = os.environ.get(k)
            os.environ[k] = v
        return self

    def __exit__(self, *exc):
        for k, old in self.old.items():
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old
        # Neither route this file exercises reads OPENBOAT_BOATS, but the snag service does
        # (see boats()), and leaving one set from another test in the same process would
        # make read_snags() look at the wrong directory silently.
        os.environ.pop("OPENBOAT_BOATS", None)


def _minimal_boat(tmp: Path) -> Path:
    """A boat.toml with no [maintenance.flush] section at all — the case this file exists
    to cover: the flush is assessed whether or not the profile mentions it."""
    path = tmp / "boat.toml"
    path.write_text(
        "[vessel]\n"
        'name = "Test Boat"\n'
        "\n"
        "[maintenance.impeller]\n"
        'description = "Impeller"\n'
        "hours = 100\n"
        "months = 12\n"
    )
    return path


# =======================================================================================
# 1. The fresh-clone path: no engine log at all. This is the one most likely to break,
#    because it is the one nobody's own boat ever exercises again after day one.
# =======================================================================================
def test_maintenance_on_a_fresh_clone_does_not_crash() -> None:
    import openboat.server as server

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        missing_db = tmp / "engine-log.db"
        # openboat/engine.py:60 resolves DEFAULT_DB = Path(os.environ.get("OPENBOAT_ENGINE_DB",
        # "engine-log.db")) at IMPORT time, and server.py imports that name once at its own
        # import time — so setting $OPENBOAT_ENGINE_DB here would have no effect on either
        # module, however early it's set within this process. Patching the module attribute
        # directly is what actually reaches the `DEFAULT_DB` global `dispatch()` reads.
        old_db, server.DEFAULT_DB = server.DEFAULT_DB, missing_db
        try:
            with Env(OPENBOAT_PROFILE=str(DEMO)), Dashboard() as dash:
                status, body = dash.request("GET", "/api/maintenance")
            check(status == 200, f"a fresh clone with no engine log still answers 200 (got {status})")
            check(body is not None and body.get("items") == [],
                  "no engine log means an honest empty list, not a guess")
            check(body is not None and body.get("running_hours") is None,
                  "no running hours are claimed when nothing has been logged")
            source = (body or {}).get("engine_hours_source") or ""
            check(isinstance(source, str) and bool(source),
                  "an honest, non-empty reason is given even in the empty case")
            check(str(missing_db) in source,
                  "the source string names the log that does not exist, rather than staying silent")
            # The route's first act is `if not DEFAULT_DB.exists(): return {...}` — the empty
            # payload is returned before engine.connect() is ever called, so a read-only GET
            # against a fresh clone must not be the thing that brings the database into being.
            check(not missing_db.exists(),
                  "GET /api/maintenance on a fresh clone does not create the engine log as a side effect")
        finally:
            server.DEFAULT_DB = old_db


# =======================================================================================
# 2. A small seeded engine log with one recorded flush and one outing since. The flush
#    item must appear even though this profile never mentions [maintenance.flush].
# =======================================================================================
def test_maintenance_with_a_seeded_log_and_a_flush() -> None:
    import openboat.server as server
    from openboat import engine, maintenance

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        boat_path = _minimal_boat(tmp)
        db_path = tmp / "engine-log.db"

        db = engine.connect(db_path)
        maintenance.ensure(db)
        t0 = datetime.now(timezone.utc) - timedelta(days=3)
        maintenance.record(db, "flush", when=t0, note="test flush")

        # One outing, a day after the flush: three samples 30s apart, well inside
        # MAX_GAP_S, all above IDLE_RPM, moving — a real trip out, not a flush on the hose.
        outing_start = t0 + timedelta(days=1)
        for i in range(3):
            sample_t = outing_start + timedelta(seconds=30 * i)
            engine.store(db, {
                "t": int(sample_t.timestamp()), "rpm": 1500.0, "temp_c": 70.0,
                "oil_kpa": 300.0, "volts": 13.0, "sea_c": 15.0, "sog_kn": 5.0,
                "engine_age_s": 0.0, "source": "signalk",
            })
        db.close()

        old_db, server.DEFAULT_DB = server.DEFAULT_DB, db_path
        try:
            with Env(OPENBOAT_PROFILE=str(boat_path)), Dashboard() as dash:
                status, body = dash.request("GET", "/api/maintenance")
        finally:
            server.DEFAULT_DB = old_db

        check(status == 200, f"a seeded log answers 200 (got {status})")
        items = (body or {}).get("items", [])
        check(bool(items), "at least one item comes back")
        verdicts = {it["verdict"] for it in items}
        check(verdicts <= VERDICTS, f"every verdict is one of {sorted(VERDICTS)} (got {verdicts})")

        by_item = {it["item"]: it for it in items}
        check("flush" in by_item,
              "the flush is assessed even though this profile never mentions [maintenance.flush]")
        check("impeller" in by_item, "an item the profile does mention is also assessed")
        if "flush" in by_item:
            flush = by_item["flush"]
            check(flush["verdict"] == "due", f"one outing since the last flush is due (got {flush['verdict']!r})")
            check(flush["outings_since"] == 1, f"exactly one outing is counted (got {flush['outings_since']!r})")
        if "impeller" in by_item:
            check(by_item["impeller"]["verdict"] == "unknown",
                  "an item with an interval but no service record on it is unknown, not a guessed date")


# =======================================================================================
# 3. Due.as_dict() round-trips — the name every other serialiser in this codebase uses
#    (Profile.as_dict, Limits.as_dict, Paper.as_dict, Hit.as_dict).
# =======================================================================================
def test_due_as_dict_round_trips() -> None:
    from openboat.maintenance import Due

    never_serviced = Due(item="oil", description="Engine oil and filter", last=None,
                         hours_since=12.5, days_since=None, outings_since=None,
                         interval_hours=100.0, interval_months=12, per_outing=False,
                         verdict="unknown", why="never recorded")
    serviced = Due(item="flush", description="Fresh-water flush", last=datetime.now(timezone.utc),
                   hours_since=3.0, days_since=2, outings_since=1, interval_hours=None,
                   interval_months=None, per_outing=True, verdict="due", why="one outing since")

    expected_keys = {"item", "description", "last", "hours_since", "days_since",
                     "outings_since", "interval_hours", "interval_months", "per_outing",
                     "verdict", "why", "symbol"}
    for due in (never_serviced, serviced):
        d = due.as_dict()
        check(set(d) == expected_keys, f"every field is present, symbol included (got {sorted(d)})")
        check(json.dumps(d) is not None, "the result is json.dumps-able")

    check(never_serviced.as_dict()["last"] is None, "no service on record serialises to None, not a guessed date")
    check(serviced.as_dict()["last"] == serviced.last.isoformat(),
          "a recorded service serialises its datetime to a string, not the object itself")


# =======================================================================================
# 4. /api/snags with no SNAGS.md at all.
# =======================================================================================
def test_snags_with_no_file_yet() -> None:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        boat_path = _minimal_boat(tmp)
        with Env(OPENBOAT_PROFILE=str(boat_path)), Dashboard() as dash:
            status, body = dash.request("GET", "/api/snags")
        check(status == 200, f"no SNAGS.md yet still answers 200 (got {status})")
        check(body is not None and body.get("snags") == [], "nothing filed means an empty list")
        check(body is not None and body.get("open_count") == 0, "and zero open, not a crash")


# =======================================================================================
# 5. /api/snags with one open entry and one fixed one, written the real way — through
#    snag.record() and a hand-edit of the Status line — rather than a guessed markdown
#    shape. See snag.py's record() and read_snags(); a hand-rolled fixture here would test
#    nothing but its own assumptions.
# =======================================================================================
def test_snags_open_and_fixed() -> None:
    from openboat import snag

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        boat_path = _minimal_boat(tmp)
        with Env(OPENBOAT_PROFILE=str(boat_path)):
            # snag.boats() falls back to the single $OPENBOAT_PROFILE boat, keyed "boat",
            # when $OPENBOAT_BOATS is not set — same profile the dashboard just loaded.
            snag.record("boat", "Locker will not latch", "saloon", [], by="the owner")
            snag.record("boat", "Second fault, stays open", "", [], by="the owner")

            target = tmp / "SNAGS.md"
            check(target.exists(), "record() wrote SNAGS.md next to the boat's profile")
            target.write_text(target.read_text().replace(
                "**Status:** open", "**Status:** fixed — new catch fitted", 1))

            with Dashboard() as dash:
                status, body = dash.request("GET", "/api/snags")

        check(status == 200, f"a seeded snag list answers 200 (got {status})")
        entries = (body or {}).get("snags", [])
        check(len(entries) == 2, f"both entries come back (got {len(entries)})")
        check(body.get("open_count") == 1, f"exactly one is still open (got {body.get('open_count')})")
        closed = [e for e in entries if not e["open"]]
        check(len(closed) == 1 and closed[0]["title"] == "Locker will not latch",
              "the fixed entry is present and marked not-open, not dropped")


# =======================================================================================
# 6. THE ARCHITECTURAL INVARIANT. This dashboard accepts exactly one POST route, ever.
#    A POST to either new GET route, or to anything else, must 404 — the same way it
#    already 404s a POST to /api/profile or /api/state. If this test ever has to be
#    loosened to make a change pass, the change is the one that is wrong, not this file:
#    it exists specifically so a second write route cannot be added by accident.
# =======================================================================================
def test_only_one_post_route_exists() -> None:
    with Env(OPENBOAT_PROFILE=str(DEMO)), Dashboard() as dash:
        for route in ("/api/maintenance", "/api/snags", "/api/not-a-real-route"):
            status, _ = dash.request("POST", route, body=b"{}")
            check(status == 404,
                  f"POST {route} is refused (got {status}) — a boat dashboard that grows a "
                  f"second write route by accident is how a read-only thing stops being one")

        # And the one route that *is* allowed still works, so the check above is proving
        # something rather than proving that POST is broken everywhere.
        status, body = dash.request(
            "POST", "/api/logbook",
            body=json.dumps({"what": "test", "found": "nothing", "verdict": "noted"}).encode())
        check(status == 200 and body is not None and "logged" in body,
              f"meanwhile the one real POST route still works (got {status})")


if __name__ == "__main__":
    print(__doc__.splitlines()[0])
    print("-" * 78)
    for case in (test_maintenance_on_a_fresh_clone_does_not_crash,
                test_maintenance_with_a_seeded_log_and_a_flush,
                test_due_as_dict_round_trips,
                test_snags_with_no_file_yet,
                test_snags_open_and_fixed,
                test_only_one_post_route_exists):
        case()
    print("-" * 78)
    failed = [what for ok, what in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} checks pass"
          + (f" — FAILED: {failed}" if failed else ""))
    sys.exit(1 if failed else 0)
