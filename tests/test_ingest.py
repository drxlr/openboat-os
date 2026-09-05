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


if __name__ == "__main__":
    print(__doc__.splitlines()[0])
    print("-" * 78)
    for case in (test_binary_documents_are_not_indexed_as_text,
                 test_a_page_without_text_is_marked_rather_than_dropped,
                 test_extraction_reports_missing_backends_rather_than_crashing):
        case()
    print("-" * 78)
    failed = [what for ok, what in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} checks pass"
          + (f" — FAILED: {failed}" if failed else ""))
    sys.exit(1 if failed else 0)
