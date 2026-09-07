#!/usr/bin/env python3
"""The library manifest, the two routes the console reads it through, and the paper route.

    python3 tests/test_console.py

`openboat/web/console.html` is a reader with no facts of its own: every document, number and
name on it comes from `/api/docs`, `/api/doc`, `/api/profile` and `/paper`. These are the
checks that make that safe to say.

Three of them matter more than the rest.

**A named document that is not on disk must come back marked missing.** Dropping it would
make the shelf look complete and quietly stop answering from a manual somebody believes is
loaded — the confident silence this project exists to refuse.

**`/api/doc` and `/paper` must only ever resolve a path the profile already named.** The
dashboard binds every interface. A route that turned a query parameter into a file read
would hand out the disk, and so would one that trusted a filename read out of a document's
own header.

**The console page must contain no fact about any boat.** It lives in a public repository.

No network. Same `check()` idiom as the rest of the suite — see tests/test_jobs.py.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from openboat import knowledge                                          # noqa: E402
from openboat.ingest import BANNER                                      # noqa: E402

PASS, FAIL = "  ok  ", "  FAIL"
results: list[tuple[bool, str]] = []


def check(condition: bool, what: str) -> None:
    results.append((bool(condition), what))
    print(f"{PASS if condition else FAIL}  {what}")


CONSOLE = ROOT / "openboat" / "web" / "console.html"
#: The views live beside the shell as plain scripts; the fact check reads all of them.
CONSOLE_DIR = ROOT / "openboat" / "web" / "console"


def console_source() -> str:
    parts = [CONSOLE.read_text(encoding="utf-8")]
    parts += [f.read_text(encoding="utf-8") for f in sorted(CONSOLE_DIR.glob("*.js"))]
    return "\n".join(parts)

DERIVED = f"""# Raw water pump, model 7-J

> Text extracted from `pump-7j.pdf` on 2026-01-01 by `openboat.ingest` using pdftotext.
>
> **{BANNER}** If a passage here reads wrongly, the paper is right.

> 4 pages, 3 with a text layer.

## Raw water pump, model 7-J — page 1

Impeller replacement interval: 200 hours.

## Raw water pump, model 7-J — page 2

Torque the cover screws to 4 Nm.
"""

BY_HAND = """# What the yard actually did

## Winter 2025

