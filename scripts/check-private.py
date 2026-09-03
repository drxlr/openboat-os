#!/usr/bin/env python3
"""Refuse to commit a private fact into the public repository.

This is the mechanism that lets one person work on a public project and their own boat at
the same time without eventually pasting the wrong thing into the wrong file. It is not a
guideline. It runs as a pre-commit hook and it fails the commit.

    python3 scripts/check-private.py            # check the whole working tree
    python3 scripts/check-private.py --staged   # check what is about to be committed
    scripts/install-hooks.sh                    # wire it into git

## What it looks for

Two kinds of thing:

**Named identifiers** — a vessel name, a person, a machine on a private network. These are
listed in `.private-markers`, one pattern per line, and that file is *gitignored*: your
list of private words is itself private, which is the point. A default list is created on
first run and covers the obvious shapes.

**Position** — any coordinate pair inside a box you declare as home. A boat's berth is
personal data of a kind people rarely think about: it says where a valuable object sits
unattended, and it is far harder to retract than a name. Declare the box in
`.private-markers` as `BOX lat_min lat_max lon_min lon_max` and any literal coordinate
inside it fails the check.

## What it deliberately does not do

It does not scan for secrets — API keys, tokens, passwords. Use a real secret scanner for
that; this one is about *facts*, which no secret scanner is looking for. It also cannot
read your mind: it catches the shapes you tell it about, so when you add a boat, a berth or
a crew member to your private overlay, add the word here too.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKERS = ROOT / ".private-markers"

DEFAULT_MARKERS = """\
# Words that must never appear in this public repository, one regex per line.
# This file is gitignored on purpose: the list of your private words is itself private.
#
# Add to it whenever your private overlay gains a name — a boat, a berth, a person, a
# machine on your tailnet. The check is case-insensitive.

# --- the machine and the person (edit these) ---
/Users/[a-z0-9_.-]+
[a-z0-9-]+\\.ts\\.net

# --- shapes that are private whatever they are called ---
# An MMSI is nine digits and identifies one specific vessel.
\\bMMSI\\s*[:=]\\s*[0-9]{9}\\b

# --- your own names go here, one per line, e.g.:
# MyBoatName
# TheMarinaName

# --- a box of coordinates that must never be committed ---
# BOX lat_min lat_max lon_min lon_max
# BOX 34.5 35.2 33.3 34.0
"""

#: Files where a coordinate is expected and legitimate: the demo profile and its docs.
COORD_ALLOWED = {"profiles/demo-boat.toml"}

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv"}
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".sqlite", ".sqlite-shm",
                 ".sqlite-wal", ".ico", ".woff", ".woff2", ".zip", ".gz"}

COORD = re.compile(r"(-?\d{1,3}\.\d{3,})\s*[,;\s]\s*(-?\d{1,3}\.\d{3,})")


def load_markers() -> tuple[list[re.Pattern], list[tuple[float, float, float, float]]]:
    if not MARKERS.is_file():
        MARKERS.write_text(DEFAULT_MARKERS)
        print(f"created {MARKERS.name} — add your private words to it", file=sys.stderr)

    words: list[re.Pattern] = []
    boxes: list[tuple[float, float, float, float]] = []
    for raw in MARKERS.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.upper().startswith("BOX "):
            try:
                a, b, c, d = (float(x) for x in line.split()[1:5])
                boxes.append((a, b, c, d))
            except ValueError:
                print(f"ignoring malformed BOX line: {line}", file=sys.stderr)
            continue
        try:
            words.append(re.compile(line, re.IGNORECASE))
        except re.error as exc:
            print(f"ignoring bad pattern {line!r}: {exc}", file=sys.stderr)
    return words, boxes


def files_to_check(staged: bool) -> list[Path]:
    if staged:
        out = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
                             cwd=ROOT, capture_output=True, text=True).stdout
        return [ROOT / name for name in out.split("\n") if name.strip()]
    found = []
    for path in ROOT.rglob("*"):
        if path.is_file() and not any(part in SKIP_DIRS for part in path.parts):
            found.append(path)
    return found


def main(argv: list[str]) -> int:
    staged = "--staged" in argv
    words, boxes = load_markers()
    hits: list[str] = []

    for path in files_to_check(staged):
        if not path.is_file() or path.suffix.lower() in SKIP_SUFFIXES:
            continue
        if path.name == ".private-markers":
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        rel = path.relative_to(ROOT).as_posix()

        for number, line in enumerate(text.splitlines(), 1):
            for pattern in words:
                match = pattern.search(line)
                if match:
                    hits.append(f"{rel}:{number}: private marker {match.group(0)!r}")
            if boxes and rel not in COORD_ALLOWED:
                for lat_text, lon_text in COORD.findall(line):
                    try:
                        lat, lon = float(lat_text), float(lon_text)
                    except ValueError:
                        continue
                    for lat_min, lat_max, lon_min, lon_max in boxes:
                        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
                            hits.append(f"{rel}:{number}: coordinate {lat},{lon} "
                                        f"is inside a private box")

    if hits:
        print("\nPrivate content found. This commit is refused.\n", file=sys.stderr)
        for hit in sorted(set(hits)):
            print(f"  {hit}", file=sys.stderr)
        print("\nThe fix is almost never to delete the line. It is to move the fact into "
              "your private profile\n(boat.toml, or your own overlay repository) and read "
              "it from there. See docs/PRIVATE-AND-PUBLIC.md.\n", file=sys.stderr)
        return 1

    scope = "staged files" if staged else "the working tree"
    print(f"clean — no private markers in {scope}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
