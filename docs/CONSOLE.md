# The console

```bash
python3 -m openboat.server        # → http://localhost:8747/console.html
```

`index.html` is the helm. It is built to be read in sunlight, at arm's length, with wet
hands: big numerals, three palettes, tap targets sized for gloves. It answers the questions
you have while the boat is moving.

The console answers the other ones. *What does the manual say about the impeller. What is
this boat owed. What did somebody photograph last week. Which of these measurements is a
guess.* Those are asked sitting down, at a keyboard, usually in a bad mood in February, and
they want density rather than legibility at two metres — a table you can scan, a citation
you can click, and the file open beside it.

So it is a different page rather than a mode of the same one, and it is deliberately not
pretty in the way the helm is. It is monospace throughout, which is a decision and not a
style: nearly everything on it is a quantity, a file name, a line number or a timestamp, and
a proportional font makes a column of those harder to compare. Tabular figures mean 9.8 and
10.1 take the same width, so a column of hours is scanned instead of read.

It is built as a shell and five views rather than one file: `console.html` holds the chrome,
the router and the keyboard, and each page is its own script under `console/`. A view is
loaded, not guessed at; the no-boat-facts test reads every one of those scripts along with
the shell, so a fact typed into a view is caught exactly as if it had been typed into the
shell itself.

## The five pages

| | |
|---|---|
| **Overview** | The counts, what is open, what is live right now, and — with the same weight — where each number came from |
| **Documents** | The library as a shelf, and BM25 retrieval over it, with the file and line on every hit |
| **Tasks** | Snags and service in one list, because the boat has one list |
| **Dashboards** | The helm, the jobs page, the weather map and the generated reports, held as apps behind their own address |
| **Boat** | The measurements, the limits, the live Signal K paths, the papers — and everything not recorded |

Every one of them is built entirely from the API. There is not one boat fact in
`openboat/web/console.html` or in any of the scripts beside it under `console/`, which is
what lets the console live in a public repository at all; the test that enforces that is in
`tests/test_console.py` and reads the shell and every view together. Open it against the demo
boat and it is a console for the demo boat.

## Every page has an address

Nothing on this console is reached only by clicking. The hash is the page: `#docs` is the
shelf, `#docs?q=impeller` is a search, `#docs/pump-7j.md` is a document and
`#docs/pump-7j.md/L212` is a line inside it, held and lit. Tasks works the same way —
`#tasks`, `#tasks?f=open` narrowed to a filter that still has a URL of its own,
`#tasks/snag:<when>` and `#tasks/service:<item>` for one fault or one service item whole. A
staged dashboard is `#boards/<id>`.

Back is the browser's own back button, not a control this page invents: the router turns
the hash into a page, and every navigation writes the hash first, so returning to where you
were is exactly what a browser already knows how to do. A detail page — a document, a snag,
a service item, a staged board — replaces the list it came from rather than squeezing in
underneath it, and opens with a breadcrumb back to that list, because a fault's note and its
photographs and its follow-ups do not fit in half a pane.

## Documents

The flagship page, and the one the rest is arranged around.

With the search box empty it is a **shelf**: every document the profile names, whether it
was typed by a person or extracted from a PDF, how many retrievable passages it holds, and
whether any of its pages are scanned images that nothing can answer from. Typed-by-hand
comes before extracted, and inside each group a document not on disk comes first — that is
the single most useful row on the page. Dropping it would make the shelf look complete and
quietly stop answering from a manual somebody believes is loaded, and a library that hides
its own gaps is the confident silence this whole project is built to refuse.

With something typed in it is **retrieval**: the same BM25 search the MCP companion uses,
over the same passages, ranked the same way, with the matching terms marked in the passage
so you can see why a hit ranked where it did. Every hit carries the document, the section
heading and the line, and picking one opens the passage beside the whole file scrolled to
that line. A manual quoted without a page is a manual you have to trust; with a page it is
one you can check, and checking it is one click.

The reader itself is built for a 150-page manual rather than for a note. A document a person
typed is rendered as markdown by a small renderer with no library behind it — everything is
escaped before anything is turned into a tag, because a manual containing a `<script>` is a
manual, not an instruction — with a nested outline of its own headings in the rail beside it.
An extracted document is shown a page at a time, each one built lazily as it scrolls into
view rather than all at once, because parsing seven thousand fragments up front is what made
an operator's manual take the best part of a minute to open; a page with no text layer says
so on its own card instead of showing blank. Sticky headers keep you oriented in either
shape, and every page has an *open in the PDF* link beside it. The whole document's own PDF
can sit next to the reading, synced to the page as you scroll — a preference remembered in
the browser rather than sent anywhere, and a fresh tab rather than a split pane on a phone,
where there is no room for two columns. Find-in-document steps with `j`/`k`, `[` and `]`,
`/` and `Esc`, and jumping to `/L212` scrolls to that line and holds it lit whichever shape
the document is in.

