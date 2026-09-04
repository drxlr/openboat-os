"""Turn the log into one self-contained HTML page. No library, no CDN, no build step.

    python3 -m openboat.engine_report --db /tmp/engine-demo.db --out engine-report.html
    open engine-report.html

The output is a single file with the charts drawn as inline SVG this script generates.
That is not minimalism for its own sake: this page has to open in ten years, off a USB
stick, in front of a surveyor or a buyer, on a boat with no internet. Every chart library
in existence would be a broken image by then.

## Why the charts look the way they do

The palette below is a warm-neutral, colour-blind-checked set of tokens chosen for this
one page — the red/green pair a naive palette would reach for separates poorly for a
deuteranope, so it is avoided entirely here:

- **No chart here encodes more than two series by hue**, and the pair is always
  `--water` against `--rust`, chosen to separate clearly for every form of colour
  blindness tested.
- The rpm bands, which would have been several lines in several colours, are **small
  multiples** instead: one panel each, one hue, and the panels are easier to read anyway
  because the bands sit well apart.
- `--red`, `--rust` and `--good` are reserved for verdict badges, where they appear with
  a glyph and a word and never carry meaning by colour alone.
- Every chart has a table under it. Colour is never the only way to get the number.
"""

from __future__ import annotations

import argparse
import html
import json
from datetime import date, datetime, timezone, tzinfo as TzInfo
from pathlib import Path
from zoneinfo import ZoneInfo

from . import engine_health, engine_hours as hourmeter
from .engine import DEFAULT_DB, connect, meta_get

#: Where the dashboard serves it from — see `openboat/server.py`'s REPORTS map. These two
#: were different for a while and the Engine log tab showed a placeholder the whole time.
DEFAULT_OUT = Path("reports") / "engine.html"

# The chart canvas, in viewBox units. Everything scales from here.
WIDE = (720, 240)
PANEL = (360, 190)
PAD = {"left": 46, "right": 16, "top": 14, "bottom": 26}

VERDICT_GLYPH = {"good": "✓", "watch": "▲", "bad": "✗", "unknown": "·"}
VERDICT_WORD = {"good": "steady", "watch": "watch", "bad": "act", "unknown": "no verdict"}


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def _nice_ticks(low: float, high: float, count: int = 4) -> list[float]:
    """Round tick values inside a range. Grid lines on 87.3 °C help nobody."""
    if high <= low:
        return [low]
    span = high - low
    raw = span / count
    magnitude = 10 ** int(f"{raw:e}".split("e")[1])
    step = min((m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw),
               default=magnitude)
    first = step * int(low / step)
    ticks, value = [], first
    while value <= high + step * 0.01:
        if value >= low - step * 0.01:
            ticks.append(round(value, 6))
        value += step
    return ticks


def _days(stamp: str) -> int:
    return date.fromisoformat(stamp).toordinal()


