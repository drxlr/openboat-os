# The snag list

A fault is noticed at a bad moment — halfway through something else, hands full, in the
rain. "The locker by the heads doesn't shut properly" gets said once and then, because
nobody wrote it down where it lived, gets rediscovered in March as a surprise. The snag
list exists to close that gap where it opens: on a phone, standing where the fault is,
in about ten seconds.

```bash
OPENBOAT_PROFILE=boat.toml python3 -m openboat.snag        # → http://<this machine>:8752
```

Open that address on any phone on the same network. Pick the boat, photograph the thing,
say what is wrong, send. It appends one entry to that boat's `SNAGS.md`, next to the
`boat.toml` the profile points at.

## Append-only, on purpose

`record()` in `openboat/snag.py` only ever adds a new heading to the end of the file. It
does not edit an existing entry, mark one fixed, or reorder anything — there is no code
path that would let it. Closing a snag is a line changed by hand:

```
**Status:** open
```
becomes
```
**Status:** fixed — replaced the striker plate, 2026-09-12
```

That is a deliberate act by a person at a desk, not a tap on a phone in a wet pocket, and
it is why `read_snags()` — the function that turns the markdown back into a list for the
page — is a *parser over the file* and not a database sitting beside it. If it kept its
own copy, a snag you had just fixed and written down would still show as open, which is
the one failure mode a fault list cannot have: a maintenance record that lies is worse
than none. Anything the parser can't make sense of is skipped rather than guessed at, and
an entry with no `Status` line at all is reported open, on the reasoning that something
somebody wrote and never marked is not something anybody has closed.

Every entry also carries a standing warning that it is unverified — recorded from a phone
at the moment of noticing, confirmed by nobody since. That is the correct epistemic status
for "the owner said so", and it is also exactly the kind of fact the boat's own library
(`openboat.knowledge`) is built to hold: specific, dated, and honest about how sure it is.

## Why this is a separate service

`openboat.server` — the dashboard — is read-only about the boat by construction, and holds
no route into `openboat/control/` at all. It accepts exactly one POST, on one exact path:
`/api/logbook`, which appends a line to the maintenance check log — a notebook entry, not a
control, and kept deliberately separate from anything that moves the boat. Bolting a second
write route onto it, even a small and clearly-scoped one like "log a fault", is exactly how
a read-only thing stops being read-only by accident, one convenient addition at a time.

So the snag list runs as its own process on its own port (`8752` by default), with its
own single write route (`POST /api/snag`) and nothing else that touches disk. It can be
started, stopped, or left off entirely without anything about the dashboard changing, and
a reviewer auditing "what can write to this boat's files" only has one small file to read
for this path — `openboat/snag.py` — rather than a dashboard grown a second job.

## Multi-boat setups

One instance can serve more than one boat. Point it at a directory holding one folder per
boat, each with its own `boat.toml`:

```bash
OPENBOAT_BOATS=~/boats python3 -m openboat.snag
```

```
~/boats/
├── plymouth-boat/boat.toml
└── the-other-one/boat.toml
```

`boats()` globs `*/boat.toml` under `$OPENBOAT_BOATS` and offers each one as a choice on
the page, keyed by its folder name. Snags and their photographs land beside that boat's
own profile — `SNAGS.md` and `photos/snags/` next to each `boat.toml` — so two boats never
share a file by accident. With no `$OPENBOAT_BOATS` set, it falls back to the single boat
in `$OPENBOAT_PROFILE` (or the demo boat, same as everything else in this project), so a
one-boat install needs no extra configuration at all.

## Who filed it

By default nobody has to say who they are — the page just asks "who are you" and remembers
the answer in the phone's own local storage, a courtesy rather than a claim. Set
`OPENBOAT_SNAG_PEOPLE` to name the people who may use the page and to know which of them is
filing:

```bash
OPENBOAT_SNAG_PEOPLE="skipper:6d9f2a1c4b7e,alex:0f3a9c2b5d1e" python3 -m openboat.snag
```

Each person gets a link with their own key on it — `http://…:8752/?k=6d9f2a1c4b7e` — and
that link *is* the credential: every route, including the page itself, requires a valid
`k`. Holding the link is holding the identity, so attribution costs the person nothing and
can't be typed in wrong by someone in a hurry, and a name never has to be trusted from a
text field again. Six weeks later, "is this still a problem?" has someone to ask, because
the entry says who saw it.

With `OPENBOAT_SNAG_PEOPLE` unset, the page is open to whoever is on the network — right
for a boat's own LAN, wrong for anything reachable further than that. `main()` says which
mode it is in, out loud, at startup, so it is never ambiguous which one you launched.

## Photos

Resizing to 1600 px on the long edge happens in the browser, before anything is sent. A
phone photograph straight off the camera is several megabytes and a boat's wifi is often
poor; 1600 px is comfortably enough to see a cracked fitting or a corroded terminal, and
it is the difference between a page that works at the end of a pontoon and one that spins
forever on an upload. Doing the resize client-side, in JavaScript already in the browser,
is also why the server stays stdlib-only — no imaging library gets carried for it.

## Security posture

**It binds every interface (`0.0.0.0`), deliberately.** The entire point is that a phone
walking around the boat can reach it, and a phone is never on `localhost`. That also means
anything else on the same network can reach it — which is a description of a home or
marina LAN, not a threat model to defend against on one.

**With no `OPENBOAT_SNAG_PEOPLE` configured, it holds no credentials at all**, and grants
whoever is on the network the ability to read and file snags — read here means the fault
list and the photographs, nothing about position, papers or the boat's live state, none of
which this service touches. Configuring `OPENBOAT_SNAG_PEOPLE` turns that into a set of
opaque per-person keys instead, which is what you want the moment this stops being purely
LAN-local — a tunnel, a marina guest network, a boat shared with someone who isn't always
aboard.

**It belongs on a home or boat LAN, not café wifi**, with or without people configured. A
key in `OPENBOAT_SNAG_PEOPLE` raises the bar from "anyone on this network" to "anyone with
a link", which is worth having the moment the network is one you don't fully trust, but it
is still a bearer token over plain HTTP on a LAN — good enough for a boat's own network,
not a substitute for a real tunnel (Tailscale, a Cloudflare Tunnel) if you expose it
further than that, the same reasoning the MCP server in `docs/COMPANION.md` uses for its
own token.
