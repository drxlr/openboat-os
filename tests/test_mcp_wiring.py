#!/usr/bin/env python3
"""Every tool a client can see must be a tool this server can actually run.

    python3 tests/test_mcp_wiring.py

The bug this exists to prevent shipped twice. `file_task` and `boat_shopping` were both
listed in `TOOLS`, both fully implemented, both annotated — and neither was in `HANDLERS`.
ChatGPT could see "file a task", offered it, called it, and got JSON-RPC -32602
`unknown tool 'file_task'` back. The symptom the owner reported was "why can't GPT add
tasks", and nothing in the code said otherwise: the tool list, the docstring and the
annotation all claimed it worked.

That is the worst shape a failure can take here. A capability that is absent is honest.
A capability that is advertised, refused at dispatch, and reported to the user as the
boat declining is not — the caller concludes the boat said no, when in fact nobody
connected the wire.

`mcp._check_wiring()` now runs at import and raises. This test is the second lock, and it
also guards the reverse: a handler nobody can reach is dead code that reads as a feature.

Do not weaken an assertion here to make a change pass.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from openboat import mcp  # noqa: E402

results: list[tuple[bool, str]] = []


def check(ok: bool, what: str) -> None:
    results.append((bool(ok), what))
    print(("  ok    " if ok else "  FAIL  ") + what)


def test_every_advertised_tool_has_a_handler() -> None:
    advertised = {t["name"] for t in mcp.TOOLS}
    unreachable = sorted(advertised - set(mcp.HANDLERS))
    check(not unreachable,
          "every tool in TOOLS is in HANDLERS — a client that can see a tool can run it "
          f"(unreachable: {unreachable or 'none'})")


def test_no_handler_without_a_tool() -> None:
    advertised = {t["name"] for t in mcp.TOOLS}
    orphans = sorted(set(mcp.HANDLERS) - advertised)
    check(not orphans,
          f"no handler exists for a tool nobody can see (orphans: {orphans or 'none'})")


def test_every_tool_is_classified() -> None:
    """`annotate()` refuses an unclassified tool; prove it still does."""
    missing = sorted({t["name"] for t in mcp.TOOLS} - set(mcp.ANNOTATIONS))
    check(not missing,
          f"every tool has an ANNOTATIONS entry (missing: {missing or 'none'})")


def test_every_handler_is_callable() -> None:
    bad = sorted(n for n, h in mcp.HANDLERS.items() if not callable(h))
    check(not bad, f"every handler is callable (not callable: {bad or 'none'})")


def test_the_wiring_guard_actually_raises() -> None:
    """The guard is the thing that keeps this true between test runs, so test the guard.

    Checked by taking a handler away and putting it back, rather than by trusting that a
    function named `_check_wiring` checks the wiring.
    """
    name = next(iter(mcp.HANDLERS))
    stashed = mcp.HANDLERS.pop(name)
    try:
        try:
            mcp._check_wiring()
        except RuntimeError as exc:
            check(name in str(exc),
                  f"the wiring guard raises and names the tool it cannot reach ({name})")
        else:
            check(False, "the wiring guard raises when a tool has no handler")
    finally:
        mcp.HANDLERS[name] = stashed
    mcp._check_wiring()  # and the tree is sound again


def test_write_tools_are_the_ones_we_expect() -> None:
    """A tool that writes must say so, and the set of them must not grow by accident.

    Not a style rule. This repo's whole claim is that an assistant can plan, explain and
    file, and cannot steer, close, pay or rewrite. That claim is only worth anything if
    somebody notices the day a seventh write appears.
    """
    writes = sorted(n for n, a in mcp.ANNOTATIONS.items()
                    if not a.get("readOnlyHint"))
    expected = sorted(["add_document", "add_link", "add_note", "fetch_document",
                       "file_task", "log_check"])
    check(writes == expected,
          f"the write tools are exactly the six intended ones (found: {writes})")


if __name__ == "__main__":
    for fn in (test_every_advertised_tool_has_a_handler,
               test_no_handler_without_a_tool,
               test_every_tool_is_classified,
               test_every_handler_is_callable,
               test_the_wiring_guard_actually_raises,
               test_write_tools_are_the_ones_we_expect):
        fn()
    bad = [w for ok, w in results if not ok]
    print("-" * 78)
    print(f"{len(results) - len(bad)}/{len(results)} checks pass")
    sys.exit(1 if bad else 0)
