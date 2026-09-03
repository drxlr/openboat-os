# Signal K — the boat's own server

[Signal K](https://signalk.org) is the open marine data standard: one server aboard collects
NMEA 0183/2000, GPS, engine and tank data, normalises it into one tree, and serves it over
REST and WebSocket. Everything else — a plotter app, a dashboard, an AI assistant — reads
from that one place instead of speaking to each instrument directly.

## Run it here first

No boat required. `simulate.py` stands in for the NMEA feed, so the whole stack — server,
config, dashboard — can be built and debugged before any hardware exists, and the first
time it meets a real boat nothing is new except the wiring.

```bash
cp signalk-config/defaults.example.json signalk-config/defaults.json
cp signalk-config/settings.example.json signalk-config/settings.json

python3 simulate.py &         # a boat that does not exist, on :10110
docker compose up -d
./setup-security.sh           # creates the admin, opens read-only access
```

Then `http://localhost:3000` shows *Demo Boat* doing 6 kn in a circle in Plymouth Sound, UK —
a real, public stretch of water and nobody's actual boat.

## The simulator has three modes

```bash
python3 simulate.py                          # 6 kn circle — a boat under way
python3 simulate.py --mode anchored          # swinging on 35 m of rode through a 120° arc
python3 simulate.py --mode dragging          # the same, anchor walking 40 m/h downwind
python3 simulate.py --lat 50.80 --lon -1.10  # simulate anywhere — the Solent, here
```

The anchored modes exist because a boat doing 6 kn has no swing circle, and an anchor watch
built on this data works by watching the **centre** of that circle migrate over time.
Without these modes its heuristic could only ever be tested against synthetic series, never
through the real Signal K path.

Verified geometrically over a 45-minute simulated series:

| mode | range from anchor | SOG | fitted swing centre | fit residual |
|---|---|---|---|---|
| `anchored` | 31.4 – 39.3 m | 0.02 – 0.50 kn | **0.7 m** from the anchor, r = 34.6 m | 1.49 m |
| `dragging` | 32.4 – 68.5 m | 0.01 – 0.50 kn | **32.1 m** from the anchor | 6.86 m |

That is exactly the signal a drag watch looks for: a boat swinging keeps its centre, a boat
dragging does not, and the residual blows up because a migrating centre does not fit one
circle at all.

Course and speed are derived from the **noiseless** track, and GPS scatter (1.5 m sigma,
`--noise`) is added to the reported position only. A real receiver takes speed from Doppler;
differentiating a scattered position would put three knots of nonsense on a boat lying still.

`--engine` adds the sentences [`../arduino/`](../arduino/) will send, so a helm panel's
engine gauges can be built and debugged before a single wire is run. Those need
[`../arduino/xdr_bridge.py`](../arduino/xdr_bridge.py) running alongside — Signal K has no
XDR parser, which that bridge's own README explains.

`--mode anchored` and `--mode dragging` also send `$HCHDT`, a compass eight degrees off the
track. A boat almost never points exactly where it is going, and the gap between heading and
course made good is the one thing a helm panel's compass rose exists to show.

## `setup-security.sh` exists because of a wall

Signal K v2 answers **401 to every REST call** until an admin user exists — correct, and also
the first thing that stops a script talking to it. The script creates the account, writes the
password to `.env.local` (gitignored), and sets `allow_readonly: true` so other read-only
clients — a dashboard, an AI assistant — can read the boat without carrying a token around.
Read-only means read-only: unauthenticated clients cannot change a setting or touch a device.

## Files

```
docker-compose.yml                    the server; same image wherever it runs
setup-security.sh                     one-time admin account + read-only access
simulate.py                           NMEA 0183 on TCP :10110 — GPS, depth, water temp, wind
signalk-config/defaults.example.json  the vessel identity — copy to defaults.json and edit
signalk-config/settings.example.json  the `sim-nmea0183` connection — copy to settings.json
signalk-config/plugin-config-data/    optional pre-seeded plugin config, same copy-and-edit pattern
```

`defaults.json` and `settings.json` are gitignored once copied into place — the same pattern
[`profiles/demo-boat.toml`](../profiles/demo-boat.toml) uses for `boat.toml` elsewhere in this
repository: the example ships in git, your own boat's facts never do.

## Swapping the simulator for the real boat

The simulator is a TCP connection in `settings.json`. On the boat it becomes a serial one:

```json
{ "type": "NMEA0183",
  "subOptions": { "type": "serial", "device": "/dev/ttyUSB0", "baudrate": 4800 } }
```

`/dev/ttyUSB0` is a USB-to-NMEA adapter — the Arduino of [`../arduino/`](../arduino/), or a
proper gateway. Use a `by-id` path rather than `ttyUSB0` once there is more than one device:
USB numbering is not stable across reboots, and a plotter that finds the engine on the GPS
port is worse than no plotter at all.

## Plugins worth having, once there is real data

Install from the server's own app store — they are npm packages, no build step.

| Plugin | Why |
|---|---|
| `@signalk/freeboard-sk` | Full chartplotter in the browser: routes, waypoints, **anchor watch with an alarm** |
| `signalk-anchoralarm-plugin` | The alarm Freeboard drives. The single most useful thing on a boat that swings at anchor |
| `@signalk/charts-plugin` | Offline chart tiles, so the plotter works with no signal |
| `signalk-to-influxdb` / Grafana | History. *Was the oil pressure always like that, or only since June* |
| `signalk-audio-notifications` | Alarms out of a speaker rather than into a browser tab nobody has open |