def line_chart(series: dict[str, list[tuple[str, float]]], unit: str,
               size=WIDE, hues=("--water", "--rust")) -> str:
    """Time on x, value on y, one or two series. Crosshair and tooltip come from the page JS.

    Series are drawn as bare 2px lines with no permanent markers: ninety outings' worth of
    dots is a texture, not a chart. The marker appears under the cursor instead.
    """
    series = {name: points for name, points in series.items() if points}
    if not series:
        return '<p class="hint">Nothing to draw — no data in this series.</p>'

    width, height = size
    plot_w = width - PAD["left"] - PAD["right"]
    plot_h = height - PAD["top"] - PAD["bottom"]

    everything = [p for points in series.values() for p in points]
    xs = [_days(stamp) for stamp, _ in everything]
    ys = [value for _, value in everything]
    x0, x1 = min(xs), max(xs)
    span = max(1, x1 - x0)
    lo, hi = min(ys), max(ys)
    margin = (hi - lo) * 0.12 or 1.0
    lo, hi = lo - margin, hi + margin

    def px(day: int) -> float:
        return PAD["left"] + (day - x0) / span * plot_w

    def py(value: float) -> float:
        return PAD["top"] + (hi - value) / (hi - lo) * plot_h

    wide = " wide" if size == WIDE else ""
    parts = [f'<svg class="chart{wide}" viewBox="0 0 {width} {height}" role="img">']

    for tick in _nice_ticks(lo, hi):
        y = py(tick)
        parts.append(f'<line class="grid" x1="{PAD["left"]}" x2="{width - PAD["right"]}" '
                     f'y1="{y:.1f}" y2="{y:.1f}"/>')
        parts.append(f'<text class="axis" x="{PAD["left"] - 6}" y="{y + 3.5:.1f}" '
                     f'text-anchor="end">{tick:g}</text>')

    # One x tick per year boundary inside the range, plus the two ends.
    seen = set()
    for day in range(x0, x1 + 1):
        stamp = date.fromordinal(day)
        if stamp.month == 1 and stamp.day == 1 and stamp.year not in seen:
            seen.add(stamp.year)
            parts.append(f'<line class="grid" x1="{px(day):.1f}" x2="{px(day):.1f}" '
                         f'y1="{PAD["top"]}" y2="{PAD["top"] + plot_h}"/>')
            parts.append(f'<text class="axis" x="{px(day):.1f}" y="{height - 8}" '
                         f'text-anchor="middle">{stamp.year}</text>')
    for day, anchor in ((x0, "start"), (x1, "end")):
        parts.append(f'<text class="axis" x="{px(day):.1f}" y="{height - 8}" '
                     f'text-anchor="{anchor}">{date.fromordinal(day):%m.%y}</text>')

    # Painted back to front, so the first series in the legend ends up on top: it is the
    # one the section is about, and the other is the context it is being read against.
    for index, (name, points) in reversed(list(enumerate(series.items()))):
        hue = hues[index % len(hues)]
        dash = ' stroke-dasharray="4 3"' if index else ""
        path = " ".join(f"{'M' if i == 0 else 'L'}{px(_days(s)):.1f},{py(v):.1f}"
                        for i, (s, v) in enumerate(points))
        parts.append(f'<path d="{path}" fill="none" stroke="var({hue})" stroke-width="2" '
                     f'stroke-linejoin="round" stroke-linecap="round"{dash}/>')
        last_stamp, last_value = points[-1]
        parts.append(f'<circle cx="{px(_days(last_stamp)):.1f}" cy="{py(last_value):.1f}" '
                     f'r="3.5" fill="var({hue})" stroke="var(--panel)" stroke-width="2"/>')

    # The hover layer: one transparent column per x position, carrying every series' value.
    columns: dict[int, list[str]] = {}
    for name, points in series.items():
        for stamp, value in points:
            columns.setdefault(_days(stamp), []).append(f"{name}: {value:g} {unit}".strip())
    ordered = sorted(columns)
    step = plot_w / max(1, len(ordered))
    for day in ordered:
        label = f"{date.fromordinal(day):%d.%m.%Y}"
        payload = esc(json.dumps([label] + columns[day]))
        parts.append(f'<rect class="hit" x="{px(day) - step / 2:.1f}" y="{PAD["top"]}" '
                     f'width="{max(step, 3):.1f}" height="{plot_h}" data-tip="{payload}"/>')

    parts.append("</svg>")

    legend = ""
    if len(series) > 1:
        chips = "".join(
            f'<span class="key"><i style="background:var({hues[i % len(hues)]});'
            f'{"opacity:.55" if i else ""}"></i>{esc(name)}</span>'
            for i, name in enumerate(series))
        legend = f'<div class="legend">{chips}</div>'
    return legend + "".join(parts)


