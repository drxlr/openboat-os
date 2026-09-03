"""aisstream.io — a free public AIS feed, for when there is no receiver aboard.

Many small boats have no AIS transceiver and no radar: they can neither see AIS targets
nor be seen as one. A shore-side feed of what the big ships are broadcasting is one way to
get live AIS data with no hardware at all — and it arrives over the internet, which means
it works inshore, near cell coverage, and stops working somewhere past the last tower.

    export AISSTREAM_KEY=...        # free, from https://aisstream.io — never committed

Without the key this module says so and the caller falls back to a fixture or a demo.

## ⚠️ Untested against the live service

The RFC 6455 client underneath (`ws.py`) is tested hard, against a server written from the
spec — see `tests/test_ws.py`. **The aisstream-specific part below is not**: the
subscription message and the shape of `PositionReport` / `ShipStaticData` are written from
the published API description, and nobody has run them against the real endpoint with a
real key. That is said here at the top rather than in a footnote, because it is the honest
place for it. If the first live run returns nothing, this parser is the first place to
look, not the last.

## What a shore feed is and is not

It is a picture of what *terrestrial receivers* heard. Coverage near a port is good and
falls off offshore; a target's absence is never evidence that nothing is there. And every
report is a few seconds to a few minutes old, so the CPA it produces is the CPA of a
slightly stale ship. A boat's own AIS receiver, where one is fitted, delivers the same
targets first-hand — prefer it over this feed whenever it answers.
"""

from __future__ import annotations

import json
import math
import os
import socket
import time

from .cpa import Vessel
from .ws import WebSocket, WebSocketError

STREAM_URL = "wss://stream.aisstream.io/v0/stream"

#: Default box radius around the profile's forecast point, in nautical miles. Generous on
#: purpose — a ship 15 miles out at 20 kn is 45 minutes away, which is still the same
#: afternoon.
DEFAULT_BOX_RADIUS_NM = 25.0


class NoKey(Exception):
    """AISSTREAM_KEY is not set. Expected; the caller falls back."""


def api_key() -> str:
    key = os.environ.get("AISSTREAM_KEY", "").strip()
    if not key:
        raise NoKey("AISSTREAM_KEY is not set — no shore-side AIS feed available")
    return key


def bounding_box(profile, radius_nm: float = DEFAULT_BOX_RADIUS_NM) -> list[list[float]]:
    """A [[south, west], [north, east]] box around the profile's forecast point.

    aisstream.io wants exactly this shape. There is no built-in box tied to any one
    location: it comes from the profile, which is the boat's own open water, not from a
    constant baked into this module.
    """
    lat, lon = profile.forecast_point
    dlat = radius_nm / 60.0                                        # 1' latitude = 1 nm
    dlon = radius_nm / 60.0 / max(math.cos(math.radians(lat)), 0.01)
    return [[lat - dlat, lon - dlon], [lat + dlat, lon + dlon]]


def _to_vessel(report: dict, meta: dict) -> Vessel | None:
    """One PositionReport into a Vessel, or None if it is not usable.

    AIS course and speed are the two fields most often absent or filled with the
    not-available sentinels (SOG 102.3, COG 360.0). Those are turned back into None
    rather than passed on as a course of 360° — `cpa.evaluate` handles a missing course
    honestly and cannot handle a fictitious one.
    """
    lat = report.get("Latitude", meta.get("latitude"))
    lon = report.get("Longitude", meta.get("longitude"))
    if lat is None or lon is None:
        return None

    cog = report.get("Cog")
    sog = report.get("Sog")
    if cog is not None and (cog >= 360.0 or cog < 0.0):
        cog = None
    if sog is not None and sog >= 102.2:
        sog = None

    return Vessel(
        lat=float(lat), lon=float(lon),
        cog_deg=float(cog) if cog is not None else None,
        sog_kn=float(sog) if sog is not None else None,
        name=(meta.get("ShipName") or "").strip() or f"MMSI {meta.get('MMSI', '?')}",
        mmsi=str(meta.get("MMSI", "")),
    )


def snapshot(seconds: float = 20.0, box: list[list[float]] | None = None,
             profile=None, timeout: float = 15.0) -> dict[str, Vessel]:
    """Listen for a while and return the latest report per MMSI.

    A stream has no "current state" to ask for — it only has what has come past since you
    connected. So a snapshot is a listening window, and the window has to be long enough
    for the slow reporters: a ship at anchor transmits every three minutes, one doing
    20 kn every two to ten seconds. Twenty seconds gets you the moving ships, which are
    the ones a CPA calculation is about.

    `box` is normally left to default: it is derived from `profile`'s forecast point (the
    profile itself defaults to whatever `openboat.profile.load()` finds) rather than from
    a location baked into this module.
    """
    key = api_key()
    if box is None:
        if profile is None:
            from .profile import load as load_profile
            profile = load_profile()
        box = bounding_box(profile)

    latest: dict[str, Vessel] = {}
    deadline = time.monotonic() + seconds

    with WebSocket(STREAM_URL, timeout=timeout) as sock:
        sock.send_json({
            "APIKey": key,
            "BoundingBoxes": [box],   # plural: the API refuses the singular
            "FilterMessageTypes": ["PositionReport"],
        })

        while time.monotonic() < deadline:
            try:
                raw = sock.recv(timeout=max(1.0, deadline - time.monotonic()))
            except (socket.timeout, TimeoutError):
                break
            if raw is None:
                break
            try:
                message = json.loads(raw)
            except (ValueError, TypeError):
                continue

            if message.get("MessageType") != "PositionReport":
                # The service also reports errors this way; surfacing it beats silence.
                if "error" in message or "Error" in message:
                    raise WebSocketError(f"aisstream refused the subscription: {message}")
                continue

            report = (message.get("Message") or {}).get("PositionReport") or {}
            vessel = _to_vessel(report, message.get("MetaData") or {})
            if vessel is not None and vessel.mmsi:
                latest[vessel.mmsi] = vessel

    return latest
