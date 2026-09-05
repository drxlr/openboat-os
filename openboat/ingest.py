#!/usr/bin/env python3
"""Turn a manual into something the boat's library can actually read.

    python3 -m openboat.ingest volvo-penta-d3.pdf          # → volvo-penta-d3.md beside it
    python3 -m openboat.ingest ~/manuals/*.pdf --out docs/
    python3 -m openboat.ingest ~/manuals/*.pdf --check     # what is text, what is a scan

Every boat comes with a bag of paper: the engine manual, the warranty booklet, the short
guide for the washer-dryer somebody installed, the yard's own handover file. It is all true
and specific to this hull, and none of it is reachable from the engine bay at seven in the
morning. The retrieval in `openboat.knowledge` makes text answerable — but a PDF is not
text, and until this module existed the only route in was somebody typing it out.

Three rules, and each one costs something:

**A page number is part of the answer.** Every page becomes its own passage headed with the
document's name and the page it came from, so an answer can be walked back to the paper and
checked. A manual quoted without a page is a manual you have to trust; with a page it is one
you can verify. This is the same reason `openboat.knowledge` quotes a line number.

**A page with no text says so.** Half the manuals on any boat are scans — an image of a page
inside a PDF wrapper, with no text layer at all. Extracting one yields an empty string, and
an empty section is indistinguishable from a page that was genuinely blank. So a page that
yields nothing is written out as a marked gap. It is retrievable, it is honest, and it tells
you exactly which pages still need OCR instead of leaving you to discover it by asking a
question and getting a confident wrong answer from a neighbouring page.

**No new dependencies.** This package is stdlib-only so it runs on a Pi with nothing
installed, and that is not negotiable for one module. Extraction therefore uses whatever the
machine already has — `pdftotext` from poppler, or `pypdf`, or PyMuPDF — and if it has none
of them it says which one to install rather than failing obscurely.

The output is a *derived* file and marks itself as one in its own header. The PDF stays the
original: if the text is wrong, the paper is right, and the fix is to re-ingest rather than
to edit the markdown and lose the correction the next time somebody runs this.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

__all__ = ["Extraction", "extract", "to_markdown", "ingest", "backends", "Refused"]

#: The line every derived file carries. It is how `ingest` recognises its own output and
#: therefore what it is allowed to overwrite — see `_may_overwrite`.
BANNER = "This is a derived file — the PDF is the original."

#: Written into a page that produced no text, so the gap is searchable rather than silent.
#: Phrased as a sentence because it ends up in a corpus an assistant reads: it has to state
#: plainly that nothing is known about the page, or the model will answer from the page next
#: to it and sound just as sure.
NO_TEXT = ("**No text layer on this page — it is a scanned image.** Nothing here knows what "
           "this page says. It needs OCR before it can be answered from.")


@dataclass
class Extraction:
    """What came out of one document: the pages, and how many of them held any text."""

    source: Path
    pages: list[str]
    backend: str

    @property
    def with_text(self) -> int:
        return sum(1 for p in self.pages if p.strip())

    @property
    def scanned(self) -> bool:
        """True when the file is mostly or entirely images of pages.

        Deliberately not "any page is empty": a real manual has blank pages, section dividers
        and inside covers. The question worth answering is whether this document needs OCR
        before it is worth anything, and a document where most pages yield nothing does.
        """
        return bool(self.pages) and self.with_text <= len(self.pages) // 3


def backends() -> list[str]:
    """Which extractors this machine actually has, best first."""
    found = []
    if shutil.which("pdftotext"):
        found.append("pdftotext")
    for module in ("fitz", "pypdf"):
        try:
            __import__(module)
        except ImportError:
            continue
        found.append("pymupdf" if module == "fitz" else module)
    return found


def _pdftotext(path: Path) -> list[str]:
    """Poppler, preferred because it keeps the reading order of a two-column page.

    The trailing form feed matters. Poppler writes one after *every* page including the
    last, so a plain split invents an extra empty page at the end — which this module then
    faithfully wrote into the corpus as "page 20 of 19, no text layer, needs OCR". A
    fabricated page asserting that a page which does not exist needs work is exactly the
    kind of confident wrong statement the corpus must not contain, and it also made the
    same file report a different page count depending on which extractor the machine had.
    """
    out = subprocess.run(["pdftotext", "-layout", str(path), "-"],
                         capture_output=True, text=True, timeout=300)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "pdftotext failed")
    pages = out.stdout.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    return pages


def _pymupdf(path: Path) -> list[str]:
    import fitz

    with fitz.open(path) as doc:
        return [page.get_text() for page in doc]


def _pypdf(path: Path) -> list[str]:
    from pypdf import PdfReader

    return [(page.extract_text() or "") for page in PdfReader(str(path)).pages]


EXTRACTORS = {"pdftotext": _pdftotext, "pymupdf": _pymupdf, "pypdf": _pypdf}


def extract(path: Path, backend: str | None = None) -> Extraction:
    """Pull the text out of a PDF, one string per page.

    Tries the requested backend, or the best one present. Falls through to the next on a
    failure rather than giving up, because a PDF that defeats one library often yields to
    another — malformed files are the norm in a bag of twenty-year-old manuals, not the
    exception.
    """
    available = [backend] if backend else backends()
    if not available or available == [None]:
        raise RuntimeError(
            "no PDF text extractor on this machine. Install one:\n"
            "    brew install poppler        (gives pdftotext, the best of the three)\n"
            "    pip install pypdf           (pure Python, no system package)\n"
            "OpenBoat itself stays stdlib-only, so this stays your choice rather than a "
            "dependency everybody carries."
        )

    problems = []
    for name in available:
        try:
            pages = EXTRACTORS[name](path)
        except Exception as exc:                       # noqa: BLE001 — try the next one
            problems.append(f"{name}: {exc}")
            continue
        return Extraction(source=path, pages=pages, backend=name)
    raise RuntimeError(f"could not read {path.name} — " + "; ".join(problems))


def to_markdown(found: Extraction, title: str | None = None) -> str:
    """One markdown document, one section per page.

    The heading carries the document's name as well as the page number. That is not
    decoration: with twenty manuals in one library, a passage headed "Page 14" is unusable,
    and the retrieval scores headings above bodies — so the name of the manual has to be in
    the heading of every passage for a question naming the manual to find it.
    """
    name = title or found.source.stem.replace("_", " ").replace("-", " ")
    head = [
        f"# {name}",
        "",
        f"> Text extracted from `{found.source.name}` on {date.today().isoformat()} "
        f"by `openboat.ingest` using {found.backend}.",
        ">",
        f"> **{BANNER}** If a passage here reads "
        "wrongly, the paper is right. Re-ingest rather than editing this file by hand, or "
        "the correction is lost the next time it is run.",
        "",
        f"> {len(found.pages)} pages, {found.with_text} with a text layer."
        + ("  **Most of this document is scanned images and needs OCR.**"
           if found.scanned else ""),
        "",
    ]
    body = []
    for number, text in enumerate(found.pages, start=1):
        body.append(f"## {name} — page {number}")
        body.append("")
        body.append(text.strip() or NO_TEXT)
        body.append("")
    return "\n".join(head + body)


def _may_overwrite(target: Path) -> bool:
    """True unless the file already there is something a person wrote.

    A `.md` beside a `.pdf` is the ordinary shape of the very directory `[knowledge] docs`
    points at, so the file about to be written may well be an owner's own notes on that
    manual — the sentence that says the torque figure in the book is wrong. Overwriting it
    silently destroys the most valuable document on the boat: the one that exists nowhere
    else. Only this module's own output, recognised by its banner, may be replaced.
    """
    if not target.exists():
        return True
    head = target.read_text(encoding="utf-8", errors="ignore")[:2000]
    return BANNER in head


class Refused(RuntimeError):
    """The target file was written by a person and would have been destroyed."""


def ingest(path: Path, out_dir: Path | None = None, backend: str | None = None,
           force: bool = False) -> Path:
    """Ingest one PDF and write the markdown next to it, or into `out_dir`."""
    found = extract(path, backend)
    target = (out_dir or path.parent) / (path.stem + ".md")
    if not force and not _may_overwrite(target):
        raise Refused(
            f"{target} already exists and was not written by openboat.ingest. Refusing to "
            f"overwrite it — it may be your own notes. Move it, or pass --force.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(to_markdown(found), encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m openboat.ingest",
        description="Turn PDFs into markdown the boat's library can answer from.")
    parser.add_argument("pdfs", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, default=None,
                        help="directory for the markdown (default: beside each PDF)")
    parser.add_argument("--backend", choices=sorted(EXTRACTORS), default=None)
    parser.add_argument("--force", action="store_true",
                        help="overwrite a markdown file this tool did not write")
    parser.add_argument("--check", action="store_true",
                        help="report what is text and what is a scan, and write nothing")
    args = parser.parse_args(argv)

    if not backends():
        print("No PDF text extractor found. Install poppler (`brew install poppler`) or "
              "pypdf (`pip install pypdf`).", file=sys.stderr)
        return 2

    scans, done, failed = [], [], []
    for pdf in args.pdfs:
        if not pdf.is_file():
            print(f"  MISSING  {pdf}", file=sys.stderr)
            failed.append(pdf)
            continue
        try:
            found = extract(pdf, args.backend)
        except RuntimeError as exc:
            print(f"  FAILED   {pdf.name}: {exc}", file=sys.stderr)
            failed.append(pdf)
            continue

        state = "SCAN" if found.scanned else "text"
        print(f"  {state:8} {pdf.name}  —  {found.with_text}/{len(found.pages)} pages "
              f"with text  ({found.backend})")
        if found.scanned:
            scans.append(pdf)
        if not args.check:
            target = (args.out or pdf.parent) / (pdf.stem + ".md")
            if not args.force and not _may_overwrite(target):
                print(f"  REFUSED  {target} exists and openboat.ingest did not write it. "
                      f"It may be your own notes — move it, or pass --force.", file=sys.stderr)
                failed.append(pdf)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(to_markdown(found), encoding="utf-8")
            done.append(target)

    if done:
        print(f"\nWrote {len(done)} markdown file(s). Add them to [knowledge] docs in the "
              f"profile to make them answerable.")
    if scans:
        print(f"\n{len(scans)} document(s) are scanned images with no text layer. Extracting "
              f"them produced marked gaps rather than silence, which is honest but not "
              f"useful — they need OCR to become answerable:")
        for pdf in scans:
            print(f"  {pdf}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