def bar_chart(points: list[tuple[str, float]], unit: str) -> str:
    """Magnitude by month. One series, so one hue and no legend — the title names it."""
    if not points:
        return '<p class="hint">No hours logged yet.</p>'

    width = max(WIDE[0], 22 * len(points) + PAD["left"] + PAD["right"])
    height = WIDE[1]
    plot_w = width - PAD["left"] - PAD["right"]
    plot_h = height - PAD["top"] - PAD["bottom"]
    top = max(v for _, v in points) * 1.1 or 1.0
    slot = plot_w / len(points)
    bar_w = max(4.0, slot - 3.0)          # a 3-unit surface gap between neighbours

    labelled: set[str] = set()
    parts = [f'<svg class="chart wide" viewBox="0 0 {width} {height}" role="img">']
    for tick in _nice_ticks(0, top):
        y = PAD["top"] + (1 - tick / top) * plot_h
        parts.append(f'<line class="grid" x1="{PAD["left"]}" x2="{width - PAD["right"]}" '
                     f'y1="{y:.1f}" y2="{y:.1f}"/>')
        parts.append(f'<text class="axis" x="{PAD["left"] - 6}" y="{y + 3.5:.1f}" '
                     f'text-anchor="end">{tick:g}</text>')

    for index, (label, value) in enumerate(points):
        x = PAD["left"] + index * slot + (slot - bar_w) / 2
        bar_h = max(1.0, value / top * plot_h)
        y = PAD["top"] + plot_h - bar_h
        payload = esc(json.dumps([label, f"{value:.1f} {unit}".strip()]))
        parts.append(f'<rect class="bar" x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" '
                     f'height="{bar_h:.1f}" rx="2" data-tip="{payload}"/>')
        # The first month present in each year, not every January — a winter with no
        # outings has no January bar, and the year label would vanish with it.
        if label[:4] not in labelled:
            labelled.add(label[:4])
            parts.append(f'<text class="axis" x="{x + bar_w / 2:.1f}" y="{height - 8}" '
                         f'text-anchor="middle">{label[:4]}</text>')

    parts.append("</svg>")
    return "".join(parts)


def table(headers: list[str], rows: list[list], caption: str) -> str:
    """The non-colour route to every number, and the thing a surveyor will actually read."""
    if not rows:
        return ""          # an empty "all 0 values" disclosure is worse than no disclosure
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f'<td class="mono">{esc(c)}</td>' for c in row) + "</tr>"
                   for row in rows)
    return (f'<details><summary>{esc(caption)}</summary>'
            f'<div class="scroll"><table><thead><tr>{head}</tr></thead>'
            f'<tbody>{body}</tbody></table></div></details>')


def badge(verdict: str) -> str:
    return (f'<span class="badge {verdict}">{VERDICT_GLYPH[verdict]} '
            f'{VERDICT_WORD[verdict]}</span>')


def finding_block(finding, size=WIDE) -> str:
    detail = "".join(f"<li>{esc(line)}</li>" for line in finding.detail)
    rows: dict[str, dict[str, float]] = {}
    for name, points in finding.series.items():
        for stamp, value in points:
            rows.setdefault(stamp, {})[name] = value
    names = [n for n, p in finding.series.items() if p]
    body = [[stamp] + [rows[stamp].get(n, "—") for n in names] for stamp in sorted(rows)]

    return (f'<div class="finding">'
            f'<div class="fhead">{badge(finding.verdict)}<h3>{esc(finding.title)}</h3></div>'
            f'<p class="lead">{esc(finding.headline)}</p>'
            f'<ul class="detail">{detail}</ul>'
            f'<div class="scroll">{line_chart(finding.series, finding.unit, size=size)}</div>'
            + (table(["date"] + names, body, f"{finding.title} — all {len(body)} values")
               if body else "") +
            f'</div>')


