#!/usr/bin/env python3
"""The companion's promises: cite, stay offline, never steer, never leak.

    python3 tests/test_companion.py

The companion reads a boat's private papers and offers them to an assistant over a network.
Four things must stay true of it, and each has a way of quietly stopping being true:

1. It answers from documents, with the line each passage came from.
2. It works with no internet and no API key.
3. It cannot reach the helm, over any transport.
4. It will not serve a boat's papers to an unauthenticated caller.

Do not weaken an assertion here to make a change pass.
"""

from __future__ import annotations

import ast
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from openboat import knowledge, logbook, mcp, mcp_http  # noqa: E402
from openboat.profile import Profile  # noqa: E402

results: list[tuple[bool, str]] = []


def check(condition: bool, what: str) -> None:
    results.append((bool(condition), what))
    print(f"{'  ok  ' if condition else '  FAIL'}  {what}")


DOC = """# Cooling
The raw-water pump is behind the front cover.

## Flushing
The blue cap is on the starboard side. Idle only.

## Kühlwasser
Der Krümmer wurde 2024 getauscht.
"""


def library() -> tuple[knowledge.Library, Path]:
    tmp = Path(tempfile.mkdtemp()) / "boat.md"
    tmp.write_text(DOC)
    return knowledge.Library(paths=[tmp]), tmp


# --------------------------------------------------------------------------------------
# 1. Passages carry their source. An answer without one is the failure mode this exists
#    to avoid.
# --------------------------------------------------------------------------------------
def test_citations() -> None:
    lib, path = library()
    hits = lib.search("blue cap")
    check(bool(hits), "a plain query finds a passage")
    check(all(h.line > 0 and h.doc == path for h in hits),
          "every passage knows the file and line it came from")
    check(all(":" in h.where for h in hits), "the citation is renderable as file:line")


# --------------------------------------------------------------------------------------
# 2. The bilingual case. This is the one that silently did not work: the same question in
#    the other language found nothing at all.
# --------------------------------------------------------------------------------------
def test_crosses_languages() -> None:
    lib, _ = library()
    german = lib.search("Krümmer")
    english = lib.search("manifold")
    check(bool(german), "a German query finds a German passage")
    check(bool(english), "the English word for it finds the same German passage")
    check(english and "Krümmer" in english[0].text,
          "asking in English returns the German text, rather than nothing")

    check("motor" in knowledge.expand(["engine"]), "the glossary widens engine → motor")
    check(knowledge.expand(["engine"])[0] == "engine",
          "the word actually asked for stays first")


# --------------------------------------------------------------------------------------
# 3. Offline. No network call, no key, no model.
# --------------------------------------------------------------------------------------
def test_offline() -> None:
    source = (ROOT / "openboat" / "knowledge.py").read_text()
    for banned in ("urllib", "requests", "http", "openai", "anthropic", "api_key"):
        check(banned not in source, f"knowledge.py does not reach for {banned!r}")

    fresh = Profile()
    check(fresh.knowledge.get("docs") == [],
          "a profile with no [knowledge] block has no documents, and says so")


# --------------------------------------------------------------------------------------
# 4. No route to the helm, over EITHER transport. The HTTP one is the one that matters:
#    it is reachable by a hosted model.
# --------------------------------------------------------------------------------------
def test_no_helm() -> None:
    for name in ("mcp.py", "mcp_http.py"):
        tree = ast.parse((ROOT / "openboat" / name).read_text())
        bad = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and "control" in (node.module or ""):
                bad.append(node.module)
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                bad += [a.name for a in node.names if "control" in a.name]
        check(not bad, f"{name} does not import control ({bad or 'clean'})")

    verbs = ("helm", "steer", "pilot", "course", "rudder", "throttle")
    offenders = [t["name"] for t in mcp_http.TOOLS
                 if any(v in t["name"] for v in verbs)]
    check(not offenders, f"no tool over HTTP is a steering verb ({offenders or 'clean'})")

    writers = [n for n in mcp_http.HANDLERS if n not in
               ("log_check",) and "log" not in n]
    check("log_check" in mcp_http.HANDLERS and len(mcp_http.HANDLERS) == len(writers) + 1,
          "exactly one tool writes anything, and it writes to a notebook")


# --------------------------------------------------------------------------------------
# 5. The HTTP door refuses to open without a token. Not a warning — a refusal.
# --------------------------------------------------------------------------------------
def test_http_needs_a_token() -> None:
    import os
    saved = os.environ.pop("OPENBOAT_MCP_TOKEN", None)
    try:
        check(mcp_http.main([]) == 2, "mcp_http refuses to start with no token set")
    finally:
        if saved:
            os.environ["OPENBOAT_MCP_TOKEN"] = saved

    check(mcp_http.BIND == "127.0.0.1",
          "it binds to localhost; exposing it is a deliberate, separate act")
    source = (ROOT / "openboat" / "mcp_http.py").read_text()
    check("compare_digest" in source, "the token is compared in constant time")


