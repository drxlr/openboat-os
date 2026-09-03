# What this is not

Read this before you rely on anything here.

**OpenBoat OS is not a navigation system, not a chartplotter, and not a safety device.** It
is advisory software. It does arithmetic on weather forecasts and on data your own boat
publishes, and it presents the result. That is all it does.

Specifically, and without exception:

- **It does not know where the land is.** The route planner measures distance, bearing, time
  and the weather each leg will meet. It has no chart, no depth data, no knowledge of rocks,
  shoals, traffic separation schemes, military areas or any other restricted water. A route
  it produces may run straight across a headland. Every route goes onto a real chart, read
  by a person, before anyone follows it.
- **It does not replace a lookout.** The collision-awareness module computes closest point of
  approach from AIS data. AIS does not see vessels without transponders, and a transponder
  can be off, wrong or lying. Rule 5 is not delegable to software.
- **It does not replace a VHF radio.** A phone is not a DSC distress call.
- **Its alarms are not certified alarms.** The anchor watch runs on consumer hardware, a
  consumer GPS and a battery. Treat it as a second opinion that wakes you up, never as the
  reason you slept.
- **Numbers you did not measure are guesses.** OpenBoat refuses to compute where a
  measurement is missing rather than substituting a plausible one, but it cannot check a
  figure you typed into your own profile.
- **Forecasts are forecasts.** A model is not the sea.

## The skipper decides

Nothing in this software relieves the person in charge of a vessel of the duty to navigate
prudently, keep a proper lookout, and make their own judgement about the weather, the boat
and the crew. If the software says the afternoon looks fine and your own eyes say otherwise,
your eyes are in charge.

## It does not steer

The core of this project is read-only toward the boat by construction. It issues no
commands: no steering, no switching, no transmitting, no payments. The Signal K client is
built from GET requests only and the MCP server exposes no tool that writes.

Control — autopilot commands and the like — is deliberately **not** part of this repository.
If it ever exists it will live in a separate project, be separately installed, never be a
dependency of this one, and require a physical enable step aboard. Any AI assistant
connected to OpenBoat may *propose* an action; a human commits it. That boundary is the
design, not a limitation waiting to be lifted.

## Warranty

There is none. See `LICENSE`, sections 7 and 8. This software is provided on an "AS IS"
basis, without warranties or conditions of any kind. You use it on your own vessel at your
own risk, and you are responsible for telling your insurer what you have installed.
