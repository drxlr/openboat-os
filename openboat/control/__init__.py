"""Control — the part of OpenBoat that can write to the boat. Off unless you turn it on.

Everything else in this package reads. This subpackage steers, and that difference is worth
more than a docstring, so it is enforced in three ways rather than described in one:

1. **It does nothing until `[control] enabled = true` is in your profile.** A fresh install,
   an install that copied the demo profile, and an install where somebody forgot — all of
   them refuse every command with an explanation.
2. **It must be armed before it will act, and the arming expires.** Enabling it in a file is
   a decision made once, months ago. Arming is a decision made now, by somebody aboard, and
   it lapses on its own so that a forgotten arm is not a permanently live helm.
3. **The AI is never in the command path.** A model may call `propose()` and put a card on a
   screen. Only `Helm.command()` moves anything, only a person reaches it, and the audit
   journal records which of the two happened.

## What it does not do

It does not implement an autopilot. It talks to the one your boat already has, through
Signal K's autopilot API, which in turn talks to the vendor's own hardware through a
provider plugin. The steering algorithm, the rudder feedback loop and the safety cutouts are
your pilot's and always have been. This is a remote control with an audit trail, and treating
it as anything more is a mistake.

**And the bus itself does not know who you are.** On SeaTalk1 there is no addressing and no
authentication at all; on NMEA 2000 there is no meaningful authentication either. A course
computer cannot distinguish this package's gated, armed, rate-limited, audited command from
any other device putting the same bytes on the wire — a published DIY remote soldered onto
two wires sends exactly the same thing. Everything below governs *OpenBoat's* path to the
pilot. It cannot govern the bus, and no amount of care here changes what else can reach it.

It also cannot make an unattended boat safe. Every command here assumes somebody is at the
helm, watching, able to reach the pilot's own standby button faster than any software. That
assumption is the whole basis on which this is reasonable, and no configuration option
relaxes it.

## The rings

    ring 0   read the boat                      always on, no configuration
    ring 1   advise: propose, explain, plan      always on
    ring 2   act: standby, auto, heading, dodge  OFF by default, opt-in, armed, audited

There is no ring 3. Nothing here will ever fire a distress call, operate a seacock, start an
engine, or transmit on your behalf.
"""

from __future__ import annotations

from .helm import (Armed, Command, ControlError, Helm, NotArmed, NotEnabled, Refused,
                   propose)

__all__ = ["Helm", "Command", "Armed", "propose",
           "ControlError", "NotEnabled", "NotArmed", "Refused"]
