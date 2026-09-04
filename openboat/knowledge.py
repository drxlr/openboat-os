#!/usr/bin/env python3
"""The boat's own documents, made answerable.

    python3 -m openboat.knowledge "which cap do I unscrew to flush"

Every boat accumulates a pile of knowledge that lives nowhere useful: the survey, the
manual, the invoice from the yard that says what they actually replaced, the long note
somebody wrote the evening they finally worked out why the port side ran hot. It is all
true, all specific to this hull, and none of it is reachable from the pontoon at seven in
the morning with wet hands.

This module makes those documents searchable from the same place as the live readings.
Three rules shape it, and each one costs something:

**It is retrieval, not generation.** Answers are passages out of your files, quoted, with
the file and line they came from. Nothing here writes new sentences about your boat. The
project's rule is that a number is either sourced or absent, and a fluent paragraph that
invents a torque figure is exactly the failure this is meant to avoid.

**It works with no internet.** No embeddings, no API, no model. Ranking is BM25 over words,
which is old, unglamorous and entirely offline. The moment you most need to know which cap
to unscrew is the moment you are least likely to have signal.

**It reads the documents where they already live.** Point the profile at them; they are not
copied, indexed into a database, or uploaded. A file you edit by hand is a file this reads
correctly the next second, and a private document stays where its owner put it.

The retrieval is deliberately literal. It finds the paragraph; you read it. If the passage
is wrong, the fix is to edit the document, and then it is right for the boat, for the crew
and for anything else that reads it — not just for one conversation.
"""

from __future__ import annotations

import math
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

#: Words carrying no signal, in the two languages these documents are actually written in.
#: Kept short on purpose — an aggressive list throws away "no", "not" and "off", which in a
#: maintenance note are the whole meaning.
STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "for", "with", "is", "are",
    "was", "were", "be", "it", "that", "this", "as", "by", "from", "you", "your", "i",
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem", "und",
    "oder", "von", "zu", "im", "am", "auf", "mit", "ist", "sind", "war", "waren", "es",
    "dass", "als", "für", "den", "wird", "werden", "nicht",
}

WORD = re.compile(r"[0-9a-zà-öø-ÿ]+", re.I)

#: Bilingual marine equivalences, applied to the *query* only.
#:
#: A boat's papers are not written in one language. The survey is in one, the yard invoice
#: in another, the owner's own notes switch mid-sentence, and the person asking is standing
#: in the engine bay reaching for whichever word arrives first. Measured on a real boat's
#: files, "blaue Kappe Spülanschluss" scored 18.4 on the right passage and "flush connector
#: blue cap" scored nothing at all — the same question, the same answer, invisible.
#:
#: Word-frequency ranking cannot cross that gap on its own, and a translation model cannot
#: run in a bilge with no signal. So the query is widened instead: ask in either language
#: and both are searched. Entries are terms that actually appear in boat documents, written
#: after diacritics are stripped ("spuelung" and "spulung" both, because both get typed).
#: Add to it freely — a missing pair costs a found answer, a wrong pair costs nothing worse
#: than a weaker hit.
EQUIV: list[set[str]] = [
    {"engine", "motor", "maschine"},
    {"flush", "flushing", "spulung", "spuelung", "spulen", "spuelen", "susswasserspulung"},
    {"cap", "kappe", "deckel", "verschluss"},
    {"hose", "schlauch"},
    {"impeller", "flugelrad"},
    {"manifold", "krummer", "kruemmer", "abgaskrummer"},
    {"riser", "steigrohr", "kniestuck", "elbow"},
    {"exhaust", "auspuff", "abgas"},
    {"coolant", "kuhlwasser", "kuehlwasser", "kuhlmittel"},
    {"temperature", "temperatur", "temp"},
    {"seawater", "raw", "seewasser", "rohwasser", "salzwasser", "saltwater"},
    {"freshwater", "susswasser", "suesswasser"},
    {"pump", "pumpe"},
    {"thermostat", "thermostat"},
    {"oil", "ol", "oel"},
    {"fuel", "kraftstoff", "benzin", "sprit", "petrol", "gasoline"},
    {"tank", "tank"},
    {"battery", "batterie", "akku"},
    {"voltage", "spannung", "volt"},
    {"anchor", "anker"},
    {"rope", "line", "leine", "tau"},
    {"propeller", "prop", "schraube"},
    {"drive", "antrieb", "outdrive", "sterndrive", "z-antrieb"},
    {"rudder", "ruder"},
    {"helm", "steuerstand", "steuer"},
    {"hull", "rumpf"},
    {"berth", "liegeplatz", "hafen", "marina"},
    {"insurance", "versicherung"},
    {"registration", "zulassung", "registrierung"},
    {"survey", "gutachten", "besichtigung"},
    {"invoice", "rechnung"},
    {"service", "wartung", "inspektion"},
    {"repair", "reparatur"},
    {"leak", "leck", "undicht"},
    {"corrosion", "korrosion", "rost", "rust"},
    {"overheat", "overheating", "uberhitzung", "ueberhitzung", "heiss", "hot"},
    {"idle", "leerlauf"},
    {"pressure", "druck"},
    {"hours", "betriebsstunden", "stunden"},
    {"winter", "winterlager", "auswintern", "einwintern"},
    {"trailer", "anhanger", "trailer"},
    {"check", "prufen", "pruefen", "kontrolle", "checken"},
    {"replace", "tauschen", "wechseln", "ersetzen"},
]

