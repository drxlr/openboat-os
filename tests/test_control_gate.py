#!/usr/bin/env python3
"""Every way a command is supposed to be refused.

    python3 tests/test_control_gate.py

This file exists because `openboat/control/` is the only code in the project that can move a
boat, and the interesting question about it is not whether it works. It is whether it refuses.
Each test below sends a command that should not go through and asserts that nothing reached
the transport.

The transport here is a spy: it records what it was asked to send and sends nothing. A test
that "passes" because a real PUT succeeded would be a test that steered something.

**Do not weaken an assertion in this file to make a change pass.** If a change makes one of
these fail, the change is wrong. The one that matters most is `test_ai_cannot_commit`.
"""

from __future__ import annotations

import ast
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from openboat.control import (Command, ControlError, Helm, NotArmed,  # noqa: E402
                              NotEnabled, Refused, propose)
from openboat.profile import Profile  # noqa: E402

results: list[tuple[bool, str]] = []


def check(condition: bool, what: str) -> None:
    results.append((bool(condition), what))
    print(f"{'  ok  ' if condition else '  FAIL'}  {what}")


class Spy:
    """Records what would have been sent. Sends nothing, ever."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict | None]] = []

    def __call__(self, path, payload):
        self.sent.append((path, payload))
        return {"ok": True}


def helm(tmp: Path, *, enabled=True, allow=("standby", "auto", "adjust", "target"),
         pilot_state="auto", target_deg=90.0, reachable=True) -> tuple[Helm, Spy]:
    boat = Profile()
    boat.control = {"enabled": enabled, "allow": list(allow), "arm_seconds": 60}
    spy = Spy()
    h = Helm(boat, journal=tmp / "journal.jsonl", transport=spy)
    h.state = lambda: ({"reachable": True, "state": pilot_state, "mode": "compass",
                        "target_deg": target_deg} if reachable
                       else {"reachable": False, "why": "no answer"})
    return h, spy


TMP = Path(__file__).resolve().parent / "_tmp"
TMP.mkdir(exist_ok=True)


# --------------------------------------------------------------------------------------
# 1. Off by default, and off means off.
# --------------------------------------------------------------------------------------
def test_disabled_by_default() -> None:
    fresh = Profile()
    check(fresh.control.get("enabled") is False,
          "a profile with no [control] block has control disabled")

    h, spy = helm(TMP, enabled=False)
    try:
        h.arm("a person")
        check(False, "arming a disabled helm refuses")
    except NotEnabled:
        check(True, "arming a disabled helm refuses")
    try:
        h.command(Command("standby", operator="a person"))
        check(False, "commanding a disabled helm refuses")
    except NotEnabled:
        check(True, "commanding a disabled helm refuses")
    check(spy.sent == [], "nothing was sent")


# --------------------------------------------------------------------------------------
# 2. Enabled is not armed, and arming expires.
# --------------------------------------------------------------------------------------
def test_must_be_armed() -> None:
    h, spy = helm(TMP)
    try:
        h.command(Command("standby", operator="a person"))
        check(False, "an unarmed helm refuses")
    except NotArmed:
        check(True, "an unarmed helm refuses")
    check(spy.sent == [], "nothing was sent while unarmed")


def test_arming_expires() -> None:
    h, spy = helm(TMP)
    h.arm("a person", seconds=0.3)
    check(h.armed is not None, "armed")
    time.sleep(0.4)
    check(h.armed is None, "the arming lapses on its own")
    try:
        h.command(Command("standby", operator="a person"))
        check(False, "a lapsed arming refuses")
    except NotArmed:
        check(True, "a lapsed arming refuses")


def test_disarm_is_always_safe() -> None:
    h, _ = helm(TMP)
    h.disarm("never armed")
    h.arm("a person")
    h.disarm("once")
    h.disarm("twice")
    check(h.armed is None, "disarm() is safe from any state and repeatable")


# --------------------------------------------------------------------------------------
# 3. THE ONE THAT MATTERS. A model cannot commit a command, by any name it might use.
# --------------------------------------------------------------------------------------
def test_ai_cannot_commit() -> None:
    h, spy = helm(TMP)
    h.arm("a person")
    for name in ("ai", "AI", "Claude", "assistant", "model", "openboat"):
        try:
            h.command(Command("standby", operator=name))
            check(False, f"operator {name!r} is refused")
        except Refused:
            check(True, f"operator {name!r} is refused")
    try:
        h.command(Command("standby", operator=""))
        check(False, "an empty operator is refused")
    except Refused:
        check(True, "an empty operator is refused")
    check(spy.sent == [], "no AI-operated command reached the boat")


def test_ai_cannot_arm() -> None:
    h, _ = helm(TMP)
    try:
        h.arm("claude")
        check(False, "a model cannot arm the helm")
    except Refused:
        check(True, "a model cannot arm the helm")


def test_propose_sends_nothing() -> None:
    card = propose("adjust", 5, evidence="to keep half a mile off the headland")
    check(card["committed"] is False and card["verb"] == "adjust",
          "propose() returns a card, uncommitted")
    check("proposal" in card["note"].lower() and "nothing has been sent" in card["note"].lower(),
          "the card says plainly that nothing was sent")


# --------------------------------------------------------------------------------------
# 4. The allow-list, the bounds, and the rate limit.
# --------------------------------------------------------------------------------------
def test_allow_list() -> None:
    h, spy = helm(TMP, allow=("standby",))
    h.arm("a person")
    try:
        h.command(Command("auto", operator="a person"))
        check(False, "a verb outside the allow-list is refused")
    except Refused:
        check(True, "a verb outside the allow-list is refused")
    check(spy.sent == [], "nothing was sent")

    h.command(Command("standby", operator="a person"))
    check(len(spy.sent) == 1 and spy.sent[0][1] == {"value": "standby"},
          "an allowed verb goes through")


def test_adjust_is_bounded() -> None:
    h, spy = helm(TMP)
    h.arm("a person")
    try:
        h.command(Command("adjust", 45, operator="a person"))
        check(False, "a 45° nudge is refused as a manoeuvre")
    except Refused:
        check(True, "a 45° nudge is refused as a manoeuvre")

    h2, spy2 = helm(TMP)
    h2.boat.control["max_adjust_deg"] = 90        # a profile cannot raise the ceiling
    h2.config = h2.boat.control
    h2.arm("a person")
    try:
        h2.command(Command("adjust", 45, operator="a person"))
        check(False, "a profile cannot raise the hard 10° ceiling")
    except Refused:
        check(True, "a profile cannot raise the hard 10° ceiling")


def test_target_jump_is_bounded() -> None:
    h, spy = helm(TMP, target_deg=90.0)
    h.arm("a person")
    try:
        h.command(Command("target", 200, operator="a person"))
        check(False, "a 110° swing of the target is refused")
    except Refused:
        check(True, "a 110° swing of the target is refused")


def test_rate_limit() -> None:
    h, spy = helm(TMP, allow=("adjust",))
    h.arm("a person")
    refused = 0
    for _ in range(12):
        try:
            h.command(Command("adjust", 1, operator="a person"))
        except Refused:
            refused += 1
    check(refused > 0 and len(spy.sent) <= 6,
          f"the rate limit stops a repeating command ({len(spy.sent)} sent, {refused} refused)")


# --------------------------------------------------------------------------------------
# 5. Compare-and-set: the boat must still be in the situation the operator saw.
# --------------------------------------------------------------------------------------
def test_stale_state_refuses() -> None:
    h, spy = helm(TMP, pilot_state="standby")
    h.arm("a person")
    try:
        h.command(Command("adjust", 5, expect_state="auto", operator="a person"))
        check(False, "a command against a stale pilot state is refused")
    except Refused:
        check(True, "a command against a stale pilot state is refused")
    check(spy.sent == [], "nothing was sent to a boat that had moved on")


# --------------------------------------------------------------------------------------
# 6. A failed command is not retried.
# --------------------------------------------------------------------------------------
def test_no_retry_on_failure() -> None:
    attempts = []

    def failing(path, payload):
        attempts.append(path)
        raise OSError("the pilot did not answer")

    h, _ = helm(TMP)
    h._transport = failing
    h.arm("a person")
    try:
        h.command(Command("standby", operator="a person"))
        check(False, "a failed command raises")
    except ControlError as exc:
        check("NOT been retried" in str(exc), "a failed command says it was not retried")
    check(len(attempts) == 1, "a failed command is attempted exactly once")


# --------------------------------------------------------------------------------------
# 7. Nothing outside control/ writes to the boat.
# --------------------------------------------------------------------------------------
def test_no_writes_elsewhere() -> None:
    offenders = []
    for path in (ROOT / "openboat").rglob("*.py"):
        if "control" in path.parts:
            continue
        text = path.read_text()
        for needle in ('method="PUT"', "method='PUT'", 'method="POST"', "method='POST'"):
            if needle in text:
                offenders.append(f"{path.name}: {needle}")
    check(not offenders, f"only control/ writes to the boat ({offenders or 'clean'})")

    # Structure, not prose. This used to grep the whole file for the word "control", which
    # meant the module could not even *document* that it has no route into the helm without
    # failing the build — and a test that punishes an honest comment gets edited rather than
    # obeyed. What matters is that the module cannot reach the helm: it must not import it,
    # must not construct it, and must not offer it as a tool. All three are checked, which
    # is strictly more than the old line proved.
    mcp = (ROOT / "openboat" / "mcp.py").read_text()
    tree = ast.parse(mcp)
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and "control" in (node.module or ""):
            imports.append(node.module)
        if isinstance(node, ast.ImportFrom):
            imports += [a.name for a in node.names if "control" in a.name]
        if isinstance(node, ast.Import):
            imports += [a.name for a in node.names if "control" in a.name]
    check(not imports, f"the MCP server does not import control ({imports or 'clean'})")

    calls = [n.func.id for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id in ("Helm", "Command", "propose")]
    check(not calls, f"the MCP server never constructs a helm ({calls or 'clean'})")

    names = re.findall(r'"name":\s*"([a-z_]+)"', mcp)
    steering = [n for n in names if any(word in n for word in
                ("helm", "steer", "pilot", "autopilot", "course", "rudder", "command"))]
    check(not steering, f"no MCP tool is a steering verb ({steering or 'clean'})")


if __name__ == "__main__":
    print(__doc__.splitlines()[0])
    print("-" * 78)
    for case in (test_disabled_by_default, test_must_be_armed, test_arming_expires,
                 test_disarm_is_always_safe, test_ai_cannot_commit, test_ai_cannot_arm,
                 test_propose_sends_nothing, test_allow_list, test_adjust_is_bounded,
                 test_target_jump_is_bounded, test_rate_limit, test_stale_state_refuses,
                 test_no_retry_on_failure, test_no_writes_elsewhere):
        case()
    print("-" * 78)
    failed = [what for ok, what in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} checks pass"
          + (f" — FAILED: {failed}" if failed else ""))
    sys.exit(1 if failed else 0)