CSS = """
:root {
  --paper:#F1F3F1; --panel:#FFFFFF; --ink:#1A1D1C; --ink-soft:#3D4442;
  --muted:#6B7370; --rule:#D6DAD7; --rule-soft:#E6E9E6;
  --red:#A93E2F; --water:#1E6B8C; --rust:#B5651D; --good:#3E7A4E;
  --shadow:0 1px 2px rgba(26,29,28,.06), 0 8px 24px -12px rgba(26,29,28,.14);
}
@media (prefers-color-scheme: dark) {
  :root {
    --paper:#15181A; --panel:#1D2123; --ink:#E9EBE8; --ink-soft:#B6BDB9;
    --muted:#8B9490; --rule:#2E3436; --rule-soft:#262B2D;
    --red:#E4705C; --water:#63B0D1; --rust:#D89A55; --good:#6FB183;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 8px 24px -12px rgba(0,0,0,.6);
  }
}
*,*::before,*::after { box-sizing:border-box; }
body { margin:0; background:var(--paper); color:var(--ink); font-size:17px; line-height:1.6;
       font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
       -webkit-font-smoothing:antialiased; }
.mono { font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
        font-variant-numeric:tabular-nums; }
.wrap { max-width:62rem; margin:0 auto; padding:0 1.1rem 4rem; }
h1,h2,h3 { margin:0; line-height:1.2; letter-spacing:-.018em; text-wrap:balance; }
a { color:var(--water); }

header { padding:2.2rem 0 1.2rem; display:flex; flex-wrap:wrap; align-items:baseline; gap:.7rem; }
.boat { font-size:.72rem; font-weight:700; text-transform:uppercase; letter-spacing:.16em;
        color:var(--muted); width:100%; }
h1 { font-size:1.9rem; }
.pill { font-size:.72rem; font-weight:700; text-transform:uppercase; letter-spacing:.1em;
        padding:.25rem .6rem; border-radius:999px; border:1px solid var(--rule); color:var(--muted); }
.pill.warn { color:var(--rust); border-color:var(--rust); }

section { background:var(--panel); border:1px solid var(--rule); border-radius:12px;
          box-shadow:var(--shadow); padding:1.2rem 1.3rem; margin-bottom:1.1rem; }
h2 { font-size:.74rem; font-weight:700; text-transform:uppercase; letter-spacing:.14em;
     color:var(--muted); margin-bottom:.8rem; }
h3 { font-size:1rem; font-weight:600; }
.sub { color:var(--ink-soft); font-size:.95rem; margin-top:.35rem; }
.hint { font-size:.85rem; color:var(--muted); margin:.6rem 0 0; }

.banner { border-color:var(--rust); border-width:2px; }
.banner h2 { color:var(--rust); }
.banner p { margin:0; color:var(--ink-soft); }

.hero { font-size:2.6rem; font-weight:600; letter-spacing:-.03em; line-height:1.1; }
.hero .unit { font-size:1.1rem; color:var(--muted); font-weight:500; letter-spacing:0; }
.stats { display:flex; flex-wrap:wrap; gap:1.6rem; margin-top:1rem;
         border-top:1px solid var(--rule-soft); padding-top:.9rem; }
.stat .k { font-size:.7rem; text-transform:uppercase; letter-spacing:.12em; color:var(--muted); }
.stat .v { font-size:1.25rem; font-weight:600; }

.badge { font-size:.68rem; font-weight:700; text-transform:uppercase; letter-spacing:.1em;
         padding:.2rem .55rem; border-radius:999px; border:1px solid currentColor;
         white-space:nowrap; }
.badge.good { color:var(--good); } .badge.watch { color:var(--rust); }
.badge.bad { color:var(--red); }   .badge.unknown { color:var(--muted); }

.finding { border-top:1px solid var(--rule-soft); padding-top:1rem; margin-top:1rem; }
.finding:first-of-type { border-top:0; padding-top:0; margin-top:0; }
.fhead { display:flex; align-items:center; gap:.6rem; flex-wrap:wrap; }
.lead { margin:.5rem 0 .4rem; color:var(--ink); }
ul.detail { margin:.2rem 0 .8rem; padding-left:1.1rem; color:var(--muted); font-size:.88rem; }

.panels { display:grid; grid-template-columns:repeat(auto-fit,minmax(19rem,1fr)); gap:1.1rem; }
.panels .finding { border-top:0; padding-top:0; margin-top:0; }

.scroll { overflow-x:auto; position:relative; }
svg.chart { width:100%; height:auto; display:block; margin-top:.4rem; min-width:17rem; }
svg.chart.wide { min-width:32rem; }
svg.chart .grid { stroke:var(--rule-soft); stroke-width:1; }
svg.chart .axis { fill:var(--muted); font-size:10px;
                  font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
svg.chart .bar { fill:var(--water); }
svg.chart .bar:hover { fill-opacity:.7; }
svg.chart .hit { fill:transparent; }
svg.chart .hit:hover { fill:var(--ink); fill-opacity:.05; }

.legend { display:flex; gap:1rem; flex-wrap:wrap; font-size:.8rem; color:var(--muted); }
.key { display:inline-flex; align-items:center; gap:.35rem; }
.key i { width:12px; height:3px; border-radius:2px; display:inline-block; }

#tip { position:fixed; z-index:9; pointer-events:none; opacity:0; transition:opacity .1s;
       background:var(--panel); color:var(--ink); border:1px solid var(--rule);
       border-radius:8px; box-shadow:var(--shadow); padding:.4rem .6rem; font-size:.78rem;
       font-family:ui-monospace,SFMono-Regular,Menlo,monospace; white-space:pre; }

details { margin-top:.7rem; }
summary { cursor:pointer; font-size:.8rem; color:var(--muted); }
table { border-collapse:collapse; font-size:.8rem; margin-top:.5rem; width:100%; }
th,td { text-align:right; padding:.25rem .6rem; border-top:1px solid var(--rule-soft);
        white-space:nowrap; }
th { color:var(--muted); font-weight:600; font-size:.7rem; text-transform:uppercase;
     letter-spacing:.08em; }
th:first-child, td:first-child { text-align:left; }
"""

