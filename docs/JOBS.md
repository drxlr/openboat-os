# The jobs page

Two lists on a boat are really one list. The engine is owed a flush because it ran in salt
water yesterday; the locker by the heads is owed a striker plate because somebody noticed it
didn't shut. The only difference between them is who spoke first — the boat, through its own
running hours, or a person, standing in front of the fault with a phone. Kept apart they are
two things nobody looks at. Kept together they are the answer to the question actually being
asked in the cockpit on a Saturday morning: *what does this boat owe me, and what is broken?*

That is the whole of `openboat/web/jobs.html`.

```bash
python3 -m openboat.server        # → http://localhost:8747/jobs.html
```

It reads two routes and holds no state of its own:

| Route | Comes from | Says |
|---|---|---|
| `GET /api/maintenance` | `openboat/maintenance.py` | what service is due, counted in running hours and salt-water outings |
| `GET /api/snags` | `openboat/snag.py` | what somebody noticed and wrote down |

Both are read-only. So is the page.

## Nothing on this page writes to the boat

The dashboard server is read-only by construction and accepts exactly one POST, on one exact
path. That is not an implementation detail that happened to be convenient; it is the reason a
reviewer can answer "what can write to this boat's files" by reading two small files. A page
that lists faults and services is precisely the page that wants to grow a *Mark done* button,
and precisely the page where the read-only boundary would be lost one convenient addition at
a time.

So work leaves this page by three different doors, and which door depends on how deliberate
the act ought to be.

**"I checked it and it's fine"** posts to `/api/logbook` — the one write the dashboard has.
That is a notebook entry: somebody looked at the anodes and reports what they saw. It records
an observation, changes no service record, and is the right weight for something done while
standing up.

**"I did the service"** does not have a button. The page shows you the command:

```bash
python3 -m openboat.maintenance --did impeller --note "Ancor, 06-2026"
```

Recording a service resets the clock on an interval, and everything downstream — the next
due date, the engine's cooling trend, the season report — is computed from it. A tap on a wet
tablet is not the correct amount of intention for that. A command typed at a desk is.

**Closing a snag** is a line changed by hand in `SNAGS.md`, for the reason set out in
[SNAGS.md](SNAGS.md): the file is the truth, and a person editing it is the act of closing.
The page shows the path and the line to change. It does not offer to do it.

**Dragging arranges your own tiles, and only that.** SortableJS is on the page so the order
of what you see is yours, remembered in the browser's local storage, in the same spirit as
the arrangeable helm tiles. Dragging a card never changes anything about the boat. If that
ever stops being true, this paragraph is the thing that was broken.

## It shows you what it does not know

An item with no interval set is displayed, with its running total, and with no verdict
attached. `openboat/maintenance.py` ships no intervals at all — "every 100 hours" is true of
some engines and wrong for others, and a schedule that is confidently wrong is worse than
none, because it gets followed. The page inherits that refusal rather than papering over it
with a plausible default. `unknown` is a state you can see, and the fix for it is your engine's
own manual, in your profile's `[maintenance]` table.

Snags carry the same honesty in the other direction. Every entry is marked unverified —
recorded from a phone at the moment of noticing, confirmed by nobody since — and the page
keeps that mark visible rather than promoting somebody's hurried note to a fact.

## Built for the pontoon, not the desk

The page loads Alpine.js and SortableJS from `openboat/web/vendor/`, on disk, along with
Leaflet. Nothing is fetched from a CDN. A boat dashboard that needs `unpkg.com` is a
dashboard that goes blank at the end of a pontoon, which is exactly where somebody is
standing when they want to know whether the impeller is overdue.

Those two libraries are the whole of the dependency budget, they are vendored rather than
installed, and there is no build step — the same install as the rest of the project:
clone it and run Python. See [`openboat/web/vendor/README.md`](../openboat/web/vendor/README.md)
for versions, licences and how to update them.

It inherits the helm dashboard's three palettes — day, dusk and night, following the sun —
and its tap targets are sized for a wet hand on a tablet rather than a mouse.
