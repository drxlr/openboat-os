#!/usr/bin/env python3
"""One MCP server, every boat on the machine — and never an answer about the wrong one.

    python3 tests/test_mcp_fleet.py

The bug this exists to prevent: a connector named after two boats, asked about the
second, searched the first boat's papers and read the first boat's expiry dates — because
`boat_specs` and the snag tools took a key while everything else answered for whichever
profile the process was started with. The reply named the boat, but only in the heading,
and the model reported "this server is running the other boat's profile" as if that were
a fact about the boat rather than a bug in the server.

The lock: on a machine with more than one boat, every tool requires `boat`, an omitted key
is refused with the list, and the key pins that boat's profile for every module the call
touches. On a machine with one boat, nothing changes.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

results: list[tuple[bool, str]] = []


def check(ok: bool, what: str) -> None:
    results.append((bool(ok), what))
    print(("  ok    " if ok else "  FAIL  ") + what)


def _boats_dir(tmp: Path) -> Path:
    for key, name, paper in (("first-boat", "First Boat", "First registration"),
                             ("second-boat", "Second Boat", "Second insurance")):
        d = tmp / key
        d.mkdir(parents=True)
        (d / "boat.toml").write_text(
            f'[vessel]\nname = "{name}"\n\n[[papers]]\nname = "{paper}"\n'
            f'expires = "2099-01-01"\n')
    return tmp


def _call(module, name, **arguments):
    answer = module.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": name, "arguments": arguments}})
    result = answer["result"]
    return bool(result.get("isError")), result["content"][0].get("text", "")


def test_two_boats_one_server() -> None:
    with tempfile.TemporaryDirectory() as raw:
        os.environ["OPENBOAT_BOATS"] = str(_boats_dir(Path(raw)))
        os.environ["OPENBOAT_PROFILE"] = str(Path(raw) / "first-boat" / "boat.toml")
        try:
            # Import after the environment is set: the schema is built from the fleet.
            for name in list(sys.modules):
                if name.startswith("openboat"):
                    del sys.modules[name]
            from openboat import mcp, mcp_http

            tools = mcp_http.handle({"jsonrpc": "2.0", "id": 1,
                                     "method": "tools/list"})["result"]["tools"]
            lacking = [t["name"] for t in tools
                       if "boat" not in t["inputSchema"].get("properties", {})]
            check(not lacking, f"every tool takes `boat` (missing on {lacking})")
            optional = [t["name"] for t in tools
                        if "boat" not in t["inputSchema"].get("required", [])]
            check(sorted(optional) == ["fetch", "search"],
                  f"with two boats, `boat` is required everywhere but search/fetch "
                  f"(optional on {optional})")
            check(tools[0]["inputSchema"]["properties"]["boat"].get("enum")
                  == ["first-boat", "second-boat"], "the schema lists the keys")

            err, text = _call(mcp, "boat_papers")
            check(err and "first-boat" in text and "second-boat" in text,
                  "a call with no boat is refused with the list, not answered")

            err, first = _call(mcp, "boat_papers", boat="first-boat")
            err2, second = _call(mcp, "boat_papers", boat="second-boat")
            check(not err and "First registration" in first
                  and "Second insurance" not in first,
                  "the first boat's papers are the first boat's")
            check(not err2 and "Second insurance" in second
                  and "First registration" not in second,
                  "the second boat's papers are the second boat's, on the same server "
                  "that was started pinned to the first")

            err, text = _call(mcp, "boat_specs", boat="second-boat")
            check(not err and text.startswith("Second Boat"),
                  "boat_specs names the boat it answers for")

            err, text = _call(mcp, "boat_papers", boat="third-boat")
            check(err and "No boat 'third-boat'" in text, "an unknown key is refused")

            # The ChatGPT contract fixes search's shape; it covers the fleet instead.
            err, text = _call(mcp_http, "search", query="anything")
            check(not err and "results" in json.loads(text),
                  "search without a boat runs across the fleet rather than refusing")

            from openboat import profile
            check(profile.PINNED.get() is None,
                  "no pin leaks out of a call")
        finally:
            os.environ.pop("OPENBOAT_BOATS", None)
            os.environ.pop("OPENBOAT_PROFILE", None)
            for name in list(sys.modules):
                if name.startswith("openboat"):
                    del sys.modules[name]

    failed = [w for ok, w in results if not ok]
    assert not failed, "\n".join(failed)


def test_one_boat_needs_no_key() -> None:
    os.environ.pop("OPENBOAT_BOATS", None)
    os.environ.pop("OPENBOAT_PROFILE", None)
    for name in list(sys.modules):
        if name.startswith("openboat"):
            del sys.modules[name]
    from openboat import mcp
    required = [t["name"] for t in mcp.TOOLS
                if "boat" in t["inputSchema"].get("required", [])]
    check(not required, f"with one boat, `boat` is nowhere required (was on {required})")
    err, text = _call(mcp, "boat_specs")
    check(not err and "Demo Boat" in text, "a one-boat server answers without a key")
    failed = [w for ok, w in results if not ok]
    assert not failed, "\n".join(failed)


if __name__ == "__main__":
    test_two_boats_one_server()
    test_one_boat_needs_no_key()
    sys.exit(0 if all(ok for ok, _ in results) else 1)
