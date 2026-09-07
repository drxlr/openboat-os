#!/usr/bin/env python3
"""Open the tunnel to the gate and tell the relay where it went.

    python3 scripts/gate_tunnel.py

`cloudflared --url` gives you a working public hostname in about two seconds and no account
at all, which is exactly the right shape for this — and a *different* hostname every time it
restarts, which is exactly the wrong one. A person's phone needs one bookmark that keeps
working, not a URL that changed while the boat's machine was asleep.

So this is the piece in the middle. It starts the tunnel, reads the hostname out of
cloudflared's own output, writes it to one Edge Config item on Vercel, and then does nothing
but wait. `deploy/vercel/api/relay.py` reads that item and forwards to whatever it says. The
fixed address is the Vercel one; the tunnel behind it is free to be as ephemeral as it likes.

Run it under launchd with `KeepAlive`, because the useful behaviour when the tunnel dies is
to start a new one and republish the new hostname — which is what happens if this exits
non-zero, and it always does when cloudflared stops.

    OPENBOAT_EDGE_CONFIG_ID   the Edge Config to write (ecfg_…)
    VERCEL_TEAM_ID            when the project belongs to a team
    VERCEL_TOKEN              an API token; falls back to the Vercel CLI's own login
    OPENBOAT_GATE_PORT        the gate's port (default 8749)

The token is never printed, not in a log line and not in an error. Log lines go to stderr so
launchd can capture them.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

GATE_PORT = int(os.environ.get("OPENBOAT_GATE_PORT", "8749"))
URL_FILE = Path.home() / ".config" / "openboat" / "gate-url"

#: cloudflared prints the hostname inside a box drawn in box-drawing characters, on its own
#: line, amid a good deal of other output. Matched rather than parsed: the surrounding
#: decoration has changed between versions and the URL has not.
TUNNEL_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

#: How long to wait for that line before giving up. cloudflared normally produces it in a
#: second or two; a minute means something is wrong and launchd should try again.
STARTUP_LINES = 400


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def vercel_token() -> str:
    """`$VERCEL_TOKEN`, or the token the Vercel CLI already holds from `vercel login`.

    Reading the CLI's own auth file means this runs on a machine somebody has already
    logged in on without a second credential to create, store and forget to rotate.
    """
    token = os.environ.get("VERCEL_TOKEN", "").strip()
    if token:
        return token
    auth = (Path.home() / "Library" / "Application Support" / "com.vercel.cli" / "auth.json")
    if not auth.is_file():
        auth = Path.home() / ".config" / "com.vercel.cli" / "auth.json"
    try:
        return str(json.loads(auth.read_text(encoding="utf-8")).get("token", "")).strip()
    except (OSError, ValueError, json.JSONDecodeError):
        return ""


def publish(url: str) -> bool:
    """Upsert the `origin` item so the relay starts forwarding here. True when it stuck."""
    config_id = os.environ.get("OPENBOAT_EDGE_CONFIG_ID", "").strip()
    token = vercel_token()
    if not config_id:
        log("OPENBOAT_EDGE_CONFIG_ID is unset — the tunnel is up but nothing was told "
            "about it. Set OPENBOAT_ORIGIN on the Vercel project, or set this.")
        return False
    if not token:
        log("no Vercel token — set VERCEL_TOKEN, or run `vercel login` as this user.")
        return False

    target = f"https://api.vercel.com/v1/edge-config/{config_id}/items"
    team = os.environ.get("VERCEL_TEAM_ID", "").strip()
    if team:
        target += f"?teamId={team}"
    payload = json.dumps({"items": [{"operation": "upsert", "key": "origin",
                                     "value": url}]}).encode()
    request = urllib.request.Request(target, data=payload, method="PATCH", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as resp:
            resp.read(4096)
    except urllib.error.HTTPError as exc:
        # The body, not the request: the request carries the token in a header and this
        # message ends up in a log file somebody pastes into an issue.
        log(f"Vercel refused the update ({exc.code}): "
            f"{exc.read(500).decode('utf-8', 'replace')}")
        return False
    except OSError as exc:
        log(f"could not reach the Vercel API: {type(exc).__name__}")
        return False
    log(f"published — the relay now forwards to {url}")
    return True


def remember(url: str) -> None:
    try:
        URL_FILE.parent.mkdir(parents=True, exist_ok=True)
        URL_FILE.write_text(url + "\n", encoding="utf-8")
    except OSError as exc:
        log(f"could not write {URL_FILE.name}: {type(exc).__name__}")


def main() -> int:
    command = ["cloudflared", "tunnel", "--url", f"http://127.0.0.1:{GATE_PORT}",
               "--no-autoupdate", "--protocol", "http2"]
    log(f"starting: {' '.join(command)}")
    try:
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1)
    except FileNotFoundError:
        log("cloudflared is not installed — `brew install cloudflared`")
        return 2

    url = ""
    for _ in range(STARTUP_LINES):
        line = proc.stdout.readline()
        if not line:
            break
        found = TUNNEL_URL.search(line)
        if found:
            url = found.group(0)
            break

    if not url:
        log("cloudflared produced no tunnel URL; giving up so launchd starts a fresh one.")
        proc.terminate()
        return 1

    log(f"tunnel up: {url}")
    remember(url)
    publish(url)

    # Everything after this is cloudflared's own output, carried through to the log. The
    # loop is also what keeps this process alive: launchd watches *this*, not the tunnel.
    try:
        for line in proc.stdout:
            sys.stderr.write(line)
    except KeyboardInterrupt:
        proc.terminate()
        return 0
    code = proc.wait()
    log(f"cloudflared exited ({code}) — exiting non-zero so it is restarted.")
    return code or 1


if __name__ == "__main__":
    raise SystemExit(main())
