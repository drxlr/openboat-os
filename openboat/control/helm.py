"""The only place in OpenBoat that writes to the boat.

One class, one method that moves anything, and every refusal path in front of it. If you are
auditing this project for what it can do to a vessel, this file is the whole answer — nothing
else in the package issues a write, and `scripts/check-private.py`'s sibling check in CI
fails the build if a PUT appears anywhere outside this directory.

## The gate, in order

A command passes through all of these or it does not happen:

    enabled?      `[control] enabled = true` in the profile. Off by default.
    armed?        somebody armed it, recently, and the arming has not expired
    permitted?    the verb is in the profile's allow-list
    bounded?      a heading change is within the per-command limit
    not too fast?  rate limit, so a stuck button cannot walk the boat round
    still true?   the pilot's live state matches what the caller thought it was
    human?        the call carries an operator, and 'ai' is not a valid operator

Then, and only then, one HTTP PUT to Signal K's autopilot API.

## Compare-and-set, and why

Every command carries the state the caller believed the pilot was in. If the pilot has moved
since — somebody turned the wheel, the pilot dropped to standby, another device sent
something — the command is refused rather than applied to a boat that is no longer in the
situation the operator was looking at. A heading nudge is a *relative* instruction, and a
relative instruction applied to the wrong baseline is how a small correction becomes a large
one.

## What is deliberately not here

No engage-and-walk-away. No route following without a person confirming each leg. No
automatic recovery after a link drop: if the connection to the pilot fails mid-command, this
code does not retry, because a retry is a second command sent blind. It reports and stops.

⚠️ The endpoint shapes below follow Signal K's v2 autopilot API. They have been exercised
against a mock, not against a physical pilot, and no autopilot was available to the authors.
Treat the first real command as a bench test with the drive belt off.
"""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..profile import Profile, load

#: Verbs this module knows. A profile's allow-list is a subset of these; anything not in the
#: allow-list is refused even when armed.
VERBS = ("standby", "auto", "adjust", "target", "dodge")

#: Absolute ceiling on a single relative heading change, whatever the profile says. Ten
#: degrees is a correction; ninety is a manoeuvre, and a manoeuvre is made by a person on the
#: wheel who can see what is in the way.
MAX_ADJUST_DEG = 10.0

#: Absolute ceiling on how far a target heading may be moved from the current one in one
#: command, for the same reason.
MAX_TARGET_JUMP_DEG = 30.0

#: An arming lapses. A helm armed this morning is not an argument for a live helm tonight.
DEFAULT_ARM_SECONDS = 900

#: No more than this many commands in this window. A stuck button, a wedged UI or a loop
#: cannot walk the boat round the compass one nudge at a time.
RATE_LIMIT = (6, 30.0)


class ControlError(Exception):
    """Something refused. The message says what, in words meant for a person at a helm."""


class NotEnabled(ControlError):
    """Control is off in the profile. This is the default and it is not a fault."""


class NotArmed(ControlError):
    """Enabled, but nobody armed it, or the arming has expired."""


class Refused(ControlError):
    """Armed, but this particular command did not pass a check."""


@dataclass
class Command:
    verb: str
    value: float | None = None          # degrees, for adjust and target
    expect_state: str | None = None     # what the caller believed the pilot was doing
    operator: str = ""                  # who is committing this. Never a model
    reason: str = ""                    # free text, goes into the journal

    def __post_init__(self) -> None:
        self.verb = self.verb.strip().lower()


@dataclass
class Armed:
    by: str
    until: float
    source: str = "manual"              # 'manual' | 'switch'

    @property
    def live(self) -> bool:
        return time.monotonic() < self.until

    @property
    def seconds_left(self) -> float:
        return max(0.0, self.until - time.monotonic())


def propose(verb: str, value: float | None = None, evidence: str = "") -> dict:
    """What an AI is allowed to produce: a suggestion, as data.

    This function writes nothing, reaches no network and touches no pilot. It returns a
    dictionary for a screen to render as a card that a person can tap. That tap calls
    `Helm.command()` with their own name on it.

    The separation is the point. A model that could call `command()` directly would be a
    model that can steer, and no amount of prompt wording makes that acceptable. A model that
    can only produce one of these is a model that can advise, which is the useful part
    anyway.
    """
    return {
        "kind": "control-proposal",
        "verb": verb.strip().lower(),
        "value": value,
        "evidence": evidence,
        "proposed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "committed": False,
        "note": "A proposal. Nothing has been sent to the boat. A person must confirm it.",
    }


