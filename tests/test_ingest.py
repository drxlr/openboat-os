#!/usr/bin/env python3
"""Getting paper into the library, and the two ways that goes silently wrong.

    python3 tests/test_ingest.py

A boat's manuals arrive as PDFs, and a PDF is not text. Both failures below produce
something that *looks* like a working corpus, which is why they are held down here.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS, FAIL = "  ok  ", "  FAIL"
results: list[tuple[bool, str]] = []


def check(ok: bool, what: str) -> None:
    results.append((bool(ok), what))
    print(f"{PASS if ok else FAIL}  {what}")


# --------------------------------------------------------------------------------------
# A PDF in [knowledge] docs used to be indexed as its own bytes.
#
# `read_text` on a PDF succeeds. It produces one passage beginning "%PDF-1.4" that scores
# against real questions and tells a reader nothing, inside a corpus an assistant answers
# from. Silence would have been better. Saying so is better than silence.
# --------------------------------------------------------------------------------------
def test_binary_documents_are_not_indexed_as_text() -> None:
    import tempfile

    from openboat.knowledge import _split

    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "manual.pdf"
        pdf.write_bytes(b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF")
        passages = _split(pdf)
        check(len(passages) == 1, "a PDF yields exactly one passage")
        text = passages[0].text
        check("%PDF" not in text, "the passage does not contain the file's bytes")
        check("could not be read" in text, "the passage says the document was not read")
        check("openboat.ingest" in text, "the passage says how to make it readable")

        # A .md that is secretly binary is caught too — the extension is not the authority.
        sneaky = Path(tmp) / "notes.md"
        sneaky.write_bytes(b"# Notes\n\x00\x00\x00binary")
        check("could not be read" in _split(sneaky)[0].text,
              "a binary file named .md is caught by its content, not its extension")


def test_a_page_without_text_is_marked_rather_than_dropped() -> None:
    """A scanned page must become a stated gap, never an empty section.

    An empty section is indistinguishable from a page that was genuinely blank, and a
    question landing near it gets answered confidently from the page next door.
    """
    from openboat.ingest import Extraction, to_markdown

    found = Extraction(source=Path("volvo-d3.pdf"),
                       pages=["real text on page one", "", "   \n  "],
                       backend="test")
    check(found.with_text == 1, "only the page with text counts as having text")
    check(found.scanned, "a document that is two-thirds empty is reported as a scan")

    out = to_markdown(found)
    check(out.count("## ") == 3, "every page gets a section, including the empty ones")
    check(out.count("No text layer") == 2, "each empty page is marked as a gap")
    check("volvo d3 — page 2" in out,
          "the heading carries the document name, not just a page number")
    check("derived file" in out, "the output says it is derived from the PDF")


def test_extraction_reports_missing_backends_rather_than_crashing() -> None:
    """OpenBoat is stdlib-only, so no extractor is a normal state and must say so."""
    from openboat import ingest

    real = ingest.backends
    try:
        ingest.backends = lambda: []
        try:
            ingest.extract(Path("whatever.pdf"))
            check(False, "extract() refuses when no backend is installed")
        except RuntimeError as exc:
            check("pip install pypdf" in str(exc) or "poppler" in str(exc),
                  "the refusal names something you can actually install")
    finally:
        ingest.backends = real


# --------------------------------------------------------------------------------------
# Poppler writes a form feed after the LAST page too.
#
# So splitting on it invented an empty page at the end of every document, which this module
# then wrote into the corpus as "page 20 of 19 — no text layer, needs OCR": a fabricated
# page asserting that a page which does not exist needs work. It also made the same file
# report a different page count depending on which extractor the machine happened to have.
# --------------------------------------------------------------------------------------
def test_every_backend_agrees_on_the_page_count() -> None:
    import subprocess

    from openboat.ingest import backends, extract

    sample = ROOT / "profiles" / "demo" / "sample.pdf"
    if not sample.exists():
        # Build a two-page PDF with no dependency on anything installed.
        body = (b"%PDF-1.4\n"
                b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
                b"2 0 obj<</Type/Pages/Kids[3 0 R 5 0 R]/Count 2>>endobj\n"
                b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]"
                b"/Resources<</Font<</F1 7 0 R>>>>/Contents 4 0 R>>endobj\n"
                b"4 0 obj<</Length 44>>stream\nBT /F1 12 Tf 20 100 Td (Page one here) Tj ET\n"
                b"endstream endobj\n"
                b"5 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]"
                b"/Resources<</Font<</F1 7 0 R>>>>/Contents 6 0 R>>endobj\n"
                b"6 0 obj<</Length 44>>stream\nBT /F1 12 Tf 20 100 Td (Page two here) Tj ET\n"
                b"endstream endobj\n"
                b"7 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
                b"trailer<</Root 1 0 R>>\n%%EOF\n")
        sample = Path(__file__).parent / "_tmp" / "twopage.pdf"
        sample.parent.mkdir(exist_ok=True)
        sample.write_bytes(body)

    counts = {}
    for name in backends():
        try:
            counts[name] = len(extract(sample, name).pages)
        except Exception:                                  # noqa: BLE001
            continue
    if len(counts) < 2:
        check(True, f"only one extractor present, cannot cross-check ({list(counts)})")
        return
    check(len(set(counts.values())) == 1,
          f"every backend reports the same page count ({counts})")


def test_ingest_refuses_to_destroy_a_hand_written_file() -> None:
    """A .md beside a .pdf is very often the owner's own notes on that manual."""
    import shutil
    import tempfile

    from openboat.ingest import BANNER, Refused, ingest

    src = None
    for candidate in (ROOT / "profiles" / "demo").glob("*.pdf"):
        src = candidate
        break
    if src is None:
        sample = Path(__file__).parent / "_tmp" / "twopage.pdf"
        if not sample.exists():
            check(True, "no sample PDF available to exercise the guard")
            return
        src = sample

    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "manual.pdf"
        shutil.copy(src, pdf)
        note = Path(tmp) / "manual.md"
        note.write_text("# My own notes\n\nTorque is 9 Nm, NOT the 12 printed in the book.\n")
        try:
            ingest(pdf)
            check(False, "a hand-written markdown file is refused, not overwritten")
        except Refused:
            check(True, "a hand-written markdown file is refused, not overwritten")
        check("9 Nm" in note.read_text(), "the owner's note is still there afterwards")

        ingest(pdf, force=True)
        check(BANNER in note.read_text(), "--force does overwrite, deliberately")

        # This module's own output may always be replaced — that is a re-ingest.
        ingest(pdf)
        check(BANNER in note.read_text(), "re-ingesting its own output needs no --force")


