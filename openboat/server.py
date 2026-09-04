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

import json
import sys
import urllib.parse
from datetime import datetime
from functools import lru_cache
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from time import time

from . import boat, knowledge, ledger, logbook, papers, windows
from .marine import ForecastUnavailable, forecast
from .profile import load
from .route import Waypoint, plan

PORT = 8747
WEB = Path(__file__).parent / "web"

#: It binds 0.0.0.0 so a tablet at the helm and a phone at home can both reach it, and it
#: has NO authentication of its own. That is only safe because the network boundary is
#: somewhere else — a private overlay network, never a port forward. See docs/NETWORK.md.
BIND = "0.0.0.0"


@lru_cache(maxsize=32)
def _cached(lat: float, lon: float, days: int, bucket: int):
    """Open-Meteo updates hourly; the bucket argument expires the cache with it."""
    del bucket
    return forecast(lat, lon, days)


def cached_forecast(lat=None, lon=None, days=7):
    boat_profile = load()
    if lat is None or lon is None:
        lat, lon = boat_profile.forecast_point
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
        lat = float(params.get("lat", boat_profile.forecast_point[0]))
        lon = float(params.get("lon", boat_profile.forecast_point[1]))

        if route == "/api/profile":
            # The dashboard has no boat facts of its own: its title, its subtitle and the
            # point its chart opens on all come from here. That is what keeps one person's
            # vessel out of a public repository's HTML.
            return boat_profile.as_dict()

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
    argv = sys.argv[1:] if argv is None else argv
    port = int(argv[0]) if argv else PORT
    boat_profile = load()
    print(f"OpenBoat on http://localhost:{port}  (Ctrl-C to stop)", file=sys.stderr)
    print(f"  profile: {boat_profile.path}  —  {boat_profile.vessel.name}", file=sys.stderr)
    print(f"  Signal K: {boat_profile.signalk_url}", file=sys.stderr)
    HTTPServer((BIND, port), OpenBoat).serve_forever()


if __name__ == "__main__":
    main()