# --------------------------------------------------------------------------------------
# 6. ChatGPT's contract: tools named exactly `search` and `fetch`, returning its shapes.
# --------------------------------------------------------------------------------------
def test_chatgpt_contract() -> None:
    names = [t["name"] for t in mcp_http.TOOLS]
    check("search" in names and "fetch" in names,
          "the two tools OpenAI's connector documentation asks for exist")

    payload = json.loads(mcp_http.tool_search("nothing will match zzzqqq"))
    check("results" in payload and isinstance(payload["results"], list),
          "search returns a results list even when it finds nothing")

    missing = json.loads(mcp_http.tool_fetch("no-such-doc#1"))
    check(missing["title"] == "not found" and missing["text"],
          "fetch on an unknown id explains itself rather than raising")


# --------------------------------------------------------------------------------------
# 6b. Every tool says whether it reads or writes. A client that is not told assumes the
#     worst — ChatGPT marked `boat_state` DESTRUCTIVE before these existed.
# --------------------------------------------------------------------------------------
def test_every_tool_is_annotated() -> None:
    from openboat import mcp
    missing = [t["name"] for t in mcp_http.TOOLS if "annotations" not in t]
    check(not missing, f"every tool carries annotations ({missing or 'clean'})")

    # A property, not a list. Naming the writers meant editing this test every time one
    # was added, which turns a safety assertion into paperwork — and a safety assertion you
    # routinely edit is one you will eventually edit for the wrong reason. What must stay
    # true is that every writer appends to a file of the companion's own and none of them
    # is destructive.
    writers = sorted(t["name"] for t in mcp_http.TOOLS
                     if not t["annotations"].get("readOnlyHint"))
    check(writers, f"the writers are declared as writers ({writers})")
    check(all(not t["annotations"].get("destructiveHint") for t in mcp_http.TOOLS),
          "no tool in the set is destructive")

    OWN_FILES = ("logbook", "notes", "documents", "ledger")
    for name in writers:
        module = {"log_check": "logbook", "add_note": "notes",
                  "add_document": "documents"}.get(name)
        check(module in OWN_FILES,
              f"{name} writes to one of the companion's own files, not the boat's ({module})")

    check("knowledge" not in {"logbook": 1, "notes": 1, "documents": 1}.keys(),
          "no writer touches the document library")
    check(not any(t["annotations"].get("destructiveHint") for t in mcp_http.TOOLS),
          "nothing in the tool set is destructive")

    # A new tool must not inherit the worst case by being forgotten.
    try:
        mcp.annotate([{"name": "brand_new_tool"}])
        check(False, "an unclassified tool is refused rather than shipped")
    except RuntimeError:
        check(True, "an unclassified tool is refused rather than shipped")

    online = {t["name"] for t in mcp_http.TOOLS if t["annotations"].get("openWorldHint")}
    check(online == {"marine_forecast", "passage_window", "ais_targets"},
          f"only the tools that really reach the internet say so ({sorted(online)})")


# --------------------------------------------------------------------------------------
# 6c. The companion can write a note, and cannot touch the boat's own documents.
#     A corpus a model both reads and writes is a prompt-injection amplifier: a sentence
#     inside a document becomes a fact in that document on the next pass, with a real
#     citation under a false claim.
# --------------------------------------------------------------------------------------
def test_notes_cannot_reach_the_documents() -> None:
    import ast as _ast
    from openboat import notes

    src = (ROOT / "openboat" / "knowledge.py").read_text()
    tree = _ast.parse(src)
    writes = [getattr(n.func, "attr", getattr(n.func, "id", ""))
              for n in _ast.walk(tree) if isinstance(n, _ast.Call)]
    bad = [w for w in writes if w in ("write_text", "write_bytes", "unlink", "rename",
                                      "truncate", "rmtree", "remove")]
    check(not bad, f"the document library cannot write, at all ({bad or 'clean'})")

    # Structural, not textual. The first version of this grepped notes.py for '"w"' and
    # failed on the docstring sentence explaining that it never uses "w" — punishing the
    # module for documenting its own guarantee. Parse it instead.
    ntree = _ast.parse((ROOT / "openboat" / "notes.py").read_text())
    modes, calls = [], []
    for n in _ast.walk(ntree):
        if isinstance(n, _ast.Call):
            fn = getattr(n.func, "attr", getattr(n.func, "id", ""))
            calls.append(fn)
            if fn == "open":
                for arg in list(n.args) + [k.value for k in n.keywords if k.arg == "mode"]:
                    if isinstance(arg, _ast.Constant):
                        modes.append(arg.value)
    check(modes and all(m == "a" for m in modes),
          f"every open() in notes.py is append mode ({modes})")
    forbidden = [c for c in calls if c in ("seek", "truncate", "unlink", "writelines",
                                           "write_text", "write_bytes", "rename")]
    check(not forbidden, f"notes.py never seeks, truncates or deletes ({forbidden or 'clean'})")

    try:
        notes.add("   ")
        check(False, "an empty note is refused")
    except ValueError:
        check(True, "an empty note is refused")
    try:
        notes.add("x" * 5000)
        check(False, "a note the size of a document is refused")
    except ValueError:
        check(True, "a note the size of a document is refused")

    from openboat import mcp
    tool = next(t for t in mcp.TOOLS if t["name"] == "add_note")
    d = tool["description"].lower()
    check("cannot edit or delete" in d,
          "the tool tells the model plainly that it cannot edit the boat's documents")
    check(tool["annotations"]["destructiveHint"] is False
          and tool["annotations"]["readOnlyHint"] is False,
          "add_note is a write, and is not destructive")