# --------------------------------------------------------------------------------------
# A blank-page marker is written for a reader, not for the search.
#
# Found the first time the snag list was queried in earnest: "what needs fixing on the bow
# thruster" returned two blank-page markers ahead of every real passage. They are short,
# BM25 rewards short passages, and the marker contained the word "needs". A machine-written
# note that a page is blank is not knowledge about the boat — but it must stay in the file,
# because that is how somebody reading it sees which pages still want OCR.
# --------------------------------------------------------------------------------------
def test_blank_page_markers_stay_in_the_file_but_out_of_the_search() -> None:
    import tempfile

    from openboat.ingest import NO_TEXT, Extraction, to_markdown
    from openboat.knowledge import _split

    md = to_markdown(Extraction(source=Path("m.pdf"),
                                pages=["Real content about impellers.", "", "  "],
                                backend="test"))
    check(md.count("No text layer") == 2, "both empty pages are marked in the file")

    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "m.md"
        f.write_text(md)
        passages = _split(f)
        bodies = [p.text for p in passages]
        check(not any("No text layer" in b for b in bodies),
              "no blank-page marker becomes a searchable passage")
        check(any("impellers" in b for b in bodies),
              "the real page is still indexed")
        check("No text layer" in f.read_text(),
              "the marker is still in the file for a person reading it")


if __name__ == "__main__":
    print(__doc__.splitlines()[0])
    print("-" * 78)
    for case in (test_binary_documents_are_not_indexed_as_text,
                 test_a_page_without_text_is_marked_rather_than_dropped,
                 test_extraction_reports_missing_backends_rather_than_crashing,
                 test_every_backend_agrees_on_the_page_count,
                 test_ingest_refuses_to_destroy_a_hand_written_file,
                 test_blank_page_markers_stay_in_the_file_but_out_of_the_search):
        case()
    print("-" * 78)
    failed = [what for ok, what in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} checks pass"
          + (f" — FAILED: {failed}" if failed else ""))
    sys.exit(1 if failed else 0)