JS = """
// One tooltip for every chart. The hit targets are full-height columns, so the pointer
// never has to find a 2px line — see references on interaction: bigger than the mark.
const tip = document.getElementById('tip');
document.querySelectorAll('svg.chart [data-tip]').forEach(hit => {
  hit.addEventListener('pointerenter', event => {
    tip.textContent = JSON.parse(hit.dataset.tip).join('\\n');
    tip.style.opacity = 1;
    move(event);
  });
  hit.addEventListener('pointermove', move);
  hit.addEventListener('pointerleave', () => { tip.style.opacity = 0; });
});
function move(event) {
  const box = tip.getBoundingClientRect();
  const x = Math.min(event.clientX + 14, window.innerWidth - box.width - 8);
  const y = Math.max(8, event.clientY - box.height - 12);
  tip.style.left = x + 'px';
  tip.style.top = y + 'px';
}
"""


def build(db, db_path: Path, profile, tz: TzInfo = timezone.utc) -> str:
    meter = hourmeter.summary(db, tz=tz)
    checks = engine_health.analyse(db, tz=tz)
    synthetic = meter["synthetic"]

    blocks = []

    if synthetic:
        blocks.append(
            '<section class="banner"><h2>Synthetic data</h2><p><strong>No engine ran.</strong> '
            'Every figure on this page was fabricated by <code>engine_seed.py</code> to prove '
            'the pipeline works before a real engine sender is wired up. It is not a record of '
            f'{esc(profile.vessel.name)} and must never be shown to a surveyor, an insurer or '
            'a buyer.</p></section>')

    # --- hours ---------------------------------------------------------------------
    if meter["engine_hours_total"] is not None:
        hero = (f'<div class="hero">{meter["engine_hours_total"]:.0f}'
                f'<span class="unit"> engine hours</span></div>'
                f'<p class="sub">{meter["baseline_hours"]:.1f} h read off the helm display on '
                f'{esc(meter["baseline_at"])}, plus {meter["running_h"]:.1f} h counted since.</p>')
    else:
        hero = (f'<div class="hero">{meter["running_h"]:.1f}'
                f'<span class="unit"> hours, logged</span></div>'
                f'<p class="sub">Not engine hours. This is what the logger watched, and the '
                f'engine may be older than the logger. Photograph the helm hour display once '
                f'and record it with <code>engine_hours.py --baseline &lt;h&gt; --baseline-at '
                f'&lt;date&gt;</code>, and this becomes a total a surveyor can follow.</p>')

    stats = [("Outings", f'{meter["outings"]}'),
             ("Unknown", f'{meter["unknown_h"]:,.1f} h' if meter["unknown_h"] < 10
              else f'{meter["unknown_h"]:,.0f} h'),
             ("Coverage", f'{meter["coverage"] * 100:.1f} %'),
             ("Samples", f'{meter["samples"]:,}'),
             ("First", (meter["first"] or "—")[:10]),
             ("Last", (meter["last"] or "—")[:10])]
    stat_html = "".join(f'<div class="stat"><div class="k">{esc(k)}</div>'
                        f'<div class="v mono">{esc(v)}</div></div>' for k, v in stats)

    blocks.append(f'<section><h2>Engine hours</h2>{hero}<div class="stats">{stat_html}</div>'
                  f'<p class="hint"><strong>The logged total is a lower bound.</strong> '
                  f'{meter["unknown_h"]:,.1f} hours passed with nobody watching — the logger '
                  f'off, the boat unpowered, or Signal K holding a dead sender\'s last value. '
                  f'The engine may have run in some of that. It is counted as unknown, never '
                  f'as zero.</p></section>')

    months = list(meter["by_month"].items())
    seasons = "".join(f'<div class="stat"><div class="k">{esc(k)}</div>'
                      f'<div class="v mono">{v:.1f} h</div></div>'
                      for k, v in meter["by_season"].items())
    blocks.append(
        f'<section><h2>Hours per month</h2>'
        f'<div class="scroll">{bar_chart(months, "h")}</div>'
        f'<div class="stats">{seasons}</div>'
        f'<p class="hint">A season here is a calendar year rather than a fixed cruising '
        f'season, so it fits a boat used year-round as well as one laid up for winter.</p>'
        + table(["month", "hours"], [[m, f"{v:.2f}"] for m, v in months],
                f"Hours per month — all {len(months)} months") +
        f'</section>')

    # --- cooling, as small multiples ------------------------------------------------
    cooling = [f for f in checks["findings"] if f.title.startswith("Cooling")]
    others = [f for f in checks["findings"] if not f.title.startswith("Cooling")]

    if cooling:
        panels = "".join(finding_block(f, size=PANEL) for f in cooling)
        sea_ref = checks["rules"]["sea_reference_c"]
        ref_text = f"a {sea_ref:.0f} °C sea" if sea_ref is not None else "one reference sea"
        blocks.append(
            f'<section><h2>Cooling — coolant drift at constant sea temperature</h2>'
            f'<p class="sub">The engine is raw-water cooled, so the sea it swims in is in '
            f'every reading, and across a season that swing can easily be far larger than the '
            f'fault worth catching. Inside each rpm band, coolant temperature is fitted '
            f'against days <em>and</em> sea temperature at once; the line plotted is every '
            f'outing pulled to {ref_text} — the median this boat\'s own log has actually seen '
            f'— which is what makes a two-degree drift visible at all.</p>'
            f'<div class="panels">{panels}</div>'
            f'<p class="hint">One median per outing per band — never one point per sample. A '
            f'verdict needs {checks["rules"]["min_outings"]} outings across '
            f'{checks["rules"]["min_span_days"]:.0f} days and a slope twice its own standard '
            f'error; below that the honest answer is printed instead.</p></section>')

    for finding in others:
        blocks.append(f'<section><h2>{esc(finding.title)}</h2>{finding_block(finding)}</section>')

    # --- the limits -----------------------------------------------------------------
    blocks.append(
        '<section><h2>What this page cannot know</h2>'
        '<ul class="detail" style="font-size:.95rem;color:var(--ink-soft)">'
        '<li>Hours before the logger existed. Nothing here can know what the engine did before '
        'it started logging; only the helm display answers that, and only once someone '
        'photographs it.</li>'
        '<li>Whether the engine ran during an unknown gap. Someone else aboard, a mechanic '
        'running it on the hard — none of that is visible here.</li>'
        '<li>Whether a sender is telling the truth. Every trend on this page is a trend in '
        'what the sender reported. A drifting sender and a drifting engine look identical '
        'from here; confirm anything alarming against a mechanical gauge.</li>'
        '<li>Anything about the exhaust side. Manifold and riser trouble tends to show up in '
        'these numbers late — it is worth a look and a listen now and then, not just a '
        'glance at this page.</li>'
        '</ul></section>')

    generated = datetime.now(tz).strftime("%d.%m.%Y %H:%M")
    title = f"{profile.vessel.name} — engine log" + (" (SYNTHETIC)" if synthetic else "")

    boat_line = " · ".join(
        part for part in (profile.vessel.kind, profile.vessel.engine_note, profile.berth_name)
        if part) or profile.vessel.name

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{esc(title)}</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
<header>
  <span class="boat">{esc(boat_line)}</span>
  <h1>Engine log</h1>
  <span class="pill{' warn' if synthetic else ''}">
    {'synthetic data' if synthetic else 'logged data'}</span>
</header>
{''.join(blocks)}
<p class="hint">Generated {esc(generated)} from <code>{esc(db_path.name)}</code> ·
   {meter['samples']:,} samples · charts are inline SVG, no library, no network.</p>
</div>
<div id="tip" class="mono"></div>
<script>{JS}</script>
</body>
</html>
"""


def main() -> None:
    from .profile import load as load_profile

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--profile", help="path to a boat.toml (default: the usual search)")
    args = parser.parse_args()

    profile = load_profile(args.profile)
    tz = ZoneInfo(profile.timezone)

    db = connect(args.db)
    synthetic = meta_get(db, "synthetic") == "1"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(build(db, args.db, profile, tz), encoding="utf-8")
    db.close()

    print(f"wrote {args.out} ({args.out.stat().st_size / 1024:.0f} kB)"
          + (" ⚠ SYNTHETIC" if synthetic else ""))


if __name__ == "__main__":
    main()