The gearbox oil was changed and the anodes were renewed.
"""


class Dashboard:
    """A real server on an ephemeral port, so every assertion sees what a browser sees."""

    def __init__(self):
        from openboat.server import OpenBoat
        self.httpd = HTTPServer(("127.0.0.1", 0), OpenBoat)
        self.port = self.httpd.server_port
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.thread.join(timeout=5)
        self.httpd.server_close()

    def get(self, path: str):
        """(status, body). `body` is parsed JSON when it is JSON, raw bytes otherwise."""
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method="GET")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                raw, kind = resp.read(), resp.headers.get("Content-Type", "")
                return resp.status, (json.loads(raw) if "json" in kind else raw)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, json.loads(raw) if raw else None
            except json.JSONDecodeError:
                return exc.code, raw


class Env:
    def __init__(self, **kv):
        self.kv, self.old = kv, {}

    def __enter__(self):
        for k, v in self.kv.items():
            self.old[k] = os.environ.get(k)
            os.environ[k] = v
        return self

    def __exit__(self, *exc):
        for k, old in self.old.items():
            os.environ.pop(k, None) if old is None else os.environ.__setitem__(k, old)


def a_boat(tmp: Path, docs: list[str]) -> Path:
    """A profile pointing at `docs`, written relative so path resolution is exercised too."""
    listed = ", ".join(f'"{d}"' for d in docs)
    profile = tmp / "boat.toml"
    profile.write_text(
        '[vessel]\nname = "Test Boat"\nkind = "motor cruiser"\n\n'
        f"[knowledge]\ndocs = [{listed}]\n", encoding="utf-8")
    return profile


# --------------------------------------------------------------------------------------
def test_describe_tells_written_from_extracted():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        (tmp / "notes.md").write_text(BY_HAND, encoding="utf-8")
        (tmp / "pump-7j.md").write_text(DERIVED, encoding="utf-8")

        hand = knowledge.describe(tmp / "notes.md")
        check(hand.exists and hand.derived_from == "",
              "a file a person wrote is not reported as extracted")
        check(hand.title == "What the yard actually did",
              f"the title comes off the first heading (got {hand.title!r})")
        check(hand.passages > 0, f"it is split into passages ({hand.passages})")
        check(hand.pages is None and hand.gaps is None,
              "a hand-written file has no page count and does not invent one")

        derived = knowledge.describe(tmp / "pump-7j.md")
        check(derived.derived_from == "pump-7j.pdf",
              f"the source PDF is read out of the header (got {derived.derived_from!r})")
        check(derived.pages == 4 and derived.pages_with_text == 3,
              f"so are the page counts (got {derived.pages}/{derived.pages_with_text})")
        check(derived.gaps == 1,
              f"and the gap is the difference, not a guess (got {derived.gaps})")


def test_a_missing_document_is_reported_not_dropped():
    """The check that matters most on this page. A profile naming a manual that is not
    there must say so — a shelf that hides its gaps is how somebody comes to believe a
    document is loaded when nothing can read it."""
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        (tmp / "here.md").write_text(BY_HAND, encoding="utf-8")
        profile = a_boat(tmp, ["here.md", "gone.md"])

        with Env(OPENBOAT_PROFILE=str(profile)):
            from openboat.profile import load
            found = knowledge.manifest(knowledge.load(load()))

        check(len(found) == 2, f"both named documents are in the manifest ({len(found)})")
        gone = [d for d in found if d.path.name == "gone.md"]
        check(len(gone) == 1, "the missing one was not silently dropped")
        check(gone and not gone[0].exists, "and it is marked as not on disk")
        check(gone and gone[0].passages == 0 and gone[0].bytes == 0,
              "with no invented size or passage count")


def test_find_resolves_only_what_the_profile_named():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        (tmp / "here.md").write_text(BY_HAND, encoding="utf-8")
        (tmp / "secret.md").write_text("not in the library", encoding="utf-8")
        library = knowledge.Library(paths=[tmp / "here.md"])

        check(knowledge.find(library, "here.md") == tmp / "here.md",
              "a document is found by its name")
        check(knowledge.find(library, str(tmp / "here.md")) == tmp / "here.md",
              "and by its full path")
        check(knowledge.find(library, "secret.md") is None,
              "a real file that the profile did not name is refused")
        for attack in ("../../etc/passwd", "/etc/passwd", "here.md/../../../etc/passwd"):
            check(knowledge.find(library, attack) is None,
                  f"and so is {attack!r}")


def test_original_for_finds_the_paper_and_refuses_a_traversal():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        (tmp / ".text").mkdir()
        text = tmp / ".text" / "pump-7j.md"
        text.write_text(DERIVED, encoding="utf-8")

        check(knowledge.original_for(text, "pump-7j.pdf") is None,
              "no paper is reported when there is no paper")

        (tmp / "pump-7j.pdf").write_bytes(b"%PDF-1.4\n")
        found = knowledge.original_for(text, "pump-7j.pdf")
        check(found == tmp / "pump-7j.pdf",
              f"the PDF one level up is found ({found})")

        # A renamed PDF: the header still names the old one, the stem still agrees.
        (tmp / "pump-7j.pdf").unlink()
        (tmp / "pump-7j-scan.pdf").write_bytes(b"%PDF-1.4\n")
        renamed = text.parent.parent / "pump-7j.pdf"
        check(not renamed.exists() and knowledge.original_for(text, "pump-7j.pdf") is None,
              "a PDF renamed away from both the header and the stem is not substituted for")

        # The recorded name is read off disk and must never escape the boat's folder.
        outside = Path(raw).parent / "escaped.pdf"
        try:
            outside.write_bytes(b"%PDF-1.4\n")
            got = knowledge.original_for(text, f"../../{outside.name}")
            check(got is None, f"a traversal in the recorded source name is refused ({got})")
        finally:
            outside.unlink(missing_ok=True)


# --------------------------------------------------------------------------------------
def test_the_routes_the_console_reads():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        (tmp / "notes.md").write_text(BY_HAND, encoding="utf-8")
        (tmp / "pump-7j.md").write_text(DERIVED, encoding="utf-8")
        (tmp / "pump-7j.pdf").write_bytes(b"%PDF-1.4\n% a paper\n")
        profile = a_boat(tmp, ["notes.md", "pump-7j.md", "vanished.md"])

        with Env(OPENBOAT_PROFILE=str(profile)), Dashboard() as dash:
            status, body = dash.get("/api/docs")
            check(status == 200, f"GET /api/docs answers ({status})")
            check(body["count"] == 3 and body["missing"] == 1,
                  f"counting the missing one in ({body['count']}, {body['missing']})")
            check(body["gaps"] == 1, f"and totalling the pages needing OCR ({body['gaps']})")
            names = [d["name"] for d in body["documents"]]
            check("vanished.md" in names, "the missing document is listed by name")
            paper = next(d for d in body["documents"] if d["name"] == "pump-7j.md")
            check(paper["original"] == "pump-7j.pdf",
                  f"the original beside a derived file is reported ({paper['original']!r})")

            status, body = dash.get("/api/doc?name=notes.md")
            check(status == 200 and body["exists"] and "gearbox oil" in body["text"],
                  "GET /api/doc returns a named document whole")

            status, body = dash.get("/api/doc?name=vanished.md")
            check(status == 200 and body["exists"] is False and "not on disk" in body["error"],
                  "a named-but-missing document is an honest answer, not a 500")

            status, body = dash.get("/api/doc?name=" + urllib.parse.quote("../boat.toml"))
            check("error" in (body or {}) and "text" not in (body or {}),
                  "and a path the profile never named is refused")

            status, body = dash.get("/paper?name=pump-7j.md")
            check(status == 200 and isinstance(body, bytes) and body.startswith(b"%PDF"),
                  f"GET /paper serves the original ({status})")

            status, body = dash.get("/paper?name=notes.md")
            check(status == 404, f"and 404s when there is no paper beside it ({status})")

            status, body = dash.get("/paper?name=" + urllib.parse.quote("../../etc/passwd"))
            check(status == 404, f"a traversal gets nothing ({status})")

            status, body = dash.get("/api/profile")
            check(status == 200 and body.get("version"),
                  "the profile route carries the version the console prints")

            status, body = dash.get("/console.html")
            check(status == 200 and b"OpenBoat" in body, "and the page itself is served")


def test_the_console_page_holds_no_boat_facts():
    """It ships in a public repository. Every name and number on it arrives from the API."""
    page = console_source()
    check(CONSOLE.exists() and len(page) > 10_000, "the console page and its views are there")
    check("/api/profile" in page and "/api/docs" in page and "/api/snags" in page,
          "and reads its content from the API")

    from openboat.profile import load
    demo = load(ROOT / "profiles" / "demo-boat.toml")
    check(demo.vessel.name not in page,
          "no vessel name is written into the page, not even the demo boat's")

    # The dashboard behind this page accepts no writes from it. Every POST the console
    # makes goes to one of exactly two shell functions, and both are in this file rather
    # than in a view: `snagPost()` reaches the snag service — its own port on a LAN,
    # relayed at `<ROOT>/snag` behind the gate — and `gatePost()` reaches the gate's own
    # account routes at `<ROOT>/account/…`. Callers choose the *route*, by passing `path`
    # or a path; they do not get to open a fetch of their own. A third POST anywhere, or
    # one aimed at this server's own /api/, is how a read-only thing stops being one.
    posts = [m.start() for m in re.finditer(r"""method:\s*["']POST["']""", page)]
    check(len(posts) == 2,
          f"exactly two POSTs in the console, in snagPost() and gatePost() (found {len(posts)})")
    shell = CONSOLE.read_text(encoding="utf-8")
    for name in ("snagPost", "gatePost"):
        fn = shell.find(f"async function {name}(")
        end = shell.find("\n}\n", fn)
        check(fn > 0 and shell.find('method: "POST"', fn, end) > 0,
              f"and one of them lives inside {name}()")
    check(not re.search(r"""fetch\(\s*["'`]/api/[^)]*method""", page),
          "nothing posts to the dashboard's own /api/")


if __name__ == "__main__":
    print(__doc__.splitlines()[0])
    print("-" * 78)
    for case in (test_describe_tells_written_from_extracted,
                 test_a_missing_document_is_reported_not_dropped,
                 test_find_resolves_only_what_the_profile_named,
                 test_original_for_finds_the_paper_and_refuses_a_traversal,
                 test_the_routes_the_console_reads,
                 test_the_console_page_holds_no_boat_facts):
        case()
    print("-" * 78)
    failed = [what for ok, what in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} checks pass"
          + (f" — FAILED: {failed}" if failed else ""))
    sys.exit(1 if failed else 0)
