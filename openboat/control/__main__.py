"""A helm on the command line — mostly so the gate can be felt before it is trusted.

    python3 -m openboat.control                    what the pilot is doing, and why not
    python3 -m openboat.control --arm "your name"  make the helm live, for a while
    python3 -m openboat.control --do standby --operator "your name"
    python3 -m openboat.control --do adjust --value -5 --operator "your name"

Every command needs `--operator`, and it needs to be a person. That is not paperwork: the
name goes into the journal beside what was sent, and it is the difference between a record
of what the boat did and a record of who did it.

Arming does not survive between invocations, on purpose. A process that armed itself and
exited would leave a live helm behind with nobody watching, so a one-shot command has to
arm in the same breath. That makes the CLI slightly awkward and the helm slightly safer,
which is the right way round.
"""

from __future__ import annotations

import argparse
import sys

from ..profile import load
from .helm import Command, ControlError, Helm


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Talk to the boat's autopilot, carefully.")
    parser.add_argument("--profile")
    parser.add_argument("--arm", metavar="NAME", help="arm the helm as this person")
    parser.add_argument("--do", metavar="VERB", help="standby, auto, adjust, target, dodge")
    parser.add_argument("--value", type=float, help="degrees, for adjust and target")
    parser.add_argument("--expect", metavar="STATE",
                        help="the pilot state you believe it is in; refused if it has moved")
    parser.add_argument("--operator", default="", help="who is committing this. A person")
    parser.add_argument("--reason", default="")
    args = parser.parse_args(argv)

    boat = load(args.profile) if args.profile else load()
    helm = Helm(boat)

    print(f"boat     : {boat.vessel.name}")
    print(f"control  : {'enabled' if helm.enabled else 'OFF (this is the default)'}")
    print(f"allow    : {', '.join(helm.config.get('allow', [])) or 'nothing'}")

    state = helm.state()
    if state.get("reachable"):
        print(f"pilot    : {state.get('state')} / {state.get('mode')}"
              + (f", target {state['target_deg']:.0f}°" if state.get("target_deg") is not None
                 else ""))
    else:
        print(f"pilot    : not reachable — {state.get('why')}")

    if not args.do and not args.arm:
        blocked = helm.why_not()
        print(f"\n{blocked}" if blocked else "\nthe helm would consider a command")
        return

    try:
        if args.arm:
            armed = helm.arm(args.arm)
            print(f"\narmed by {armed.by} for {armed.seconds_left:.0f} s")
        if args.do:
            if not helm.armed:
                # One-shot: arm as the operator, so the name on the arming and the name on
                # the command are the same person, which is the only combination that means
                # anything in the journal.
                helm.arm(args.operator or "unnamed")
            result = helm.command(Command(args.do, args.value, args.expect,
                                          args.operator, args.reason))
            print(f"\nsent: {result['verb']}"
                  + (f" {result['value']:+g}" if result["value"] is not None else ""))
    except ControlError as exc:
        print(f"\nrefused: {exc}")
        sys.exit(1)
    finally:
        helm.disarm("cli exit")


if __name__ == "__main__":
    main()
