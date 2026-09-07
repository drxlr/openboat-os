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
does not edit an existing entry or reorder anything — there is no code path that would let
it. A fault's status still changes, in one of two ways, and both leave the history intact.

By hand, at a desk, the line

```
**Status:** open
```
becomes
```
**Status:** fixed — replaced the striker plate, 2026-09-12
```

Or from the console's task page, as a **follow-up** that carries a status. A fault moves
through three words: `open` is what the phone files; `review` says somebody has an idea and
it wants looking at before anybody picks up a tool; `fixed` closes it. The follow-up is
appended like any other entry, with the person's name and a note — closing one without
saying what was done is refused — and the fault's status is the newest thing anybody wrote
about it. A follow-up that says nothing about status carries no `Status` line and changes
nothing. Reopening a fault is done the way it used to be closed: by hand, in the file.

Either way it is a deliberate act with a name on it, not a tap on a phone in a wet pocket,
and it is why `read_snags()` — the function that turns the markdown back into a list for the
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

## Handing one to somebody

A fault on a list is nobody's. From the console's task page, **Assign** writes a name
against it — appended, like everything else, as a follow-up carrying one line:

```
**Assigned:** Jo at the yard
```

The newest name anybody wrote is the one it is on. A follow-up that says nothing about it
changes nothing, and `-` hands it back to nobody. The name is who is *doing* it, not a
notification: nothing here emails anybody, and nothing checks that the name belongs to a
person who exists. The console remembers the names typed on this browser so the same three
or four come round as suggestions.

## Sharing a fault

The person with the spanner usually has no account and should not need one. From the same
page, **Share with the person fixing it** mints a link to *one fault*:

```
https://openboat.example.vercel.app/s/eyJib2F0Ijo…
```

Whoever opens it sees that fault — the note, the photographs, the follow-ups, who it is
with — and gets one form back: *I have looked at it* or *It is fixed*, with a note and a
name. That is the whole surface. No other fault, no other boat, no document, no position,
and no second kind of write.

The link is minted by the gate, because the gate is the only component here that has a
login and therefore a role to check: `owner` and `admin` may hand one out and `crew` gets a
404, the same 404 they get for a boat that is not theirs. It is good for a number of days
you choose, up to ninety.

**The grant is written into the boat's own file**, as a follow-up on the fault:

```
**Share:** 3f8a91c2 2026-10-07T18:00:00+03:00 Jo at the yard
```

and taking it back is one more line:

```
**Unshare:** 3f8a91c2
```

That is the whole revocation mechanism, and it is deliberate. The token itself is stateless
— a signed statement of boat, fault, expiry and id — so checking one needs no table. But a
link you cannot take back is not a link anybody should hand out, so the id has to be listed
on the fault, un-revoked, or the link opens nothing however good its signature is. It also
means *who was given a way in* is a thing a person can read, in the file the fault is in,
in the same place they read everything else about it. The console lists every link ever
made for a fault, live or taken back, with a Revoke button beside the live ones.

Somebody holding a link can append follow-ups and nothing else, thirty times a day at most.
Every one of them is filed as `Jo (via share link 3f8a91c2)` — what they said their name
was, and which link they said it through, because a name typed into a form is a claim.

The whole of it is `openboat/share.py`, and it hangs off the gate through the documented
`MOUNTS` seam rather than by editing the gate's router. It reads and appends to the boat's
own files in the gate's process, so the gate has to be started with `$OPENBOAT_BOATS` set
the way the snag service is — without it every share route is an honest 404. See
[docs/GATE.md](GATE.md).

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

## The other two things this service writes

It started as one write route and it is now three families, and they are here together on
purpose: this is the process a reviewer reads when they want to know what can change a
boat's files.

`POST /api/snag` files a fault or a follow-up, which is everything above. The other two are
the inbox — `POST /api/intake/accept` and `POST /api/intake/reject`, with
`GET /api/intake` and `GET /api/intake/file` to read it. An assistant reaching the boat over
MCP can put a link or a PDF into `intake/`; nothing in there is searched or answered from,
and accepting one is the step that moves it into the boat's library. Both POSTs require a
name and refuse without one, a rejection requires a reason, and a PDF flagged as carrying
active content is never served for download. The whole design, and why the person's name is
the point of it, is in [docs/INTAKE.md](INTAKE.md).

Accepting a document is the most consequential write in this project — it is the moment
something an assistant found on the internet becomes one of the boat's own papers — so it
belongs on the port a reviewer already has to read, not bolted onto the read-only dashboard.

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
