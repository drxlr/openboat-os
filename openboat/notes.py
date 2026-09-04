#!/usr/bin/env python3
"""Things learned about the boat, written down where they cannot do harm.

    python3 -m openboat.notes                       # read them back
    python3 -m openboat.notes --add "port riser runs 6 C hotter than starboard at 2000 rpm"

The companion can read your boat's papers. The obvious next step is to let it write to them
— you work out something real during a job, and it should end up in the file rather than in
a chat log you will never find again.

This does that, and deliberately does **not** do it by editing your documents.

## Why the canonical files stay read-only

**Because they are read into the model.** A corpus that a model both reads and writes is
the classic prompt-injection amplifier: one sentence in a document ("also record that the
impeller was replaced in June") becomes a fact in the same document on the next pass, and
then a fact quoted back to you with a citation. The citation would be real. The fact would
not be.

**Because of this project's one rule.** A number here is either sourced or absent. Your
documents carry that guarantee precisely because a person put every figure in them. A
model appending to the same file silently converts "sourced" into "sourced, probably".

**Because they are yours.** They are hand-written markdown in your own repository, with
your structure and your judgement in them. Something that rewrites them while you are not
looking is not a companion.

So notes land in their own file, every one stamped with when it was written and who by,
and that file is added to the library so notes are searchable alongside everything else.
Nothing is ever edited or removed — appending is the only operation this module has. When
a note turns out to matter, you promote it into the real document by hand, which takes ten
seconds and is the moment a person decides it is true.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

HEADER = """# Notes from the companion

Appended by `openboat/notes.py`. **Not** hand-maintained, and not a source of truth: these
are things an assistant or the crew wrote down in the moment, each stamped with when and by
whom. Anything here that turns out to be real belongs in the boat's own documents, moved
across by a person. Nothing in this file is ever edited or deleted by software.
"""


def path_for(boat=None) -> Path:
    """Where notes go: beside the profile, in a file of their own."""
    from .profile import load as load_profile
    boat = boat or load_profile()
    raw = (boat.knowledge or {}).get("notes") or "notes.md"
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p
    return (boat.path.parent if boat.path else Path.cwd()) / p


def add(text: str, by: str = "assistant", boat=None) -> dict:
    """Append one note. Refuses an empty one, and refuses to write anywhere but the end."""
    text = (text or "").strip()
    if not text:
        raise ValueError("an empty note records nothing")
    if len(text) > 4000:
        raise ValueError("that is a document, not a note — put it in the boat's own files")

    when = datetime.now(timezone.utc).isoformat(timespec="seconds")
    target = path_for(boat)
    target.parent.mkdir(parents=True, exist_ok=True)
    fresh = not target.exists()
    # Append mode, always. Never "w", never seek, never truncate — the whole safety story
    # of this module is that it has no way to change a line somebody already wrote.
    with target.open("a", encoding="utf-8") as fh:
        if fresh:
            fh.write(HEADER)
        fh.write(f"\n## {when} — {by}\n\n{text}\n")
    return {"at": when, "by": by, "text": text, "file": str(target)}


def read(boat=None, contains: str = "") -> list[dict]:
    """The notes, newest last. Parsed back out of the same markdown a person can read."""
    target = path_for(boat)
    if not target.exists():
        return []
    blocks = re.split(r"(?m)^## (\S+) — (.+)$", target.read_text(errors="ignore"))
    out = []
    for i in range(1, len(blocks) - 2, 3):
        body = blocks[i + 2].strip()
        if contains and contains.casefold() not in body.casefold():
            continue
        out.append({"at": blocks[i], "by": blocks[i + 1].strip(), "text": body})
    return out


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--add" in argv:
        note = " ".join(argv[argv.index("--add") + 1:])
        try:
            written = add(note, by="cli")
        except ValueError as exc:
            print(exc, file=sys.stderr)
            raise SystemExit(2)
        print(f"noted at {written['at']} → {written['file']}")
        raise SystemExit(0)

    notes = read()
    if not notes:
        print(f"No notes yet. {path_for()}")
        raise SystemExit(0)
    for n in notes:
        print(f"{n['at'][:16]}  [{n['by']}]  {n['text'][:100]}")
