"""Change the skipper's weather limits, without losing the file they live in.

The limits are the one part of a profile that is a *preference* rather than a measurement.
Everything else in `boat.toml` is a fact about the boat that somebody sourced; these are
where this skipper turns back, and they legitimately change — with a new crew, a new sea
area, a guest who does not enjoy it. So they are the one part that is edited from the app
rather than at a keyboard.

    python3 -m openboat.limits                       # what they are, and who set them
    python3 -m openboat.limits --wind 25 --gust 32 --wave 1.5 --by "the skipper"

## Why this is not a TOML round-trip

`tomllib` reads and does not write, and every library that writes TOML throws the comments
away. In this project that would be the worst possible trade: `boat.toml` is mostly
comments, and they carry the sourcing — which figure came from a class specification and
not from this hull, why `_TEMPLATE.md` is deliberately outside the corpus, that the limits
in front of you were copied from another boat and are wrong. Losing those to change one
number would destroy more than it wrote.

So this edits the text. It finds the `[limits]` block, replaces the values on their own
lines, leaves every other byte alone, and writes through a temporary file so an interrupted
save cannot leave a boat with half a profile.

## The provenance line is the point

Setting a limit also records who set it and when, as `source` inside the block. A profile
copied from another boat reads exactly like a considered one until something distinguishes
them, and "can we go out on Saturday" is computed off these numbers. `source` empty means
nobody has chosen them, and every screen that shows them is expected to say so.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date
from pathlib import Path

from .profile import Limits, load as load_profile

#: What each field is allowed to be. Not the boat's capability — a sanity range, so a
#: fat-fingered "250" knots cannot quietly become a limit that passes everything.
BOUNDS: dict[str, tuple[float, float]] = {
    "max_wind_kn": (0.0, 60.0),
    "max_gust_kn": (0.0, 80.0),
    "max_wave_m": (0.0, 10.0),
    "max_rain_mm": (0.0, 50.0),
    "daylight_from_h": (0, 23),
    "daylight_to_h": (0, 23),
}

INTEGERS = {"daylight_from_h", "daylight_to_h"}


class LimitsError(ValueError):
    """Something about the request was wrong. Reported as a sentence, never a traceback."""


def clean(values: dict) -> dict:
    """Validate a set of proposed limits, or refuse with a sentence saying which one.

    Refuses rather than clamps. A number silently moved to the nearest allowed one is a
    limit the owner did not set being presented back as the limit they set.
    """
    out: dict = {}
    for key, raw in (values or {}).items():
        if key not in BOUNDS:
            raise LimitsError(f"{key} is not a limit this profile has.")
        if raw is None or raw == "":
            continue
        try:
            number = int(raw) if key in INTEGERS else float(raw)
        except (TypeError, ValueError):
            raise LimitsError(f"{key} must be a number, not {raw!r}.")
        low, high = BOUNDS[key]
        if not low <= number <= high:
            raise LimitsError(f"{key} must be between {low:g} and {high:g}, not {number:g}.")
        out[key] = number

    gust, wind = out.get("max_gust_kn"), out.get("max_wind_kn")
    if gust is not None and wind is not None and gust < wind:
        # Not pedantry: `windows.py` tests a window against wind *and* gust, so a gust
        # limit below the wind limit makes the wind limit unreachable and every window
        # is silently decided by the gust alone.
        raise LimitsError(
            f"the gust limit ({gust:g} kn) is below the wind limit ({wind:g} kn), which "
            f"would make the wind limit meaningless — every window would be decided by "
            f"the gust.")
    if not out:
        raise LimitsError("nothing to change.")
    return out


def _block(text: str) -> tuple[int, int]:
    """Where `[limits]` starts and ends in the file, as character offsets."""
    m = re.search(r"^\[limits\][^\n]*\n", text, re.M)
    if not m:
        raise LimitsError("this profile has no [limits] block to change.")
    start = m.end()
    nxt = re.search(r"^\[", text[start:], re.M)
    return start, start + (nxt.start() if nxt else len(text[start:]))


def _set(block: str, key: str, rendered: str) -> str:
    """Replace `key = …` inside the block, or append it if the profile never had one.

    Appending keeps whatever blank lines the block ended with, so a key added here does not
    quietly close up the gap before the next section every time somebody saves.
    """
    pattern = re.compile(rf"^(\s*){re.escape(key)}\s*=\s*[^\n#]*", re.M)
    if pattern.search(block):
        return pattern.sub(lambda m: f"{m.group(1)}{key} = {rendered}", block, count=1)
    body = block.rstrip("\n")
    tail = block[len(body):] or "\n"
    return f"{body}\n{key} = {rendered}{tail}"


#: A warning that has stopped being true is worse than no warning: it teaches the reader
#: that the warnings in this file are noise. When somebody sets their own limits, the note
#: saying these were copied from another boat is replaced by what actually happened.
STALE_WARNING = re.compile(
    r"^#[^\n]*UNVERIFIED[^\n]*\n(?:#[^\n]*\n)*", re.M)


def _retire_warning(block: str, who: str, stamp: str) -> str:
    if not STALE_WARNING.search(block):
        return block
    return STALE_WARNING.sub(
        f"# Set by {who} on {stamp} through the app. Before that these were another boat's\n"
        f"# figures, carried over as placeholders — see `source` below, which is what any\n"
        f"# screen showing these numbers reads to say whether anybody chose them.\n"
        f"#\n",
        block, count=1)


def write(values: dict, by: str = "", profile_path: str | Path | None = None,
          when: str = "") -> dict:
    """Write new limits into a profile, preserving everything else in the file.

    Returns the limits as they now stand. Raises `LimitsError` for anything the caller got
    wrong, so an HTTP layer can turn it into a sentence rather than a 500.
    """
    checked = clean(values)

    boat = load_profile(str(profile_path)) if profile_path else load_profile()
    path = Path(profile_path) if profile_path else (Path(boat.path) if boat.path else None)
    if path is None or not path.is_file():
        raise LimitsError("no profile file to write to.")

    text = path.read_text(encoding="utf-8")
    start, end = _block(text)
    block = text[start:end]

    for key, number in checked.items():
        rendered = str(int(number)) if key in INTEGERS else f"{float(number):g}"
        block = _set(block, key, rendered)

    who = (by or "").strip() or "somebody using the app"
    stamp = (when or date.today().isoformat()).strip()
    # A quoted TOML string, with the two characters that could break out of one removed
    # rather than escaped — this ends up in a file a person reads, and a backslash escape
    # in the middle of a name reads like a mistake.
    mark = f'"{who.replace(chr(92), "").replace(chr(34), "")}, {stamp}"'
    block = _set(block, "source", mark)
    block = _retire_warning(block, who, stamp)

    updated = text[:start] + block + text[end:]

    # Through a temporary file in the same directory, then renamed over the original: a
    # rename is atomic on every filesystem this runs on, so an interrupted save leaves the
    # old profile intact rather than half a new one. A boat with no profile does not boot.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(updated, encoding="utf-8")
    os.replace(tmp, path)

    fresh = load_profile(str(path))
    return fresh.limits.as_dict()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="The skipper's weather limits.")
    ap.add_argument("--wind", type=float, help="max sustained wind, knots")
    ap.add_argument("--gust", type=float, help="max gust, knots")
    ap.add_argument("--wave", type=float, help="max significant wave height, metres")
    ap.add_argument("--rain", type=float, help="max rain, mm/h")
    ap.add_argument("--from-hour", type=int, dest="from_h")
    ap.add_argument("--to-hour", type=int, dest="to_h")
    ap.add_argument("--by", default="", help="whose limits these are")
    ap.add_argument("--profile")
    args = ap.parse_args(argv)

    wanted = {k: v for k, v in {
        "max_wind_kn": args.wind, "max_gust_kn": args.gust, "max_wave_m": args.wave,
        "max_rain_mm": args.rain, "daylight_from_h": args.from_h,
        "daylight_to_h": args.to_h}.items() if v is not None}

    if not wanted:
        boat = load_profile(args.profile) if args.profile else load_profile()
        lim = boat.limits
        print(f"profile: {boat.path}")
        print(f"  wind      {lim.max_wind_kn:g} kn")
        print(f"  gust      {lim.max_gust_kn:g} kn")
        print(f"  wave      {lim.max_wave_m:g} m")
        print(f"  rain      {lim.max_rain_mm:g} mm/h")
        print(f"  daylight  {lim.daylight_from_h:02d}:00–{lim.daylight_to_h:02d}:00")
        print()
        if lim.source:
            print(f"Set by {lim.source}.")
        else:
            print("⚠️  UNVERIFIED — nobody has set these. They are the package defaults, "
                  "and 'can we go out' is computed off them.\n"
                  "    Set them with: python3 -m openboat.limits --wind … --by 'your name'")
        return

    try:
        now = write(wanted, by=args.by, profile_path=args.profile)
    except (LimitsError, OSError) as exc:
        print(f"Not written: {exc}", file=sys.stderr)
        raise SystemExit(2)
    print(f"Set. wind {now['max_wind_kn']:g} kn · gust {now['max_gust_kn']:g} kn · "
          f"wave {now['max_wave_m']:g} m · rain {now['max_rain_mm']:g} mm/h · "
          f"daylight {now['daylight'][0]:02d}:00–{now['daylight'][1]:02d}:00")
    print(f"Recorded as set by {now['source']}.")


if __name__ == "__main__":
    main()