#: word -> every word it is equivalent to, built once at import.
_EQUIV_INDEX: dict[str, set[str]] = {}
for _group in EQUIV:
    for _word in _group:
        _EQUIV_INDEX.setdefault(_word, set()).update(_group)


def expand(words: list[str]) -> list[str]:
    """A query widened across the glossary, order kept, duplicates dropped."""
    out: list[str] = []
    for word in words:
        for term in [word] + sorted(_EQUIV_INDEX.get(word, set()) - {word}):
            if term not in out:
                out.append(term)
    return out


def terms(text: str) -> list[str]:
    """Words, case-folded and stripped of diacritics, minus the stop list.

    Diacritics go because these documents mix German and English and nobody is consistent
    about them: `Krümmer` and `Kruemmer` must be the same word, or half the maintenance
    history is unfindable from a phone keyboard.
    """
    flat = unicodedata.normalize("NFKD", text.casefold())
    flat = "".join(c for c in flat if not unicodedata.combining(c))
    return [w for w in WORD.findall(flat) if w not in STOP and len(w) > 1]


@dataclass
class Passage:
    """One section of one document, with enough address to go and read the rest."""

    doc: Path
    heading: str
    line: int
    text: str
    score: float = 0.0

    @property
    def where(self) -> str:
        return f"{self.doc.name}:{self.line}"

    def as_dict(self) -> dict:
        return {"doc": self.doc.name, "path": str(self.doc), "heading": self.heading,
                "line": self.line, "text": self.text, "score": round(self.score, 3),
                "where": self.where}


