# OpenBoat OS

**An open skipper's layer for a boat.** It reads your boat, reads the weather, and answers
the questions a chart screen does not: *can we go out on Saturday*, *what will it be doing
at the second waypoint*, *is the boat alright*, *why is that alarm going off*.

It is not a chartplotter. Those exist and they are good. This is the layer above one —
your boat's own data, your boat's own history, and an assistant that can read both.

```bash
git clone https://github.com/drxlr/openboat-os
cd openboat-os
python3 -m openboat.server      # → http://localhost:8747
```

That is the whole install. No dependencies, no build step, no account, no API key, and no
boat: it starts on a demo profile and shows live weather immediately. Python 3.11 or newer
is the only requirement.

## What you get

- **Passage windows.** Contiguous runs of hours that pass *your* limits, longest first. The
  answer to "when can we go out", not a wall of numbers to interpret yourself.
- **Routes with the weather each leg will actually meet.** A forecast for the harbour is not
  a forecast for the passage. A leg leaving at 08:00 in a flat calm can arrive at 13:00 in a
  sea breeze, so every leg is sampled at its own midpoint and its own hour.
- **A helm dashboard** built for a tablet in a cockpit: day, dusk and night palettes that
  follow the sun, tap targets sized for wet hands, and tiles you arrange yourself.
- **An anchor watch that tells swinging from dragging** — the difference between a boat
  moving around its anchor and a boat moving *with* it. A plain radius alarm cannot.
- **Collision awareness** from AIS, with closest point of approach and time to it.
- **An engine log that writes itself** — hours from the rpm sender, and a cooling trend
  fitted per rpm band against sea temperature, because a raw-water engine swims in the thing
  it is measuring and the seasonal swing is usually larger than the fault you are hunting.
- **Five read-only tools for an AI assistant**, over MCP. Ask Claude when the next four-hour
  window is and it answers from live data.

## Give it to an AI

```bash
claude mcp add openboat -- python3 -m openboat.mcp
```

`marine_forecast`, `passage_window`, `plan_route`, `boat_state`, `ais_targets`. All read-only.
The assistant can plan, explain and remember; it cannot steer, switch or send. That boundary
is the design — see [DISCLAIMER.md](DISCLAIMER.md).

## Connect it to your boat

OpenBoat reads [Signal K](https://signalk.org), the open marine data standard, over its REST
API. If you already run Signal K, point OpenBoat at it and you are done:

```bash
SIGNALK_URL=http://boat.local:3000 python3 -m openboat.server
```

If you do not, `signalk/` has a Docker compose file and an NMEA simulator, so you can run the
entire stack — server, instruments, a boat moving in a circle — on a laptop with no hardware
at all. That is the recommended way to learn it: in a warm room in January, not in a locker
on a pontoon.

Hardware, when you want it, is deliberately undemanding. A Raspberry Pi is the usual boat
computer but it is one option among several, and the whole system runs happily with none.
See [docs/HARDWARE.md](docs/HARDWARE.md) — including the part about why you must never wire a
boat sender straight to a Pi's pins.

## Make it your boat

Everything specific to a vessel lives in one file:

```bash
cp profiles/demo-boat.toml boat.toml
$EDITOR boat.toml
```

Your dimensions, your berth, your limits, your Signal K paths, your alarm bands. `boat.toml`
is gitignored, and the code contains no boat facts at all — which is what makes it safe to
run this on your own boat and contribute to the public project at the same time. That
arrangement is written up in [docs/PRIVATE-AND-PUBLIC.md](docs/PRIVATE-AND-PUBLIC.md), and
there is a commit hook that enforces it rather than trusting you to remember.

**One rule the code takes seriously: a number is either sourced or absent.** OpenBoat will
not invent a length, a fuel burn or an engine power because a model name implied one. Ask it
for a passage's fuel without a measured burn rate and it returns zero and says why, rather
than multiplying by a plausible guess. A confidently wrong figure is worse than a blank.

## Two ideas worth knowing before you trust a number

**The berth is not the sea.** A coastal forecast grid cell containing a marina is
land-influenced. It under-reads the wind you will meet outside and over-reads the gusts, and
because a window is tested against wind *and* gusts, those two errors do not cancel: good
afternoons get thrown away on gusts that only exist ashore. That is why a profile has a
`berth` and a separate `forecast_point`, and why nothing here has a name meaning both.

**Offline is the normal case.** A boat is in its berth most of the year and a berth often has
no network. Every part of this degrades on its own: a gauge with no sender says *no sender*
rather than showing a confident zero, and a dashboard that showed an error page because the
boat was where it belongs would be a broken dashboard.

## What this is not

Not a chartplotter, not a navigation system, not a safety device. The route planner knows
nothing about land, depth or restricted areas. Nothing here steers, switches, transmits or
pays, and the read-only boundary is structural rather than a feature not yet written. Read
[DISCLAIMER.md](DISCLAIMER.md) before you rely on any of it.

## Layout

| | |
|---|---|
| `openboat/` | The package. Stdlib only, so it runs on a Pi, a laptop and a mini PC without a virtualenv |
| `tests/` | Plain scripts, no framework. `python3 tests/test_regressions.py` |
| `profiles/` | The demo boat, and the schema your own profile follows |
| `signalk/` | Signal K in Docker, plus an NMEA simulator so everything is testable with no boat |
| `arduino/` | Analogue engine senders → NMEA → Signal K. ⚠️ Never tested against a real engine |
| `scripts/` | The private-content check and its git hook |
| `docs/` | [Hardware](docs/HARDWARE.md) · [Network](docs/NETWORK.md) · [Forecast](docs/FORECAST.md) · [Private and public](docs/PRIVATE-AND-PUBLIC.md) |

## Contributing

Yes, please — especially bug reports from real boats, which is the only place the interesting
failures live. [CONTRIBUTING.md](CONTRIBUTING.md) has the details: sign-off with a DCO, no
boat facts in the code, and never weaken a test to make it pass.

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
