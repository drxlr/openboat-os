"""Arm these tests when pytest is the one running them.

Every test file here is also a plain script — `python3 tests/test_snag.py` — and that is the
runner the README names. The idiom follows from it: `check()` records into a module-level
list and prints a tick or a cross, no exception is raised, and the `__main__` block at the
bottom counts the crosses at the end and exits non-zero. Run that way, a false check fails
the run, loudly, with every other check still reported rather than the run stopping at the
first one. That is a good design for a suite somebody reads the output of.

Under pytest the `__main__` block never executes. The test function appends `(False, …)` to
the list, returns normally, and pytest — which only fails on an exception — calls it a pass.
So the whole suite reported green whatever the checks actually found, and `pytest -q` was
the command everyone reached for.

This closes that gap without touching a single test: after the last test in a module, read
whatever the module recorded and fail it here if anything is false. Both conventions in the
tree are covered — `results`, holding `(ok, description)` pairs, and `failures`, holding
already-formatted strings. A module using neither is left alone.

The plain-script path is unchanged. This only teaches pytest what the scripts already knew.
"""

import pytest

#: The two module-level names the test files use to record a failed check.
RESULTS = "results"      # list[tuple[bool, str]] — every check, passed or not
FAILURES = "failures"    # list[str] — only the failures, pre-formatted


def _recorded_failures(module) -> list[str]:
    found = []
    for ok, what in getattr(module, RESULTS, None) or []:
        if not ok:
            found.append(what)
    found += list(getattr(module, FAILURES, None) or [])
    return found


@pytest.fixture(autouse=True, scope="module")
def check_failures_fail_the_module(request):
    """Fail a module whose recorded checks did not all pass.

    Module-scoped, so it reports once with the complete list rather than attributing the
    tally to whichever test happened to run last — the lists accumulate across a module and
    are not reset between tests.
    """
    yield
    failed = _recorded_failures(request.module)
    if failed:
        pytest.fail(
            f"{len(failed)} recorded check(s) failed in {request.module.__name__} — these "
            "are check() calls, which do not raise, so pytest would otherwise report this "
            "module as passing:\n  - " + "\n  - ".join(str(f) for f in failed),
            pytrace=False)