## Tasks

The same union as [the jobs page](JOBS.md), laid out for a desk. Snags parsed back out of
the boat's own `SNAGS.md`, service due out of the engine log, in one list with filter chips
that carry their own counts and their own address, so a filtered list survives a reload.

A snag opens with the note as it was written — blank lines as paragraphs, a mention of a
document turned into a link when the boat actually has that document — the photographs in a
gallery with a lightbox over them, and any follow-ups laid out as a timeline under the fault
they belong to: one fault, one item, however many times somebody added to it. A section
headed *In the papers* asks the same retrieval the Documents page uses, built from the words
of the fault itself, so a fault about the raw-water pump surfaces the pump's own manual
without anybody typing a second query. The photographs come from the snag service on its own
port; if that service is not running the console says so in the frame rather than showing a
broken image, because a missing picture that looks like a missing picture is honest and one
that looks like no picture was ever taken is not. The footer says exactly how a snag is
closed — a line changed by hand in `SNAGS.md` — rather than pretending a button could do it.

A service item opens with every counter behind its verdict: hours since, days since, outings
since, the interval it is measured against, each one an em dash rather than a zero when it is
not known, and the same running-hours provenance line the maintenance API itself writes,
quoted rather than paraphrased. The command that actually records a service is printed
underneath it, because typing that command is the only way the clock resets.

## The shell around them

`/` reaches the search box on whichever page you are on, `?` opens a card of what the keys
do, and `g` then a letter — `o d t s b` — jumps straight to a view. ⌘K (or Ctrl-K) opens one
search over everything: the same BM25 over the papers plus the snags and service items
filtered in memory, in one list of results that opens exactly where the address says. None
of this fires while you are typing into a box, and Esc always closes whatever is open before
it does anything else.

Under about 720 pixels the rail becomes a bottom dock, the same shape as the helm page's for
the same reason — a thumb reaches the bottom of a phone and not the top-left corner of it —
and a table sheds its lower-priority columns rather than scrolling sideways. A request that
fails is a sentence in the page's own furniture, not a blank panel: loading, empty and error
states are three different things and none of them is a zero standing in for "nobody could
ask."

## Overview and Boat gain a live pane

Overview now asks `/api/state` for what Signal K is saying right now — speed, heading,
depth, wind, engine, battery — and shows it as its own card, aged rather than timestamped
raw, only when the boat is actually on the network; a boat ashore with nothing arriving gets
a sentence saying so, not a card full of dashes. Boat lists every path actually arriving over
Signal K, filterable, with its value, its unit, its source and how old it is — the difference
between "this boat has a depth sounder" and "the depth sounder said something ten seconds
ago" — alongside the alarm bands the helm turns a reading amber or red with, and the boat's
own papers as a table with days left column, so a lapsed registration reads the same as a
lapsed insurance renewal.

## Nothing on this page writes

The dashboard server accepts exactly one POST, on one exact path, and the console does not
use it. Every route the console reads is a GET, and `tests/test_console.py` checks that the
page and every one of its views together contain no POST at all.

That is not fastidiousness. A page listing faults and services is precisely the page that
wants to grow a *Mark done* button, and precisely the page where the read-only boundary
would be lost one convenient addition at a time. Work leaves it the way it leaves the jobs
page: a snag is closed by editing the file, a service is recorded by somebody at a keyboard
who knows the work actually happened, and a fault is filed from the phone page — which is a
separate service, on a separate port, and the only thing in the project that writes.

## Reading the disk

`/api/doc` and `/paper` both turn a query parameter into a file read, and this server binds
every interface. Both resolve **only** against `library.paths` — the documents the profile
already named — and `/paper` will only follow a source filename recorded in a document's own
header as far as its basename. A `../../` in a header is a file on disk that somebody else
may have written; it must not become a file the server hands out.

The tests for that are the ones worth keeping: `test_find_resolves_only_what_the_profile_named`
and `test_original_for_finds_the_paper_and_refuses_a_traversal`.

## The routes it reads

| Route | Says |
|---|---|
| `GET /api/profile` | the vessel, the limits, the unsourced measurements, the version |
| `GET /api/docs` | the library as a shelf: kind, source, passages, pages, gaps, missing |
| `GET /api/doc?name=` | one named document, whole |
| `GET /paper?name=` | the PDF a derived document came out of |
| `GET /api/ask?q=` | BM25 over the passages, with file and line |
| `GET /api/snags` | what somebody wrote down, with the key and port to fetch photographs |
| `GET /api/maintenance` | what the engine is owed, and where the hours came from |
| `GET /api/papers` | the ship's documents and how many are expiring |
| `GET /api/state` | Signal K's live readings, for the Overview and Boat cards |
| `GET /api/paths` | every live path arriving over Signal K, with its age |
