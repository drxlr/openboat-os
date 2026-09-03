# Working on your own boat and on a public project at the same time

This project was extracted from one person's private boat repository, and the extraction is
the interesting part. If you are doing the same thing — running this on your own vessel while
contributing to the public code — here is the arrangement that works, and the reason each
piece of it exists.

## The rule

> **The public repository holds code and never a fact. The private one holds facts and never
> a fix.**

Everything else follows from that sentence. A bug fix belongs upstream where other people
get it. Your boat's length, your berth, your engine's alarm thresholds and your Signal K
password belong to you, and they belong in a file that is never pushed anywhere public.

The reason to be strict about the direction, and not just about the content, is that the
failure is asymmetric. A fix stuck in a private repo is a small waste. A berth coordinate
pushed to a public one is permanent: it is cloned, cached, mirrored and indexed within
minutes, and deleting the commit does not delete those copies.

## The shape

```
~/code/
├── openboat-os/          public   — the code. Cloned by strangers. No boat facts.
│   ├── openboat/                    the package
│   ├── profiles/demo-boat.toml      a fictional boat at a public harbour
│   └── scripts/check-private.py     refuses a commit carrying a private fact
│
└── my-boat/              private  — your overlay. Never pushed anywhere public.
    ├── boat.toml                    your vessel, your berth, your limits, your paths
    ├── signalk/security.json        your Signal K users and tokens
    ├── logbook/                     your tracks, your engine history
    └── notes/                       surveys, invoices, the mechanic's opinion
```

Two directories, two remotes. The private one runs the public code:

```bash
export OPENBOAT_PROFILE=~/code/my-boat/boat.toml
python3 -m openboat.server
```

That single environment variable is the whole coupling. No fork, no patches to carry, no
merge conflicts when you pull. You are a plain user of the public package who happens to
also commit to it.

If you would rather have one directory, put the public repo inside the private one as a git
submodule. It works, and it survives `git status` showing two repositories, but the
environment-variable version is harder to get wrong.

## Why a profile and not a config fork

The tempting shortcut is to fork the public repo, edit the constants, and keep your fork
private. It fails within a month. Every upstream change becomes a merge, every merge touches
the lines you changed, and eventually one merge silently reverts your boat's alarm bands or
carries your berth into a pull request. The profile exists so that the file you edit and the
file you contribute are never the same file.

The same reasoning is why `openboat/profile.py` refuses to compute rather than fall back to a
default measurement. A default length or a default fuel burn is a fact about *some* boat, and
the moment it appears in a calculation nobody can tell whether the answer describes your boat
or the demo one.

## The guard

Discipline is not a mechanism, so there is a mechanism:

```bash
scripts/install-hooks.sh          # once
python3 scripts/check-private.py  # any time
```

It reads `.private-markers` — which is itself gitignored, because your list of private words
is private — and fails any commit that contains one. Two kinds of entry:

```
# a word, as a regex, case-insensitive
MyBoatName
TheMarinaName
/Users/[a-z0-9_.-]+

# a box of coordinates that must never appear: lat_min lat_max lon_min lon_max
BOX 50.1 50.5 -4.4 -3.9
```

The coordinate box is the part people skip and then regret. A berth position is personal data
of a kind that does not feel like personal data: it says where a valuable object sits
unattended and when nobody is aboard. It also leaks in places a word search will not catch —
a test fixture, a sample GPX track, a screenshot's EXIF, a cached forecast filename.

When the hook fires, the fix is almost never to delete the line. It is to move the fact into
your profile and read it from there. That is usually a two-line change and it makes the code
better, because a constant that had to be hidden was a constant that should have been
configurable.

## What the guard does not do

It is not a secret scanner. It looks for *facts*, which is the thing no secret scanner is
looking for: an API key has a recognisable shape and a boat's name does not. Run a real
secret scanner as well.

It also only knows what you tell it. Add a word the day your overlay gains one — a new
mooring, a crew member, a machine on your network — not the day you notice it in a diff.

## Contributing a fix you found on your own boat

1. Reproduce it against the demo profile. If it only reproduces with your boat's numbers,
   that is worth saying in the issue, and usually means the bug is about a value being
   absent rather than about its size.
2. Write the test with demo or invented values.
3. Commit in the public repo. The hook checks you.
4. Pull it back into your boat by pulling the package. There is nothing to merge.

## If something private is already public

Assume it is permanent. Rotate anything rotatable — Signal K users, tokens, passwords — the
same hour. For a position or a name, rewriting history helps only if you are fast and the
repository is unpopular; GitHub keeps unreachable objects reachable for a while, and forks
and caches keep their own copies. Do it anyway, then treat the fact as public and plan
accordingly. This is why the hook exists: it is much cheaper than the alternative.
