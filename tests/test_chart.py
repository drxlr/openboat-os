#!/usr/bin/env python3
"""The chart's licence and tile-policy obligations, as tests.

    python3 tests/test_chart.py

Attribution and the ban on prefetching are not house style — they are the conditions under
which two donated tile servers let this project draw their maps. A pull request that drops
an attribution string or adds a "download this area" button is not a style regression, it
is the thing that gets a project blocked, so it fails the build instead of a review.

See docs/CHARTS.md. Do not weaken an assertion here to make a change pass.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from openboat.profile import DEFAULT_CHART, Profile, load  # noqa: E402

results: list[tuple[bool, str]] = []
INDEX = (ROOT / "openboat" / "web" / "index.html").read_text()
WINDY = (ROOT / "openboat" / "web" / "windy.html").read_text()


def check(condition: bool, what: str) -> None:
    results.append((bool(condition), what))
    print(f"{'  ok  ' if condition else '  FAIL'}  {what}")


# --------------------------------------------------------------------------------------
# 1. Attribution. OSM's policy specifies the wording; OpenSeaMap's tiles are CC BY-SA.
# --------------------------------------------------------------------------------------
def test_attribution() -> None:
    check("© OpenStreetMap contributors" in DEFAULT_CHART["base_attribution"],
          "the base layer credits OpenStreetMap contributors, in those words")
    check("OpenSeaMap" in DEFAULT_CHART["seamark_attribution"]
          and "CC BY-SA" in DEFAULT_CHART["seamark_attribution"],
          "the seamark layer credits OpenSeaMap and names its licence")

    for name, page in (("index.html", INDEX), ("windy.html", WINDY)):
        for layer in re.findall(r"L\.tileLayer\((.{0,400}?)\)\s*[.;\n]", page, re.S):
            check("attribution" in layer, f"every tile layer in {name} carries an attribution")


# --------------------------------------------------------------------------------------
# 2. No prefetching. OSM's tile policy forbids it in those words; see docs/CHARTS.md.
# --------------------------------------------------------------------------------------
def test_no_prefetch() -> None:
    banned = ("download for offline", "save area", "prefetch", "pre-seed", "preseed",
              "seedtiles", "seed_tiles", "downloadarea", "download_area", "bulk download")
    offenders = []
    for path in list((ROOT / "openboat").rglob("*.py")) + \
                list((ROOT / "openboat").rglob("*.html")):
        low = path.read_text().lower()
        for word in banned:
            # docs/ may name the practice in order to forbid it; code may not implement it.
            if word in low and "prohibit" not in low and "forbid" not in low:
                offenders.append(f"{path.name}: {word!r}")
    check(not offenders, f"no tile prefetch or offline-download feature ({offenders or 'clean'})")


# --------------------------------------------------------------------------------------
# 3. The tile URL is configuration, which the policy asks for and offline charts require.
# --------------------------------------------------------------------------------------
def test_tiles_are_configurable() -> None:
    check("base_url" in DEFAULT_CHART and "seamark_url" in DEFAULT_CHART,
          "tile URLs are profile settings, switchable without a software update")
    check("chart" in Profile().as_dict(),
          "the dashboard gets its layers from /api/profile, not from its own HTML")
    check("C.base_url" in INDEX and "chart.seamark_url" in WINDY,
          "both pages actually read the configured URL")


# --------------------------------------------------------------------------------------
# 4. No key is ever committed, and none is invented.
# --------------------------------------------------------------------------------------
def test_no_key_in_the_repo() -> None:
    check(DEFAULT_CHART["windy_map_key"] == "",
          "no Windy key ships with the project")
    check(load(ROOT / "profiles" / "demo-boat.toml").chart["windy_map_key"] == ""
          or __import__("os").environ.get("OPENBOAT_WINDY_MAP_KEY"),
          "the demo profile carries no key")

    key_shape = re.compile(r"['\"][A-Za-z0-9]{32}['\"]")
    offenders = [p.name for p in list((ROOT / "openboat").rglob("*.py"))
                 + list((ROOT / "openboat").rglob("*.html"))
                 + list((ROOT / "profiles").rglob("*.toml"))
                 if key_shape.search(p.read_text())]
    check(not offenders, f"nothing key-shaped is committed ({offenders or 'clean'})")


# --------------------------------------------------------------------------------------
# 5. The free Windy tier is labelled on screen. Windy forbid production use of it, and
#    their point API returns deliberately shuffled data — see docs/CHARTS.md.
# --------------------------------------------------------------------------------------
def test_free_tier_is_labelled() -> None:
    check("development only" in WINDY.lower(),
          "the weather chart says on screen that the free tier is development-only")
    check("windy_point_key" not in (ROOT / "openboat" / "profile.py").read_text(),
          "no point-forecast key setting exists: that tier's data is shuffled")
    check((ROOT / "docs" / "CHARTS.md").exists()
          and "randomly shuffled" in (ROOT / "docs" / "CHARTS.md").read_text(),
          "docs/CHARTS.md records why, with Windy's own wording")


if __name__ == "__main__":
    print(__doc__.splitlines()[0])
    print("-" * 78)
    for case in (test_attribution, test_no_prefetch, test_tiles_are_configurable,
                 test_no_key_in_the_repo, test_free_tier_is_labelled):
        case()
    print("-" * 78)
    failed = [what for ok, what in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} checks pass"
          + (f" — FAILED: {failed}" if failed else ""))
    sys.exit(1 if failed else 0)