@dataclass
class Library:
    """The documents a profile points at, split into passages and ranked on demand.

    Read fresh on every search rather than cached. These files are edited by hand, often in
    the middle of the job being asked about, and a stale answer about a boat is worse than a
    slow one. They are markdown on local disk; re-reading them costs milliseconds.
    """

    paths: list[Path] = field(default_factory=list)

    #: A section longer than this is split further. Long enough to keep a numbered procedure
    #: whole — the ten steps of a flush are one answer, not ten — and short enough that a
    #: hit is a paragraph to read rather than a chapter to wade through.
    max_chars: int = 1800

    def passages(self) -> list[Passage]:
        out: list[Passage] = []
        for path in self.paths:
            if not path.exists():
                continue
            out.extend(_split(path))
        return out

    def search(self, query: str, limit: int = 5) -> list[Passage]:
        """The best passages for a query, best first. BM25 over the passage collection."""
        asked = terms(query)
        if not asked:
            return []
        # The query is widened, the documents are not: expanding 183 passages would blur
        # every one of them towards every other. Widening the question is free and only
        # ever adds candidates.
        wanted = expand(asked)
        found = self.passages()
        if not found:
            return []

        bags = [terms(p.heading + " " + p.text) for p in found]
        n = len(found)
        avg = sum(len(b) for b in bags) / n
        seen: dict[str, int] = {}
        for bag in bags:
            for word in set(bag):
                seen[word] = seen.get(word, 0) + 1

        k1, b = 1.5, 0.75
        for passage, bag in zip(found, bags):
            score = 0.0
            length = len(bag) or 1
            for word in wanted:
                count = bag.count(word)
                if not count:
                    continue
                idf = math.log(1 + (n - seen[word] + 0.5) / (seen[word] + 0.5))
                score += idf * (count * (k1 + 1)) / (count + k1 * (1 - b + b * length / avg))
            # A heading match is worth more than a body match: somebody wrote that heading
            # to say what the section is about, which is exactly the question being asked.
            head = terms(passage.heading)
            score *= 1 + 0.35 * sum(1 for w in asked if w in head)
            passage.score = score

        ranked = sorted((p for p in found if p.score > 0), key=lambda p: -p.score)
        return ranked[:limit]


def _split(path: Path) -> list[Passage]:
    """Markdown into passages, cut at headings and then at paragraphs if still too long."""
    lines = path.read_text(errors="ignore").splitlines()
    out: list[Passage] = []
    heading, start, buf = path.stem, 1, []

    def flush(at_heading: str, at_line: int, body: list[str]) -> None:
        text = "\n".join(body).strip()
        if not text:
            return
        if len(text) <= 1800:
            out.append(Passage(path, at_heading, at_line, text))
            return
        # Too long: cut at blank lines, keeping the running line number honest so a
        # citation still lands on the right part of the file.
        chunk, chunk_start, size = [], at_line, 0
        for offset, line in enumerate(body):
            chunk.append(line)
            size += len(line) + 1
            if size >= 1800 and not line.strip():
                out.append(Passage(path, at_heading, chunk_start, "\n".join(chunk).strip()))
                chunk, chunk_start, size = [], at_line + offset + 1, 0
        if "\n".join(chunk).strip():
            out.append(Passage(path, at_heading, chunk_start, "\n".join(chunk).strip()))

    for number, line in enumerate(lines, 1):
        if line.startswith("#"):
            flush(heading, start, buf)
            heading, start, buf = line.lstrip("# ").strip(), number, []
        else:
            buf.append(line)
    flush(heading, start, buf)
    return out


def load(boat=None) -> Library:
    """The library a profile describes. Empty unless somebody named their documents.

    Empty is the correct default for a public package: OpenBoat ships no documents about
    anybody's boat, and a library with nothing in it answers "I have no documents" rather
    than inventing something, which is the whole point.
    """
    from .profile import load as load_profile
    boat = boat or load_profile()
    base = boat.path.parent if boat.path else Path.cwd()
    paths = []
    for raw in boat.knowledge.get("docs", []):
        p = Path(raw).expanduser()
        paths.append(p if p.is_absolute() else (base / p))

    # The companion's own notes file, if it exists, is searched alongside the documents —
    # a note nobody can find again is a note nobody wrote. It is kept separate on disk and
    # marked as unverified in its own header; see `openboat/notes.py` for why the boat's
    # real documents stay read-only.
    from .notes import path_for as notes_path
    notes = notes_path(boat)
    if notes.exists() and notes not in paths:
        paths.append(notes)
    return Library(paths=paths)


if __name__ == "__main__":
    query = " ".join(sys.argv[1:])
    library = load()
    if not library.paths:
        print("No documents. Point [knowledge] docs at them in your profile.")
        raise SystemExit(1)
    if not query:
        print(f"{len(library.passages())} passages across {len(library.paths)} documents:")
        for p in library.paths:
            print(f"  {'ok ' if p.exists() else 'MISSING'} {p}")
        raise SystemExit(0)
    for hit in library.search(query):
        print(f"\n\033[1m{hit.heading}\033[0m  ({hit.where}, score {hit.score:.2f})")
        print(hit.text[:700])