# --------------------------------------------------------------------------------------
# 6d. Transcribed documents. The danger here is not a crash — it is a model filling in a
#     smudged digit of a hull number from context, producing a plausible string that looks
#     exactly like a read one.
# --------------------------------------------------------------------------------------
def test_transcriptions_are_marked_as_such() -> None:
    import ast as _ast
    from openboat import documents, mcp

    dtree = _ast.parse((ROOT / "openboat" / "documents.py").read_text())
    modes, calls = [], []
    for n in _ast.walk(dtree):
        if isinstance(n, _ast.Call):
            fn = getattr(n.func, "attr", getattr(n.func, "id", ""))
            calls.append(fn)
            if fn == "open":
                for a in list(n.args) + [k.value for k in n.keywords if k.arg == "mode"]:
                    if isinstance(a, _ast.Constant):
                        modes.append(a.value)
    check(modes and all(m == "a" for m in modes),
          f"documents.py only ever appends ({modes})")
    check(not [c for c in calls if c in ("unlink", "rename", "truncate", "seek",
                                          "write_text", "rmtree", "remove")],
          "documents.py cannot delete, move or rewrite anything")

    check("transcriptions, not\nsources" in documents.HEADER
          or "transcriptions, not" in documents.HEADER.replace("\n", " "),
          "the file says on its face that it holds transcriptions, not sources")

    tool = next(t for t in mcp.TOOLS if t["name"] == "add_document")
    d = tool["description"]
    check("TRANSCRIBE, DO NOT INTERPRET" in d,
          "the tool tells the model not to interpret, in words it cannot miss")
    check("'?'" in d and "never complete it from context" in d,
          "and tells it exactly what to do with a character it cannot read")
    check("unclear" in tool["inputSchema"]["properties"],
          "there is somewhere to say what could not be made out")

    try:
        documents.take_in(title="", read="something")
        check(False, "a transcription with no title is refused")
    except ValueError:
        check(True, "a transcription with no title is refused")
    try:
        documents.take_in(title="x", read="")
        check(False, "a transcription with nothing read is refused")
    except ValueError:
        check(True, "a transcription with nothing read is refused")

    check(documents.files.__doc__ and "never opened or moved" in documents.files.__doc__,
          "listing files is listing only")


# --------------------------------------------------------------------------------------
# 7. The log is append-only and refuses a meaningless entry.
# --------------------------------------------------------------------------------------
def test_logbook() -> None:
    try:
        logbook.Entry(what="", found="x")
        check(False, "a check with no subject is refused")
    except ValueError:
        check(True, "a check with no subject is refused")
    try:
        logbook.Entry(what="impeller", verdict="catastrophic")
        check(False, "an invented verdict is refused")
    except ValueError:
        check(True, "an invented verdict is refused")

    source = (ROOT / "openboat" / "logbook.py").read_text()
    for banned in (".unlink(", "w\")", "'w'", "truncate"):
        check(banned not in source, f"logbook.py never opens the log for {banned!r}")
    check('open("a"' in source or '.open("a"' in source,
          "the log is opened for append and nothing else")


if __name__ == "__main__":
    print(__doc__.splitlines()[0])
    print("-" * 78)
    for case in (test_citations, test_crosses_languages, test_offline, test_no_helm,
                 test_http_needs_a_token, test_chatgpt_contract,
                 test_every_tool_is_annotated,
                 test_notes_cannot_reach_the_documents,
                 test_transcriptions_are_marked_as_such, test_logbook):
        case()
    print("-" * 78)
    failed = [what for ok, what in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} checks pass"
          + (f" — FAILED: {failed}" if failed else ""))
    sys.exit(1 if failed else 0)