class Helm:
    """A remote control for the autopilot your boat already has."""

    def __init__(self, boat: Profile | None = None, journal: Path | None = None,
                 transport=None) -> None:
        self.boat = boat or load()
        self.config = dict(getattr(self.boat, "control", {}) or {})
        self.journal = journal or Path(self.config.get("journal", "control-journal.jsonl"))
        self._armed: Armed | None = None
        self._recent: list[float] = []
        #: Injected in tests and by the simulator. Real transport is `_put` below.
        self._transport = transport

    # -- state ---------------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", False))

    @property
    def armed(self) -> Armed | None:
        return self._armed if (self._armed and self._armed.live) else None

    def why_not(self) -> str | None:
        """One sentence on why a command would be refused right now, or None if it would
        be considered. Written for a status line, not a log."""
        if not self.enabled:
            return ("control is off — set [control] enabled = true in your profile if you "
                    "want this boat to be steerable from here")
        if not self.armed:
            return "control is enabled but not armed — arm it at the helm first"
        return None

    def arm(self, by: str, seconds: float | None = None, source: str = "manual") -> Armed:
        """Make the helm live, for a while.

        `by` is a person. Arming is the moment a human takes responsibility, so it is
        recorded with a name, and the name appears on every command that follows.
        """
        if not self.enabled:
            raise NotEnabled(self.why_not())
        if not by or by.strip().lower() in ("ai", "claude", "assistant", "model"):
            raise Refused("arming needs a person's name. A model cannot arm the helm")
        window = float(seconds or self.config.get("arm_seconds", DEFAULT_ARM_SECONDS))
        self._armed = Armed(by=by.strip(), until=time.monotonic() + window, source=source)
        self._note("armed", by=by.strip(), seconds=window, source=source)
        return self._armed

    def disarm(self, reason: str = "manual") -> None:
        """Always safe, from any state. Does not touch the pilot — disarming stops OpenBoat
        from sending, it does not disengage your autopilot. Reach for the pilot's own standby
        for that, or send a standby command before disarming."""
        if self._armed:
            self._note("disarmed", reason=reason)
        self._armed = None

    # -- the one method that writes -------------------------------------------------------

    def command(self, cmd: Command) -> dict:
        """Send one command, or refuse and say why. This is the only write in OpenBoat."""
        self._check_gate(cmd)
        pilot = self.state()
        self._check_against_live(cmd, pilot)

        path, payload = self._render(cmd, pilot)
        self._recent.append(time.monotonic())

        try:
            result = self._put(path, payload)
        except Exception as exc:                                     # noqa: BLE001
            # No retry, ever. A retry is a second command sent without knowing whether the
            # first arrived, which on a helm is worse than a failure that says so.
            self._note("failed", verb=cmd.verb, value=cmd.value, error=str(exc),
                       operator=cmd.operator)
            raise ControlError(
                f"the command was not confirmed by the pilot: {exc}. It has NOT been retried "
                f"— check the pilot itself before sending another") from exc

        self._note("sent", verb=cmd.verb, value=cmd.value, operator=cmd.operator,
                   reason=cmd.reason, was=pilot.get("state"), path=path)
        return {"sent": True, "verb": cmd.verb, "value": cmd.value, "result": result}

    # -- checks ---------------------------------------------------------------------------

    def _check_gate(self, cmd: Command) -> None:
        if not self.enabled:
            raise NotEnabled(self.why_not())
        if not self.armed:
            raise NotArmed(self.why_not())

        operator = (cmd.operator or "").strip()
        if not operator:
            raise Refused("a command needs an operator: who is committing this")
        if operator.lower() in ("ai", "claude", "assistant", "model", "openboat"):
            raise Refused(
                "a model cannot commit a command. Use control.propose() to put a card on "
                "the screen and let a person tap it")

        allowed = self.config.get("allow", [])
        if cmd.verb not in VERBS:
            raise Refused(f"unknown command {cmd.verb!r}; known: {', '.join(VERBS)}")
        if cmd.verb not in allowed:
            raise Refused(
                f"{cmd.verb!r} is not in this boat's allow-list ({', '.join(allowed) or 'empty'})")

        if cmd.verb == "adjust":
            if cmd.value is None:
                raise Refused("adjust needs a number of degrees")
            limit = min(float(self.config.get("max_adjust_deg", MAX_ADJUST_DEG)),
                        MAX_ADJUST_DEG)
            if abs(cmd.value) > limit:
                raise Refused(
                    f"{cmd.value:+.0f}° is more than the {limit:.0f}° this helm will send in "
                    f"one command. A larger change is a manoeuvre — make it on the wheel")

        if cmd.verb == "target" and cmd.value is None:
            raise Refused("target needs a heading")

        count, window = RATE_LIMIT
        now = time.monotonic()
        self._recent = [t for t in self._recent if now - t < window]
        if len(self._recent) >= count:
            raise Refused(
                f"too many commands: {count} in {window:.0f} s is the limit. Something is "
                f"repeating, and a helm is not the place to let it")

    def _check_against_live(self, cmd: Command, pilot: dict) -> None:
        """Compare-and-set. The boat must still be in the situation the operator saw."""
        if cmd.expect_state is not None and pilot.get("state") != cmd.expect_state:
            raise Refused(
                f"the pilot is in {pilot.get('state')!r}, not {cmd.expect_state!r} as the "
                f"screen showed. Nothing was sent — look at the pilot and try again")

        if cmd.verb == "target" and cmd.value is not None:
            current = pilot.get("target_deg")
            if current is not None:
                delta = abs((cmd.value - current + 180) % 360 - 180)
                if delta > MAX_TARGET_JUMP_DEG:
                    raise Refused(
                        f"that would swing the target {delta:.0f}° in one command, more than "
                        f"the {MAX_TARGET_JUMP_DEG:.0f}° limit")

    # -- the boat ------------------------------------------------------------------------

    def _base(self) -> str:
        return self.boat.signalk_url.rstrip("/")

    def _pilot_id(self) -> str:
        return str(self.config.get("autopilot_id", "_default"))

    def state(self) -> dict:
        """What the pilot is doing. Read-only, and safe to call when control is off."""
        url = (f"{self._base()}/signalk/v2/api/vessels/self/autopilots/{self._pilot_id()}")
        try:
            with urllib.request.urlopen(url, timeout=4) as response:
                raw = json.load(response)
        except (urllib.error.URLError, TimeoutError, OSError,
                json.JSONDecodeError, UnicodeDecodeError) as exc:
            return {"reachable": False, "why": str(exc)}

        target = raw.get("target")
        return {
            "reachable": True,
            "state": raw.get("state"),
            "mode": raw.get("mode"),
            "engaged": raw.get("engaged"),
            # Signal K is SI throughout, so a heading arrives in radians.
            "target_deg": (math.degrees(target) % 360) if isinstance(target, (int, float))
                          else None,
            "raw": raw,
        }

    def _render(self, cmd: Command, pilot: dict) -> tuple[str, dict | None]:
        root = f"/signalk/v2/api/vessels/self/autopilots/{self._pilot_id()}"
        if cmd.verb == "standby":
            return f"{root}/state", {"value": "standby"}
        if cmd.verb == "auto":
            return f"{root}/state", {"value": "auto"}
        if cmd.verb == "adjust":
            return f"{root}/target/adjust", {"value": math.radians(float(cmd.value))}
        if cmd.verb == "target":
            return f"{root}/target", {"value": math.radians(float(cmd.value) % 360)}
        if cmd.verb == "dodge":
            return f"{root}/dodge", ({"value": math.radians(float(cmd.value))}
                                     if cmd.value is not None else None)
        raise Refused(f"unknown command {cmd.verb!r}")

    def _put(self, path: str, payload: dict | None):
        if self._transport is not None:
            return self._transport(path, payload)

        body = json.dumps(payload or {}).encode()
        request = urllib.request.Request(f"{self._base()}{path}", data=body, method="PUT")
        request.add_header("Content-Type", "application/json")
        token = self.config.get("token")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(request, timeout=4) as response:
            text = response.read().decode("utf-8", "replace")
            try:
                return json.loads(text) if text.strip() else {"status": response.status}
            except json.JSONDecodeError:
                return {"status": response.status, "body": text[:200]}

    # -- the record ----------------------------------------------------------------------

    def _note(self, event: str, **fields) -> None:
        """Append to the journal. A command that cannot be recorded is still sent — the
        record must never be able to block a helm — but the failure is printed."""
        row = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "event": event, **fields}
        try:
            with open(self.journal, "a") as handle:
                handle.write(json.dumps(row) + "\n")
        except OSError as exc:
            print(f"control journal unwritable ({exc}): {row}", flush=True)
