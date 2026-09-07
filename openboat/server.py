#!/usr/bin/env python3
"""OpenBoat on a port: the same functions the MCP server uses, with a face on them.

    python3 -m openboat.server          # http://localhost:8747

Run it on the always-on Mac and it is reachable from the sofa, from the Pixel tablet and
the boat over a private network — no port forwarding, no certificate, no cloud.

Read-only about the boat. It cannot steer, and it holds no route into `openboat/control/`.
The one thing it writes is the check log: `POST /api/logbook` appends a line to a local
file recording that somebody looked at something. That is a notebook, not a control, and it
is kept deliberately separate from anything that moves.

Binds to all interfaces so the tailnet can reach it; keep it OFF the public internet —
Tailscale is the boundary, this server has no authentication of its own.
"""

from __future__ import annotations

import errno
import json
import os
import sys
import urllib.parse
from datetime import datetime
from functools import lru_cache
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import time

from . import __version__
from . import boat, engine_hours, knowledge, ledger, logbook, maintenance, papers, windows
from .engine import DEFAULT_DB
from .engine import connect as connect_engine_log
from .marine import ForecastUnavailable, forecast
from .profile import ProfileError, load, require_point
from .route import Waypoint, plan

PORT = 8747
WEB = Path(__file__).parent / "web"

#: It binds 0.0.0.0 so a tablet at the helm and a phone at home can both reach it, and it
#: has NO authentication of its own. That is only safe because the network boundary is
#: somewhere else — a private overlay network, never a port forward. See docs/NETWORK.md.
# Every interface by default, because a phone on the boat's Wi-Fi is the point. Behind a
# reverse proxy on a server, set OPENBOAT_BIND=127.0.0.1: an internet-facing box with no
# firewall would otherwise hand the raw, unauthenticated service to anyone who finds the port.
BIND = os.environ.get("OPENBOAT_BIND", "0.0.0.0")


@lru_cache(maxsize=32)
def _cached(lat: float, lon: float, days: int, bucket: int):
    """Open-Meteo updates hourly; the bucket argument expires the cache with it."""
    del bucket
    return forecast(lat, lon, days)


