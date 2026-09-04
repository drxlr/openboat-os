#!/usr/bin/env python3
"""Documents brought in from photographs — transcribed, attributed, never invented.

    python3 -m openboat.documents                    # what has been taken in
    python3 -m openboat.documents --files            # image and PDF files on disk

The most common way a fact about a boat exists is as a photograph: the CE plate, the engine
serial, the registration certificate, a handwritten quote from a yard. The fact is right
there and it is unreachable — not searchable, not quotable, and gone the moment you cannot
remember which of four hundred photos it was in.

An assistant with vision can read those. This is where what it read goes.

## The rule that makes this safe

**Transcribe, do not interpret.** A hull identification number, an MMSI, an engine serial,
a policy number — these are strings where one wrong character is worse than a blank. A
model that half-reads `4012241991` and confidently writes `4012241991` is useful; one that
fills in a smudged digit from context is dangerous, and the danger is invisible because the
output looks identical.

So every entry records **what was read**, **what it was read from**, and **what could not be
made out** — and the tool that writes them says so to the model in as many words. A
character nobody could see is written `?`, not guessed.

Entries land in their own file, marked as transcriptions rather than sources, and joined to
the search library so they are findable. As with `openboat/notes.py`, nothing here edits or
deletes: the boat's real documents stay a thing a person maintains, and a transcription
gets promoted into them by hand once somebody has looked at the photo and agreed.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

#: What a photographed document usually is. Free text is allowed; this is a hint, not a
#: gate — refusing an unfamiliar kind would only make people mislabel things.
KINDS = ("registration", "insurance", "survey", "invoice", "quote", "plate",
         "manual", "certificate", "photo", "other")

FILE_TYPES = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".pdf", ".tif", ".tiff"}

HEADER = """# Transcribed documents

Written by `openboat/documents.py` from photographs. **These are transcriptions, not
sources.** Each says what was read and what could not be made out; a character nobody could
see is written `?` rather than guessed. Nothing here is edited or deleted by software.

Before relying on a number from this file — a hull number, a serial, a policy number — look
at the photograph. When it is confirmed, move it into the boat's own documents by hand.
"""


def path_for(boat=None) -> Path:
    from .profile import load as load_profile
    boat = boat or load_profile()
    raw = (boat.knowledge or {}).get("transcriptions") or "transcriptions.md"
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p
    return (boat.path.parent if boat.path else Path.cwd()) / p


def photo_dirs(boat=None) -> list[Path]:
    """Where the boat's own images live, taken from the profile's document list.

    Deliberately derived rather than configured separately: the folders worth looking in
    are the ones beside the documents somebody already pointed at.
    """
    from .profile import load as load_profile
    boat = boat or load_profile()
    base = boat.path.parent if boat.path else Path.cwd()
    seen: list[Path] = []
    for raw in (boat.knowledge or {}).get("docs", []):
        p = Path(raw).expanduser()
        p = p if p.is_absolute() else (base / p)
        for candidate in (p.parent, p.parent / "photos", p.parent / "boat" / "photos"):
            if candidate.is_dir() and candidate not in seen:
                seen.append(candidate)
    return seen


def files(boat=None, contains: str = "") -> list[dict]:
    """Image and PDF files sitting in those folders. Listed, never opened or moved."""
    out = []
    for folder in photo_dirs(boat):
        for f in sorted(folder.iterdir()):
            if f.suffix.lower() not in FILE_TYPES or not f.is_file():
                continue
            if contains and contains.casefold() not in f.name.casefold():
                continue
            out.append({"name": f.name, "path": str(f),
                        "kb": round(f.stat().st_size / 1024)})
    return out


def take_in(title: str, read: str, source: str = "", kind: str = "other",
            unclear: str = "", boat=None) -> dict:
    """Record what was read off a document. Append-only.

    `read` is the transcription. `source` is what it was read from — a filename, "photo
    sent in chat on 4 September", a scan. `unclear` is the part that could not be made out,
    and leaving it empty is a claim that everything was legible.
    """
    title = (title or "").strip()
    read = (read or "").strip()
    if not title or not read:
        raise ValueError("a transcription needs a title and the text that was read")
    if len(read) > 20000:
        raise ValueError("that is a whole document; put the file on disk and reference it")

    when = datetime.now(timezone.utc).isoformat(timespec="seconds")
    target = path_for(boat)
    target.parent.mkdir(parents=True, exist_ok=True)
    fresh = not target.exists()
    with target.open("a", encoding="utf-8") as fh:
        if fresh:
            fh.write(HEADER)
        fh.write(f"\n## {title}\n\n"
                 f"- **kind**: {kind}\n"
                 f"- **read from**: {source or 'not stated'}\n"
                 f"- **transcribed**: {when}\n"
                 f"- **could not be made out**: {unclear or 'nothing stated'}\n\n"
                 f"{read}\n")
    return {"title": title, "kind": kind, "source": source, "at": when,
            "unclear": unclear, "file": str(target)}


def read_all(boat=None) -> list[dict]:
    target = path_for(boat)
    if not target.exists():
        return []
    parts = re.split(r"(?m)^## (.+)$", target.read_text(errors="ignore"))
    return [{"title": parts[i].strip(), "body": parts[i + 1].strip()}
            for i in range(1, len(parts) - 1, 2)]


if __name__ == "__main__":
    if "--files" in sys.argv:
        found = files()
        if not found:
            print("No image or PDF files found beside the documents in your profile.")
        for f in found:
            print(f"  {f['name']:<62} {f['kb']:>6} kB")
        raise SystemExit(0)
    entries = read_all()
    if not entries:
        print(f"Nothing transcribed yet. {path_for()}")
        raise SystemExit(0)
    for e in entries:
        print(f"\n\033[1m{e['title']}\033[0m\n{e['body'][:400]}")
