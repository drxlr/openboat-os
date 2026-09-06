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

## The five pages

| | |
|---|---|
| **Overview** | The counts, what is open, and — with the same weight — where each number came from |
| **Documents** | The library as a shelf, and BM25 retrieval over it, with the file and line on every hit |
| **Tasks** | Snags and service in one list, because the boat has one list |
| **Dashboards** | The helm, the jobs page, the weather map and the generated reports, held as apps |
| **Boat** | The measurements, the limits, the papers — and everything not recorded |

Every one of them is built entirely from the API. There is not one boat fact in
`openboat/web/console.html`, which is what lets it live in a public repository at all; the
test that enforces that is in `tests/test_console.py`. Open it against the demo boat and it
is a console for the demo boat.

## Documents

The flagship page, and the one the rest is arranged around.

With the search box empty it is a **shelf**: every document the profile names, whether it
was typed by a person or extracted from a PDF, how many retrievable passages it holds, and
whether any of its pages are scanned images that nothing can answer from.

A path named in the profile that is not on disk appears in that list marked **named but not
on disk**. That is the single most useful row on the page. Dropping it would make the shelf
look complete and quietly stop answering from a manual somebody believes is loaded — and a
library that hides its own gaps is the confident silence this whole project is built to
refuse.

With something typed in it is **retrieval**: the same BM25 search the MCP companion uses,
over the same passages, ranked the same way. Every hit carries the document, the section
heading and the line, and picking one opens the passage beside the whole file scrolled to
that line. A manual quoted without a page is a manual you have to trust; with a page it is
one you can check, and checking it is one click.

Three details are there for a 150-page manual rather than for a note:

- **The paper is one click away.** `/paper?name=…` serves the PDF a derived document was
  extracted from — the document of record, next to the machine's reading of it.
- **A big file opens as a window** around the line being cited, with the rest behind a
  button. A citation points at a line; everything above it can wait until somebody asks.
- **Most lines never touch the HTML parser.** Parsing seven thousand fragments one at a
  time is what made an operator's manual take the best part of a minute to open.

## Tasks

The same union as [the jobs page](JOBS.md), laid out for a desk. Snags parsed back out of
the boat's own `SNAGS.md`, service due out of the engine log, in one list with one filter.

A snag opens with what was written, the photographs, and any follow-ups nested under the
fault they belong to — one fault, one item, however many times somebody added to it. The
photographs come from the snag service on its own port; if that service is not running the
console says so in the frame rather than showing a broken image, because a missing picture
that looks like a missing picture is honest and one that looks like no picture was ever
taken is not.

## Nothing on this page writes

The dashboard server accepts exactly one POST, on one exact path, and the console does not
use it. Every route the console reads is a GET, and `tests/test_console.py` checks that the
page contains no POST at all.

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
