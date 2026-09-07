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
- **A companion that answers from your own papers** — the survey, the manuals, the invoice
  that says what was actually replaced — quoted with the file and the page, never guessed.
  `openboat.ingest` turns a PDF into passages the companion can cite back to; see
  [docs/COMPANION.md](docs/COMPANION.md).
- **A snag list you can file from a phone.** Photograph a fault at the pontoon, say what is
  wrong, send — it lands in the boat's own file in ten seconds, appended and never rewritten.
  See [docs/SNAGS.md](docs/SNAGS.md).
- **One page for everything the boat owes you** — service due in running hours and salt-water
  outings, alongside the faults somebody photographed at the pontoon. Nothing on it writes:
  a check is a notebook line, a service is a command at a desk. See [docs/JOBS.md](docs/JOBS.md).
- **A console for the workshop end of the boat** — the manuals as a searchable shelf, every
  answer citing its file and line with the PDF one click away, the tasks, the dashboards, and
  a page that lists what is *not* recorded with the same weight as what is. Read-only, and it
  holds no boat facts of its own. See [docs/CONSOLE.md](docs/CONSOLE.md).
- **A front door, when somebody else needs to see the boat.** One login in front of any
  number of boats, invite links instead of passwords anybody types, and a fixed public
  address that survives the tunnel changing. The boat's own services stay unauthenticated
  on localhost, where they belong. See [docs/GATE.md](docs/GATE.md).
- **An account page, so a login is not a thing only a terminal can change.** Your own name
  and password, one button that signs out every other device, and — for an admin — invite,
  re-issue, revoke and which boats each person sees, without going near the machine. See
  [docs/GATE.md](docs/GATE.md).
- **A link for the person actually holding the spanner.** Hand one fault to somebody with
  no account — they see it, its photographs and its history on their phone, and answer
  *looked at it* or *fixed it*. The grant is written into the boat's own file, so who was
  given a way in is readable by a person and taken back the same way. See
  [docs/SNAGS.md](docs/SNAGS.md).
- **An inbox an assistant can put paper into, and only a person takes it out of.** A model
  can fetch the manual it found and drop it in; nothing searches or quotes it until somebody
  reads it and accepts it, and every passage then says which assistant brought it, from
  where, and who let it in. A corpus a model both reads and writes is a prompt-injection
  amplifier, and this is the seam that breaks the loop. See [docs/INTAKE.md](docs/INTAKE.md).
- **Read-only tools for an AI assistant**, over MCP. Ask Claude when the next four-hour
  window is and it answers from live data.

## Give it to an AI

```bash
claude mcp add openboat -- python3 -m openboat.mcp
```

`marine_forecast`, `passage_window`, `plan_route`, `boat_state`, `ais_targets`,
`boat_docs`, `boat_specs`, `boat_papers`, `boat_costs`, `boat_files`, `checks`,
`snag_photo`, `boat_inbox`, `boat_tasks` (the snags with their status, and the service due)
and `engine_data` (hours, coverage and the health findings). All read-only; five
append-only tools write to their own files and nothing else — `log_check`, `add_note` and
`add_document`, plus `add_link` and `fetch_document`, which put a suggestion or a PDF into
the boat's **inbox**, which is not the boat's documents and is never searched or quoted
until a person accepts it. The assistant can plan, explain, remember and suggest; it cannot
steer, switch, send, or put anything into the library on its own. That boundary is the
design — see [DISCLAIMER.md](DISCLAIMER.md) and [docs/INTAKE.md](docs/INTAKE.md).

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
nothing about land, depth or restricted areas. Read [DISCLAIMER.md](DISCLAIMER.md) before you
rely on any of it.

## It can steer, and it will not until you say so

Control is **possible, not required, and off**. A fresh install cannot send a command, and
three separate acts are needed before it can: enabling it in your profile, putting a verb in
that boat's allow-list, and arming the helm — which expires on its own.

Then it can put your autopilot into standby, engage it, nudge a heading by up to 10°, or set
a target within 30° of the current one. It is not an autopilot; it talks to the one you have,
through Signal K's autopilot API, and your pilot stays in charge of the actual steering.

**The AI is never in the command path.** A model can propose an action with its reasoning and
put a card on a screen. A person taps it. The operator's name is required and the names a
model might use for itself are refused, `openboat/control/` is the only directory that
writes, and a test fails the build if that stops being true.

None of this makes an unattended boat safe. Every command assumes somebody at the helm who
can reach the pilot's own standby faster than any software.

[OpenBoat Flush](https://github.com/drxlr/openboat-flush) is the other write path — a button
that flushes the engine's raw-water circuit and records it here.

## Layout

| | |
|---|---|
| `openboat/` | The package. Stdlib only, so it runs on a Pi, a laptop and a mini PC without a virtualenv |
| `tests/` | Plain scripts, no framework. `python3 tests/test_regressions.py` |
| `profiles/` | The demo boat, and the schema your own profile follows |
| `signalk/` | Signal K in Docker, plus an NMEA simulator so everything is testable with no boat |
| `arduino/` | Analogue engine senders → NMEA → Signal K. ⚠️ Never tested against a real engine |
| `scripts/` | The private-content check, its git hook, and the tunnel that publishes the gate |
| `deploy/` | The Vercel relay: a fixed public address in front of the gate's tunnel |
| `docs/` | [Hardware](docs/HARDWARE.md) · [Network](docs/NETWORK.md) · [Forecast](docs/FORECAST.md) · [Charts](docs/CHARTS.md) · [Companion](docs/COMPANION.md) · [Intake](docs/INTAKE.md) · [Snags](docs/SNAGS.md) · [Jobs](docs/JOBS.md) · [Console](docs/CONSOLE.md) · [Gate](docs/GATE.md) · [Private and public](docs/PRIVATE-AND-PUBLIC.md) |

## Contributing

Yes, please — especially bug reports from real boats, which is the only place the interesting
failures live. [CONTRIBUTING.md](CONTRIBUTING.md) has the details: sign-off with a DCO, no
boat facts in the code, and never weaken a test to make it pass.

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
