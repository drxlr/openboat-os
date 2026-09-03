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

## It can steer, if you turn that on

**This changed, and the honest version matters more than a tidy one.** Earlier versions of
this document said the project never writes to the boat. That is no longer true, and a
promise like that cannot be quietly retired.

What is true now:

- **Everything is read-only until you say otherwise.** A fresh install, and an install that
  copied the demo profile, cannot send a single command. The default lives in the code, not
  in a file an upgrade might overwrite.
- **One directory writes.** `openboat/control/` is the only code in the project that issues a
  write, and a test fails the build if a PUT or POST appears anywhere else.
- **Turning it on takes three separate acts**: `[control] enabled = true` in your profile, a
  verb added to that boat's allow-list, and somebody arming the helm — which expires on its
  own so a forgotten arm is not a permanently live helm.
- **The AI is never in the command path.** A model can call `propose()` and put a card on a
  screen with its reasoning. Only a person's tap sends anything, the operator's name is
  required, and the strings a model might use for itself are refused outright. The MCP server
  exposes no route into the helm at all.
- **It is not an autopilot.** It talks to the pilot your boat already has, through Signal K's
  autopilot API. Your pilot's own steering, rudder feedback and cutouts are unchanged and
  remain in charge. This is a remote control with an audit trail.
- **Bounded on purpose.** No single command turns more than 10°. No target swings more than
  30°. There is a rate limit. A command whose assumptions have gone stale is refused rather
  than applied to a boat that has moved on. A failed command is never retried, because a
  retry is a second command sent blind.

**What none of that buys you: an unattended boat.** Every command assumes a person at the
helm, watching, who can reach the pilot's own standby faster than any software. That
assumption is the entire basis on which this is reasonable, and no setting relaxes it.

Nothing here will ever transmit a distress call, operate a seacock, start an engine, or make
a payment.

Separately, [OpenBoat Flush](https://github.com/drxlr/openboat-flush) opens a valve to flush
an engine's raw-water circuit — a normally-closed solenoid, real interlocks, every failure
path ending shut. It writes a service record back into this project's maintenance log; this
project never calls it.

**Tell your insurer.** Software that can steer your boat is a material fact about your boat,
and the time to raise it is before it is installed. Any AI assistant
connected to OpenBoat may *propose* an action; a human commits it. That boundary is the
design, not a limitation waiting to be lifted.

## Warranty

There is none. See `LICENSE`, sections 7 and 8. This software is provided on an "AS IS"
basis, without warranties or conditions of any kind. You use it on your own vessel at your
own risk, and you are responsible for telling your insurer what you have installed.
