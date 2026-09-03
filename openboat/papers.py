#!/usr/bin/env python3
"""The boat's papers, and which of them is about to expire.

    python3 -m openboat.papers

Registration, insurance, the radio licence, the VAT proof, the survey, the lifting-jacket
service, the flare expiry. Every one of them is a PDF somewhere and a date nobody is
tracking, and the failure is always the same: it lapses quietly, and you find out from a
marina office, an insurer refusing a claim, or a coastguard boarding.

The knowledge library already searches documents for their *text*. This is the other half —
the ones that matter because of a **date**, whose contents you rarely need to read and whose
expiry you must never miss. They are listed in the profile with the file they live in, and
this sorts them by how close they are to lapsing.

Nothing is uploaded, copied or parsed. A paper is a name, a date and a path to a file on
disk. The point is not to hold your documents; it is to know when they run out.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

#: When a paper starts being a problem rather than a note. Chosen from what the renewals
#: actually take: an insurance quote takes days, a flag-state registration renewal can take
#: weeks, and a survey needs a haul-out booked in advance.
SOON_DAYS = 60
URGENT_DAYS = 21


@dataclass
class Paper:
    name: str
    kind: str = ""
    file: str = ""
    expires: str = ""
    issued: str = ""
    number: str = ""
    note: str = ""

    @property
    def days_left(self) -> int | None:
        if not self.expires:
            return None
        try:
            return (date.fromisoformat(self.expires) - date.today()).days
        except ValueError:
            return None

    @property
    def status(self) -> str:
        """`expired`, `urgent`, `soon`, `ok`, or `undated` — never a guess."""
        left = self.days_left
        if left is None:
            return "undated"
        if left < 0:
            return "expired"
        if left <= URGENT_DAYS:
            return "urgent"
        if left <= SOON_DAYS:
            return "soon"
        return "ok"

    @property
    def path(self) -> Path | None:
        return Path(self.file).expanduser() if self.file else None

    def exists(self, base: Path | None = None) -> bool:
        p = self.path
        if p is None:
            return False
        if not p.is_absolute() and base:
            p = base / p
        return p.exists()

    def as_dict(self, base: Path | None = None) -> dict:
        return {"name": self.name, "kind": self.kind, "number": self.number,
                "issued": self.issued, "expires": self.expires, "note": self.note,
                "days_left": self.days_left, "status": self.status,
                "file": self.file, "on_disk": self.exists(base)}


def load(boat=None) -> list[Paper]:
    """The papers a profile lists, soonest to expire first, undated ones last."""
    from .profile import load as load_profile
    boat = boat or load_profile()
    out = [Paper(**{k: str(v) for k, v in row.items() if k in Paper.__dataclass_fields__})
           for row in getattr(boat, "papers", []) if isinstance(row, dict) and row.get("name")]
    # Undated papers sort last rather than first: a missing date is not an emergency, and
    # putting them at the top would bury the one that actually lapses next.
    return sorted(out, key=lambda p: (p.days_left is None, p.days_left
                                      if p.days_left is not None else 0))


def expiring(within: int = SOON_DAYS, boat=None) -> list[Paper]:
    """Papers already expired or lapsing within `within` days. What a briefing wants."""
    return [p for p in load(boat) if p.days_left is not None and p.days_left <= within]


def base_for(boat=None) -> Path | None:
    from .profile import load as load_profile
    boat = boat or load_profile()
    return boat.path.parent if boat.path else None


if __name__ == "__main__":
    papers = load()
    if not papers:
        print("No papers listed. Add them to your profile:\n\n"
              '  [[papers]]\n  name = "Insurance"\n  kind = "insurance"\n'
              '  expires = "2027-03-31"\n  file = "~/boat/insurance-2026.pdf"\n')
        raise SystemExit(0)

    base = base_for()
    mark = {"expired": "EXPIRED", "urgent": "urgent", "soon": "soon",
            "ok": "", "undated": "no date"}
    for paper in papers:
        left = paper.days_left
        when = (f"{paper.expires}  ({left} days)" if left is not None and left >= 0
                else f"{paper.expires}  ({-left} days ago)" if left is not None
                else "—")
        flag = mark[paper.status]
        missing = "" if not paper.file or paper.exists(base) else "   [file not found]"
        print(f"  {paper.name:<28} {when:<26} {flag:<8}{missing}")

    soon = expiring()
    print(f"\n{len(soon)} of {len(papers)} need attention within {SOON_DAYS} days."
          if soon else f"\nNothing lapses within {SOON_DAYS} days.")