def cached_forecast(lat=None, lon=None, days=7):
    boat_profile = load()
    if lat is None or lon is None:
        lat, lon = require_point(boat_profile, "forecast_point")
    return _cached(lat, lon, days, int(time() // 3600))


def hour_json(hour):
    return {
        "time": hour.time.isoformat(),
        "wind_kn": hour.wind_kn,
        "gust_kn": hour.gust_kn,
        "wind_deg": hour.wind_deg,
        "wind_name": hour.wind_name,
        "beaufort": hour.beaufort,
        "temp_c": hour.temp_c,
        "rain_mm": hour.rain_mm,
        "wave_m": hour.wave_m,
        "wave_s": hour.wave_s,
    }


class OpenBoat(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB), **kwargs)

    def log_message(self, fmt, *args):
        sys.stderr.write(f"{self.address_string()} {fmt % args}\n")

    # The two generated reports live beside the code that makes them. Serving them from
    # here rather than file:// is what lets the shell hold them as apps instead of links —
    # same origin, so they sit in a frame without the browser refusing.
    #: Generated reports, served from here rather than from file:// so the dashboard can
    #: hold them in a frame as apps rather than link out to them — same origin, no refusal.
    REPORTS = {
        "/reports/season.html": Path("reports") / "season.html",
        "/reports/engine.html": Path("reports") / "engine.html",
    }

    def do_GET(self):
        if self.path.startswith("/api/"):
            return self.api()
        if self.path.split("?")[0] == "/paper":
            return self.send_paper()
        report = self.REPORTS.get(self.path.split("?")[0])
        if report:
            return self.send_report(report)
        return super().do_GET()

    def do_POST(self):
        """The only write this server accepts: one line in the check log.

        Not a general write surface. The route is matched exactly, the body is capped, and
        anything else gets 404 rather than a helpful error — a boat dashboard that grows a
        second POST route by accident is how a read-only thing stops being one.
        """
        if self.path.split("?")[0] != "/api/logbook":
            return self.send_json({"error": "not found"}, status=404)
        try:
            length = min(int(self.headers.get("Content-Length", 0)), 64_000)
            body = json.loads(self.rfile.read(length) or b"{}")
            entry = logbook.record(
                what=str(body.get("what", "")),
                found=str(body.get("found", "")),
                verdict=str(body.get("verdict", "noted")),
                by=str(body.get("by", "dashboard")),
                refs=list(body.get("refs") or []),
            )
        except (ValueError, json.JSONDecodeError) as exc:
            return self.send_json({"error": str(exc)}, status=400)
        return self.send_json({"logged": json.loads(entry.line())})

    def send_paper(self):
        """The PDF a derived document was extracted from, if it is still beside it.

        `openboat.ingest` writes markdown and says in its own header that the paper is the
        original. This is the route that makes that sentence actionable from a browser
        instead of true but unreachable.

        It will only ever serve a file the profile already named, or the `.pdf` sitting
        beside one. Any other path is a 404 — the console is on an open port, and "serve
        whatever is asked for" is how a dashboard becomes a file server.
        """
        params = {k: v[0] for k, v in
                  urllib.parse.parse_qs(self.path.partition("?")[2]).items()}
        try:
            library = knowledge.load(load())
        except ProfileError as exc:
            return self.send_json({"error": str(exc)}, status=500)
        named = knowledge.find(library, params.get("name", ""))
        if named is None:
            return self.send_json({"error": "not in this boat's library"}, status=404)
        paper = knowledge.original_for(named, knowledge.describe(named).derived_from)
        if paper is None:
            return self.send_json({"error": f"no original found beside {named.name}"},
                                  status=404)
        body = paper.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", f'inline; filename="{paper.name}"')
        self.end_headers()
        self.wfile.write(body)

    def send_report(self, report: Path):
        if not report.exists():
            how = ("python3 -m openboat.season" if "season" in report.name
                   else "python3 -m openboat.engine_report")
            body = (
                "<!doctype html><meta charset=utf-8>"
                "<meta name=viewport content='width=device-width,initial-scale=1'>"
                "<style>:root{color-scheme:light dark}"
                "body{margin:0;padding:2rem;font:400 15px/1.6 -apple-system,BlinkMacSystemFont,"
                "'Segoe UI',Roboto,sans-serif;background:#15181A;color:#C2C8C4}"
                "@media(prefers-color-scheme:light){body{background:#EEF1EF;color:#2B3330}}"
                "code{background:rgba(128,128,128,.18);padding:.15em .45em;border-radius:5px}"
                "</style>"
                f"<p>Not generated yet. Run <code>{how}</code> to build it.</p>"
                "<p style='opacity:.7'>It writes into <code>reports/</code>, which is where "
                "this page is served from.</p>").encode()
            return self.send_html(body, 404)
        return self.send_html(report.read_bytes())

    def send_html(self, body: bytes, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def api(self):
        route, _, query = self.path.partition("?")
        params = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}

        try:
            payload = self.dispatch(route, params)
        except Exception as exc:
            return self.send_json({"error": f"{type(exc).__name__}: {exc}"}, status=500)
        return self.send_json(payload)

    def dispatch(self, route: str, params: dict):
        boat_profile = load()
        # Resolved lazily: a boat with no position recorded yet still answers every question
        # about its equipment, its papers and its log, and only the routes that ask the sky
        # something need a coordinate.
        point = boat_profile.forecast_point
        lat = float(params["lat"]) if "lat" in params else (point[0] if point else None)
        lon = float(params["lon"]) if "lon" in params else (point[1] if point else None)

        if route == "/api/profile":
            # The dashboard has no boat facts of its own: its title, its subtitle and the
            # point its chart opens on all come from here. That is what keeps one person's
            # vessel out of a public repository's HTML.
            # The version rides along so the console can print what it is running against
            # rather than carrying its own copy of the number and drifting from it.
            return {**boat_profile.as_dict(), "version": __version__,
                    "profile": str(boat_profile.path) if boat_profile.path else ""}

        if route == "/api/forecast":
            days = int(params.get("days", 3))
            return {"lat": lat, "lon": lon,
                    "hours": [hour_json(h) for h in cached_forecast(lat, lon, days)]}

        if route == "/api/windows":
            limits = {key: float(params[key]) for key in
                      ("max_wind_kn", "max_gust_kn", "max_wave_m") if key in params}
            found = windows.find(lat, lon, days=int(params.get("days", 7)),
                                 min_hours=int(params.get("min_hours", 3)), limits=limits)
            return {"limits": boat_profile.limits.as_dict() | limits, "point": [lat, lon], "windows": [
                {"start": w.start.isoformat(), "end": w.end.isoformat(),
                 "hours": w.length_h, "wind_kn": w.worst_wind_kn,
                 "gust_kn": w.worst_gust_kn, "wave_m": w.worst_wave_m}
                for w in found]}

        if route == "/api/ask":
            # The companion. Retrieval over the boat's own documents, the live readings and
            # the check log, returned side by side and separately labelled. Nothing here
            # writes a sentence about the boat: the passages are quoted out of files the
            # owner wrote, with the line they came from, because a fluent paragraph that
            # invents a torque figure is exactly the failure this project refuses.
            question = params.get("q", "").strip()
            if not question:
                return {"error": "ask something: /api/ask?q=…"}
            library = knowledge.load(boat_profile)
            hits = library.search(question, limit=int(params.get("limit", 5)))
            try:
                live = {"online": True, **boat.state()}
            except boat.Offline as exc:
                live = {"online": False, "reason": str(exc)}
            return {
                "question": question,
                "documents": len(library.paths),
                "passages": [h.as_dict() for h in hits],
                "live": live,
                "checks": logbook.entries(boat=boat_profile, what=question.split()[0],
                                          limit=5) if question.split() else [],
                "vessel": boat_profile.as_dict()["vessel"],
            }

        if route == "/api/papers":
            base = papers.base_for(boat_profile)
            found = papers.load(boat_profile)
            return {"papers": [p.as_dict(base) for p in found],
                    "expiring": len(papers.expiring(boat=boat_profile))}

        if route == "/api/ledger":
            return {"summary": ledger.summary(boat=boat_profile,
                                              year=params.get("year", "")),
                    "items": ledger.items(boat=boat_profile,
                                          year=params.get("year", ""),
                                          category=params.get("category", ""))}

        if route == "/api/logbook":
            return {"entries": logbook.entries(boat=boat_profile,
                                               since=params.get("since", ""),
                                               what=params.get("what", ""),
                                               limit=int(params.get("limit", 50))),
                    "path": str(logbook.path_for(boat_profile))}

        if route == "/api/maintenance":
            # What the engine is owed. Read-only, and there is deliberately no companion
            # route that records a service: writing one is `python3 -m openboat.maintenance
            # --did …` at a keyboard, or OpenBoat Flush writing its own record when a valve
            # actually opened. Both are deliberate acts by something that knows the work
            # happened. A dashboard button is neither.
            #
            # The log is opened only if it is already there. `engine.connect()` creates the
            # file, and a GET that quietly creates a database in whatever directory the
            # server was started from is a side effect a read-only route must not have. A
            # fresh clone has no log, and the honest answer is that nothing has been counted
            # — not an empty file and a confident zero.
            if not DEFAULT_DB.exists():
                return {"items": [], "running_hours": None,
                        "engine_hours_source":
                            f"no engine log at {DEFAULT_DB} — nothing has been counted. "
                            f"Start one with python3 -m openboat.engine"}
            db = connect_engine_log(DEFAULT_DB)
            try:
                items = [d.as_dict() for d in maintenance.due(db, boat_profile)]
                meter = engine_hours.summary(db)
            finally:
                db.close()

            # There is no field here for "and by the way this log was fabricated", so the
            # provenance string carries it. Every other renderer of this data says so on
            # every page, and a maintenance verdict computed from a seeded log that did not
            # announce itself would be the exact failure this project refuses.
            if not meter["samples"]:
                running, source = None, "the engine log exists but holds no samples yet"
            elif meter["engine_hours_total"] is not None:
                running = meter["engine_hours_total"]
                # Counted *since the baseline*, which is not `running_h`: that is the whole
                # log, including the hours before the display was photographed, and adding
                # it to the baseline would count them twice.
                since = meter["engine_hours_total"] - meter["baseline_hours"]
                source = (f"{meter['baseline_hours']:.1f} h read off the helm display on "
                          f"{meter['baseline_at']}, plus {since:.1f} h counted since")
            else:
                running = meter["running_h"]
                source = (f"{meter['running_h']:.1f} h counted since logging began, over "
                          f"{meter['coverage'] * 100:.0f} % coverage. Not engine hours: "
                          f"nothing here knows what the engine did before the log existed")
            if meter["synthetic"]:
                source = "SYNTHETIC DATA — no engine ran. " + source
            return {"items": items, "running_hours": running,
                    "engine_hours_source": source}

        if route == "/api/snags":
            # `openboat.snag` is the *write* surface — its own service, on its own port,
            # which is the reason this server accepts exactly one POST. Imported here rather
            # than at the top of the file so that the module holding `record()` never joins
            # this one's namespace. It was checked: nothing in `snag` runs at import time,
            # so this is a boundary made visible rather than a safety mechanism. The
            # boundary is the point of the two being separate services at all.
            from . import snag

            # The snag service can serve several boats; this server serves exactly one. Match
            # on the resolved profile path rather than a name, because two boats may share a
            # name and only one file is the one this dashboard was started on.
            here = boat_profile.path.resolve() if boat_profile.path else None
            key = next((b["key"] for b in snag.boats()
                        if here and Path(b["profile"]).resolve() == here), None)
            # A boat the snag service does not know about, or one with no SNAGS.md yet, has
            # nothing to report — which is not an error. Nothing has been filed.
            entries = snag.read_snags(key) if key else []
            # The key and the port are handed back so a page can build a photo URL. The
            # photographs live with the write service, not here: this server never grew a
            # route into the boat's folders, and a console that wants to show a picture
            # asks the service that stored it.
            return {"snags": entries, "boat": key, "photo_port": snag.PORT,
                    "open_count": sum(1 for e in entries if e["open"])}

        if route == "/api/docs":
            # The library as a shelf rather than as an answer. The console's Documents page
            # is built from this, and so is the honest part of it: a path named in the
            # profile that is not on disk comes back marked missing instead of being left
            # out, because a shelf that hides its gaps is how somebody comes to believe a
            # manual is loaded when nothing can read it.
            library = knowledge.load(boat_profile)
            found = [d.as_dict() for d in knowledge.manifest(library)]
            return {"documents": found,
                    "count": len(found),
                    "missing": sum(1 for d in found if not d["exists"]),
                    "passages": sum(d["passages"] for d in found),
                    "gaps": sum(d["gaps"] or 0 for d in found)}

        if route == "/api/doc":
            # Reading one document whole, so a passage can be seen in the paragraph it came
            # out of. `knowledge.find` resolves only against the paths the profile already
            # named — this server binds every interface, and a route that turned a query
            # parameter straight into a file read would hand out the disk.
            library = knowledge.load(boat_profile)
            path = knowledge.find(library, params.get("name", ""))
            if path is None:
                return {"error": "no such document in this boat's library"}
            if not path.exists():
                return {"name": path.name, "path": str(path), "exists": False, "text": "",
                        "error": f"{path} is named in the profile but is not on disk"}
            return {"name": path.name, "path": str(path), "exists": True,
                    "text": path.read_text(encoding="utf-8", errors="replace"),
                    "document": knowledge.describe(path).as_dict()}

        if route == "/api/state":
            try:
                return {"online": True, **boat.state()}
            except boat.Offline as exc:
                return {"online": False, "reason": str(exc)}

        if route == "/api/paths":
            # The live Signal K tree, so a skipper can copy instance names into [paths]
            # instead of guessing engine_1 / house / main.
            try:
                return {"online": True, "paths": boat.leaves(),
                        "mapped": boat_profile.paths}
            except boat.Offline as exc:
                return {"online": False, "reason": str(exc),
                        "paths": [], "mapped": boat_profile.paths}

        if route == "/api/route":
            # waypoints=lat,lon,name;lat,lon,name
            points = []
            for i, part in enumerate(params.get("waypoints", "").split(";")):
                fields = part.split(",")
                if len(fields) < 2:
                    continue
                name = fields[2] if len(fields) > 2 and fields[2] else f"WP{i + 1}"
                points.append(Waypoint(name, float(fields[0]), float(fields[1])))
            if len(points) < 2:
                return {"error": "need at least two waypoints"}

            depart = params.get("depart")
            speed = params.get("speed_kn")
            burn = params.get("litres_per_hour")
            passage = plan(points, speed_kn=float(speed) if speed else None,
                           depart=datetime.fromisoformat(depart) if depart else None,
                           litres_per_hour=float(burn) if burn else None,
                           boat=boat_profile)
            return {
                "distance_nm": round(passage.distance_nm, 2),
                "hours": round(passage.hours, 2),
                "fuel_litres": round(passage.fuel_litres, 1),
                "legs": [{
                    "from": leg.frm.name, "to": leg.to.name,
                    "distance_nm": round(leg.distance_nm, 2),
                    "bearing_deg": round(leg.bearing_deg),
                    "bearing_name": leg.bearing_name,
                    "depart": leg.depart.isoformat(), "arrive": leg.arrive.isoformat(),
                    "weather": hour_json(leg.weather) if leg.weather else None,
                } for leg in passage.legs],
            }

        return {"error": f"no route {route}"}

    def send_json(self, payload, status: int = 200):
        body = json.dumps(payload, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def main(argv: list[str] | None = None) -> None:
    """Start the dashboard, and fail in sentences rather than in tracebacks.

    Both failures caught here belong to the same moment — somebody adding a *second* boat —
    and a traceback is the wrong answer to either. A profile that refuses to load is
    OpenBoat working: an incomplete boat is meant to stop, because the alternative is a
    forecast for an invented position that looks entirely normal. That is worth a sentence
    saying which field is missing, not forty lines of stack ending in KeyError('lat').
    """
    argv = sys.argv[1:] if argv is None else argv
    port = int(argv[0]) if argv else PORT
    try:
        boat_profile = load()
    except ProfileError as exc:
        print(f"OpenBoat cannot start: {exc}", file=sys.stderr)
        print("  The profile is incomplete, which is why it stopped rather than guessed.",
              file=sys.stderr)
        raise SystemExit(2) from None
    print(f"OpenBoat on http://localhost:{port}  (Ctrl-C to stop)", file=sys.stderr)
    print(f"  profile: {boat_profile.path}  —  {boat_profile.vessel.name}", file=sys.stderr)
    print(f"  Signal K: {boat_profile.signalk_url}", file=sys.stderr)
    try:
        # Threading, because one stuck request must not take the whole dashboard down:
        # a browser tab holding a half-sent request wedged a single-threaded server for
        # three quarters of an hour once, and every other caller saw it as offline.
        ThreadingHTTPServer((BIND, port), OpenBoat).serve_forever()
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE:
            raise
        print(f"Port {port} is already in use — another boat is probably on it.",
              file=sys.stderr)
        print(f"  Give this one a port of its own:  python3 -m openboat.server {port + 1}",
              file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
