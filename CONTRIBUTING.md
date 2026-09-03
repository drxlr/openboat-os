# Contributing

Bug reports from real boats are the most valuable thing you can send. The interesting
failures in this project have all come from a sender that died, a network that vanished or a
number that was quietly wrong — none of which happen on a desk.

## Sign your commits off

This project uses the [Developer Certificate of Origin](https://developercertificate.org/).
There is no CLA to sign and no copyright to assign; you keep yours.

```bash
git commit -s -m "..."
```

That adds a `Signed-off-by:` line, which is you saying you wrote the change or have the right
to contribute it.

## The rules that are not style preferences

**No boat facts in the code.** Every vessel measurement, position, limit and Signal K path
belongs in a profile, never in a module. A constant that had to be hidden was a constant that
should have been configurable. `scripts/check-private.py` runs as a pre-commit hook and will
tell you; install it with `scripts/install-hooks.sh`.

**A number is either sourced or absent.** Do not add a default length, a default fuel burn or
a default alarm band. If a calculation needs a measurement nobody has, it must refuse and say
so — `Profile.require()` exists for exactly this. A plausible invented figure is the worst
possible output, because nobody can tell it from a real one.

**Degrade, never crash.** The boat is offline most of the time and half its senders do not
exist. `Offline` is an ordinary answer. A missing reading is "no sender", never zero. A
missing sea state is not a calm sea.

**Nothing writes to the boat.** No steering, no switching, no transmitting. The Signal K
client is GET-only and the MCP server exposes no write tool. A pull request that adds a write
path to this repository will be declined — not because control is illegitimate, but because
it belongs in a separate project that people install deliberately. See
[DISCLAIMER.md](DISCLAIMER.md).

**Mark what is untested.** The Arduino sketch has never met a real engine and says so at the
top of the file, not in a footnote. If you add something you could not test on the water, say
that where a reader will see it.

**Stdlib only, Python 3.11+.** The package must run on a Raspberry Pi with nothing installed
and no virtualenv. A dependency has to earn its place against that.

## Tests

```bash
python3 tests/test_regressions.py     # and the other test_*.py beside it
```

They are plain scripts, not a framework, so they run anywhere Python does. If a change breaks
a test, fix the change — do not weaken the assertion. Several of these tests exist because a
silent wrong answer got shipped once, and the assertion is the only thing standing between
that and the next one.

New behaviour needs a test that would have failed before it. For anything involving weather,
use a fixture rather than the live API: a test that depends on today's wind is a test that
fails on a calm day.

## Comments

The bar is that a comment explains **why**, and earns its place. This codebase would rather
have four paragraphs about the one thing that is genuinely counter-intuitive than a line
above every function restating its name. When you fix a bug that produced a plausible wrong
answer, leave the explanation behind — that is the comment someone will need in two years.

## Pull requests

Small ones. One idea each. Say what a reviewer should be sceptical about — that sentence is
worth more than a paragraph of description, and it is the fastest route to a useful review.
