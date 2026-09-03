"""`season.py`'s numbers as a page you can hand to someone — charts drawn, not plotted.

Every chart here is an SVG string built in Python. No chart library, no CDN, no script tag
doing the drawing: the file that comes out is one document that renders with the network
off, on the boat, in five years. That constraint is the reason for the hand-rolled scales
below, and it is worth the effort.

Decimals are commas and dates are European throughout — a fixed style, not the viewer's
locale, matching the rest of this project's logbook output.
"""

from __future__ import annotations

import math

from . import windows
from . import season as season_mod
from .trip import WAVE_START

# Chart geometry. One width for every chart so the columns line up down the page, and a
# min-width in CSS so a narrow screen scrolls the chart instead of crushing it.
W = 760
PAD_L, PAD_R = 44, 48

SHORT = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def de(value: float, digits: int = 1) -> str:
    text = f"{value:,.{digits}f}"
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _svg(height: int, body: str, label: str) -> str:
    return (f'<div class="chart"><svg viewBox="0 0 {W} {height}" width="100%" '
            f'height="{height}" role="img" aria-label="{label}" '
            f'preserveAspectRatio="xMidYMid meet">{body}</svg></div>')


def _band(index: int, count: int = 12, right: float | None = None) -> tuple[float, float]:
    """Left edge and width of one month's slot across the plot area.

    `right` overrides the default right margin for charts that hang a label out there.
    """
    inner = W - PAD_L - (PAD_R if right is None else right)
    step = inner / count
    return PAD_L + index * step, step


# --- the season -------------------------------------------------------------------------

def season_chart(months, threshold: float = 70.0) -> str:
    """Bars: share of days with a full six-hour window. Line: sea temperature.

    These two together are the whole decision. Weather good enough to go out is necessary
    and not sufficient — nobody swims off the boat in a cold sea — so the months worth
    owning a boat in are where the bar is tall AND the line is high, and the shape of that
    overlap is the answer the page exists to give.
    """
    height, top, floor = 320, 26, 250
    sst_lo, sst_hi = 5.0, 26.0
    parts = []

    for value in (0, 25, 50, 75, 100):
        y = floor - value / 100 * (floor - top)
        parts.append(f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{W - PAD_R}" y2="{y:.1f}" '
                     f'stroke="var(--rule-soft)" stroke-width="1"/>'
                     f'<text x="{PAD_L - 8}" y="{y + 4:.1f}" text-anchor="end" '
                     f'class="ax">{value}%</text>')

    y_line = floor - threshold / 100 * (floor - top)
    parts.append(f'<line x1="{PAD_L}" y1="{y_line:.1f}" x2="{W - PAD_R}" y2="{y_line:.1f}" '
                 f'stroke="var(--ink-soft)" stroke-width="1.5" stroke-dasharray="5 4"/>'
                 f'<text x="{W - PAD_R + 6}" y="{y_line - 5:.1f}" class="ax" '
                 f'fill="var(--ink-soft)">the season line</text>')

    points = []
    for i, m in enumerate(months):
        left, step = _band(i)
        bar_w = step * 0.56
        x = left + (step - bar_w) / 2
        h = m.full_day_pct / 100 * (floor - top)
        # Two states, not a gradient: in the season or not. Colour carries one meaning here.
        fill = "var(--water)" if m.full_day_pct >= threshold else "var(--rule)"
        parts.append(
            f'<rect x="{x:.1f}" y="{floor - h:.1f}" width="{bar_w:.1f}" height="{h:.1f}" '
            f'rx="3" fill="{fill}"><title>{m.name}: {de(m.full_day_pct, 0)} % of days hold '
            f'a six-hour window ({de(m.full_days)} days)</title></rect>'
            f'<text x="{left + step / 2:.1f}" y="{floor - h - 7:.1f}" text-anchor="middle" '
            f'class="val" paint-order="stroke" stroke="var(--panel)" stroke-width="3.5" '
            f'stroke-linejoin="round">{de(m.full_day_pct, 0)}</text>'
            f'<text x="{left + step / 2:.1f}" y="{floor + 19:.1f}" text-anchor="middle" '
            f'class="ax">{SHORT[m.month]}</text>')
        if m.sea_c is not None:
            ratio = (m.sea_c - sst_lo) / (sst_hi - sst_lo)
            points.append((left + step / 2, floor - max(0.0, min(1.0, ratio)) * (floor - top)))

    if points:
        path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}"
                        for i, (x, y) in enumerate(points))
        parts.append(f'<path d="{path}" fill="none" stroke="var(--rust)" stroke-width="2.5" '
                     f'stroke-linejoin="round"/>')
        for (x, y), m in zip(points, months):
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="var(--panel)" '
                         f'stroke="var(--rust)" stroke-width="2"><title>{m.name}: sea '
                         f'{de(m.sea_c)} °C</title></circle>')
        for degrees in sorted({round(sst_lo), round((sst_lo + sst_hi) / 2), round(sst_hi)}):
            y = floor - (degrees - sst_lo) / (sst_hi - sst_lo) * (floor - top)
            parts.append(f'<text x="{W - PAD_R + 6}" y="{y + 4:.1f}" class="ax" '
                         f'fill="var(--rust)">{degrees}°</text>')

    parts.append(f'<text x="{PAD_L}" y="14" class="ax">days with a 6 h window</text>'
                 f'<text x="{W - PAD_R}" y="14" text-anchor="end" class="ax" '
                 f'fill="var(--rust)">sea temperature</text>')
    return _svg(height, "".join(parts), "Share of days per month holding a six-hour weather "
                                        "window, with mean sea temperature")


# --- what actually stops you --------------------------------------------------------------

def limits_chart(months, limits: dict) -> str:
    """Four rows, one per limit, so the reason a month is bad is legible at a glance.

    Grouping four coloured bars per month would need a categorical palette and would bury
    the finding. Four single-colour rows on a shared scale put it in the open: a tall row
    in the wrong season names the actual cause, rather than leaving it to be assumed.
    """
    rows = [("over the wind limit", f"more than {limits['max_wind_kn']:.0f} kn", "fail_wind_pct"),
            ("gusting past the limit", f"gusts over {limits['max_gust_kn']:.0f} kn", "fail_gust_pct"),
            ("sea over the limit", f"more than {limits['max_wave_m']} m", "fail_sea_pct"),
            ("raining", f"more than {limits['max_rain_mm']} mm in the hour", "fail_rain_pct")]

    scale = 40.0          # per cent of daylight hours; nothing reaches it, and a shared
    row_h, gap, top = 46, 34, 30      # ceiling is what makes the four rows comparable
    height = top + len(rows) * (row_h + gap)
    parts = []

    for r, (title, sub, field) in enumerate(rows):
        base = top + r * (row_h + gap) + row_h
        parts.append(f'<text x="0" y="{base - row_h - 11:.0f}" class="rowlab">{title}'
                     f'<tspan class="ax" dx="8">{sub}</tspan></text>'
                     f'<line x1="{PAD_L}" y1="{base}" x2="{W - 110}" y2="{base}" '
                     f'stroke="var(--rule)" stroke-width="1"/>')
        for i, m in enumerate(months):
            left, step = _band(i, 12, right=110)
            bar_w = step * 0.56
            value = getattr(m, field)
            h = min(value / scale, 1.0) * row_h
            colour = "var(--rust)" if field == "fail_sea_pct" else "var(--water)"
            parts.append(
                f'<rect x="{left + (step - bar_w) / 2:.1f}" y="{base - h:.1f}" '
                f'width="{bar_w:.1f}" height="{max(h, 0.6):.1f}" rx="2" fill="{colour}" '
                f'opacity="{0.35 + 0.65 * min(value / scale, 1.0):.2f}">'
                f'<title>{m.name}: {de(value)} % of daylight hours</title></rect>')
            if r == len(rows) - 1:
                parts.append(f'<text x="{left + step / 2:.1f}" y="{base + 17:.0f}" '
                             f'text-anchor="middle" class="ax">{SHORT[m.month]}</text>')
        # Only the ceiling is labelled; the baseline rule is its own label, and a "0"
        # here sat directly under the next row's title.
        peak = max(months, key=lambda m: getattr(m, field))
        parts.append(f'<text x="{PAD_L - 8}" y="{base - row_h + 4:.0f}" text-anchor="end" '
                     f'class="ax">{scale:.0f}%</text>'
                     f'<text x="{W - 104}" y="{base - row_h + 4:.0f}" class="ax">'
                     f'worst: {SHORT[peak.month]}, {de(getattr(peak, field), 0)} %</text>')
    return _svg(height + 22, "".join(parts),
                "Share of daylight hours over each of the four limits, by month")


# --- the wind itself ----------------------------------------------------------------------

def wind_box(months, limits: dict) -> str:
    """p10-p90 whisker, p25-p75 box, median tick — the distribution, not just the mean.

    A monthly mean wind speed is close to useless for planning: it is the same number for a
    month of steady nine knots and a month that alternates flat calm with a gale. The spread
    is the planning fact, so the spread is what is drawn.
    """
    height, top, floor = 300, 26, 246
    ceiling = 24.0
    parts = []

    def y_of(knots: float) -> float:
        return floor - min(knots / ceiling, 1.0) * (floor - top)

    for knots in (0, 5, 10, 15, 20):
        y = y_of(knots)
        parts.append(f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{W - PAD_R}" y2="{y:.1f}" '
                     f'stroke="var(--rule-soft)"/>'
                     f'<text x="{PAD_L - 8}" y="{y + 4:.1f}" text-anchor="end" '
                     f'class="ax">{knots}</text>')

    y_limit = y_of(limits["max_wind_kn"])
    parts.append(f'<line x1="{PAD_L}" y1="{y_limit:.1f}" x2="{W - PAD_R}" y2="{y_limit:.1f}" '
                 f'stroke="var(--red)" stroke-width="1.5" stroke-dasharray="5 4"/>'
                 f'<text x="{W - PAD_R + 6}" y="{y_limit + 4:.1f}" class="ax" '
                 f'fill="var(--red)">limit</text>')

    for i, m in enumerate(months):
        left, step = _band(i)
        cx = left + step / 2
        box_w = step * 0.44
        parts.append(
            f'<line x1="{cx:.1f}" y1="{y_of(m.p10):.1f}" x2="{cx:.1f}" y2="{y_of(m.p90):.1f}" '
            f'stroke="var(--water)" stroke-width="1.5"/>'
            f'<rect x="{cx - box_w / 2:.1f}" y="{y_of(m.p75):.1f}" width="{box_w:.1f}" '
            f'height="{y_of(m.p25) - y_of(m.p75):.1f}" rx="2" fill="var(--water)" '
            f'opacity="0.35" stroke="var(--water)" stroke-width="1"/>'
            f'<line x1="{cx - box_w / 2:.1f}" y1="{y_of(m.median):.1f}" '
            f'x2="{cx + box_w / 2:.1f}" y2="{y_of(m.median):.1f}" '
            f'stroke="var(--water)" stroke-width="2.5"/>'
            f'<rect x="{left:.1f}" y="{top}" width="{step:.1f}" height="{floor - top}" '
            f'fill="transparent"><title>{m.name}: median {de(m.median)} kn, '
            f'half of all daylight hours between {de(m.p25)} and {de(m.p75)} kn, '
            f'nine in ten below {de(m.p90)} kn, strongest {de(m.strongest, 0)} kn</title></rect>'
            f'<text x="{cx:.1f}" y="{floor + 19:.1f}" text-anchor="middle" '
            f'class="ax">{SHORT[m.month]}</text>')

    parts.append(f'<text x="{PAD_L}" y="14" class="ax">knots · box is the middle half of '
                 f'daylight hours, whisker p10–p90</text>')
    return _svg(height, "".join(parts), "Distribution of daylight wind speed by month")


# --- the sea breeze -----------------------------------------------------------------------

def diurnal_chart(month, limits: dict, ceiling: float = 16.0) -> str:
    """Mean wind by local hour, with the wind's direction drawn as an arrow every third hour.

    The arrows are the half that matters. Speed alone makes a calm winter afternoon look
    like a breezy summer one; the direction shows one of them swinging round to a
    consistent quarter and staying there, which is a thermal circulation and is therefore
    something you can plan a departure time around, where the coast produces one at all.
    """
    height, top, floor = 250, 24, 172
    low, high = limits["daylight"]
    parts = []

    def x_of(hour: float) -> float:
        return PAD_L + hour / 23 * (W - PAD_L - PAD_R)

    def y_of(knots: float) -> float:
        return floor - min(knots / ceiling, 1.0) * (floor - top)

    parts.append(f'<rect x="{x_of(low):.1f}" y="{top}" width="{x_of(high) - x_of(low):.1f}" '
                 f'height="{floor - top}" fill="var(--rule-soft)" opacity="0.7"/>'
                 f'<text x="{(x_of(low) + x_of(high)) / 2:.1f}" y="{top + 12}" '
                 f'text-anchor="middle" class="ax">daylight, {low}–{high}h</text>')

    for knots in range(0, int(ceiling) + 1, 5 if ceiling > 12 else 2):
        y = y_of(knots)
        parts.append(f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{W - PAD_R}" y2="{y:.1f}" '
                     f'stroke="var(--rule)" stroke-width="0.7"/>'
                     f'<text x="{PAD_L - 8}" y="{y + 4:.1f}" text-anchor="end" '
                     f'class="ax">{knots}</text>')

    gust = " ".join(f"{'M' if h['hour'] == 0 else 'L'}{x_of(h['hour']):.1f},"
                    f"{y_of(h['gust_kn']):.1f}" for h in month.diurnal)
    wind = " ".join(f"{'M' if h['hour'] == 0 else 'L'}{x_of(h['hour']):.1f},"
                    f"{y_of(h['wind_kn']):.1f}" for h in month.diurnal)
    parts.append(f'<path d="{gust}" fill="none" stroke="var(--muted)" stroke-width="1.5" '
                 f'stroke-dasharray="4 3"/>'
                 f'<path d="{wind}" fill="none" stroke="var(--water)" stroke-width="2.5" '
                 f'stroke-linejoin="round"/>')

    for entry in month.diurnal:
        x = x_of(entry["hour"])
        parts.append(f'<rect x="{x - 8:.1f}" y="{top}" width="16" height="{floor - top}" '
                     f'fill="transparent"><title>{entry["hour"]:02d}:00 — '
                     f'{de(entry["wind_kn"])} kn from the {entry["from"]}, '
                     f'gusting {de(entry["gust_kn"])}</title></rect>')
        if entry["hour"] % 3:
            continue
        # The glyph below is drawn pointing +y, which on screen is south, and SVG rotate()
        # turns clockwise — so rotating by the FROM bearing already points the arrow the
        # way the wind is going. A north wind (000°) blows south: no rotation, arrow down.
        angle = entry["from_deg"] % 360
        parts.append(f'<g transform="translate({x:.1f},{floor + 30}) rotate({angle})">'
                     f'<path d="M0,-9 L0,7 M-4,2 L0,7 L4,2" fill="none" stroke="var(--water)" '
                     f'stroke-width="1.8" stroke-linecap="round"/></g>'
                     f'<text x="{x:.1f}" y="{floor + 19:.0f}" text-anchor="middle" '
                     f'class="ax">{entry["hour"]:02d}</text>'
                     f'<text x="{x:.1f}" y="{floor + 55:.0f}" text-anchor="middle" '
                     f'class="ax">{entry["from"]}</text>')

    parts.append(f'<text x="{PAD_L}" y="14" class="ax">knots — mean by hour, '
                 f'gust dashed · arrow points the way the wind blows</text>')
    return _svg(height, "".join(parts),
                f"Mean wind by hour of day in {month.name}, with wind direction")


# --- the berth is not the sea --------------------------------------------------------------

def compare_chart(offshore, berth, forecast_point_label: str) -> str:
    """The same code, the same limits, two positions."""
    height, top, floor = 250, 26, 196
    ceiling = 14.0
    parts = []

    def y_of(knots: float) -> float:
        return floor - min(knots / ceiling, 1.0) * (floor - top)

    for knots in (0, 5, 10):
        y = y_of(knots)
        parts.append(f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{W - PAD_R}" y2="{y:.1f}" '
                     f'stroke="var(--rule-soft)"/>'
                     f'<text x="{PAD_L - 8}" y="{y + 4:.1f}" text-anchor="end" '
                     f'class="ax">{knots}</text>')

    for i, (sea, land) in enumerate(zip(offshore, berth)):
        left, step = _band(i)
        bar_w = step * 0.30
        x = left + step / 2 - bar_w - 1.5
        for value, fill, who in ((sea.median, "var(--water)", forecast_point_label),
                                 (land.median, "var(--muted)", "the berth")):
            parts.append(
                f'<rect x="{x:.1f}" y="{y_of(value):.1f}" width="{bar_w:.1f}" '
                f'height="{floor - y_of(value):.1f}" rx="2" fill="{fill}">'
                f'<title>{sea.name}, {who}: median {de(value)} kn</title></rect>')
            x += bar_w + 3
        parts.append(f'<text x="{left + step / 2:.1f}" y="{floor + 19:.1f}" '
                     f'text-anchor="middle" class="ax">{SHORT[sea.month]}</text>'
                     f'<text x="{left + step / 2:.1f}" y="{floor + 38:.1f}" '
                     f'text-anchor="middle" class="ax gf">{de(land.gust_factor, 2)}</text>')

    parts.append(
        f'<text x="{PAD_L}" y="14" class="ax">median daylight wind, knots</text>'
        f'<text x="{PAD_L - 8}" y="{floor + 38:.1f}" text-anchor="end" class="ax gf">gust×</text>'
        f'<g transform="translate({W - PAD_R - 210},4)">'
        f'<rect width="11" height="11" rx="2" fill="var(--water)"/>'
        f'<text x="16" y="10" class="ax">open sea</text>'
        f'<rect x="86" width="11" height="11" rx="2" fill="var(--muted)"/>'
        f'<text x="102" y="10" class="ax">the berth cell</text></g>')
    return _svg(height, "".join(parts),
                "Median daylight wind at the berth grid cell against the open sea")


# --- the page ------------------------------------------------------------------------------

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
  .wrap { max-width:62rem; margin:0 auto; padding:0 1.1rem calc(4rem + env(safe-area-inset-bottom)); }
  h1,h2 { margin:0; line-height:1.2; letter-spacing:-.018em; text-wrap:balance; }
  a { color:var(--water); }
  p { margin:.7rem 0 0; }

  header { padding:2.2rem 0 1.2rem; display:flex; flex-wrap:wrap; align-items:baseline; gap:.7rem; }
  .boat { font-size:.72rem; font-weight:700; text-transform:uppercase; letter-spacing:.16em;
          color:var(--muted); width:100%; }
  h1 { font-size:1.9rem; }
  .pill { font-size:.72rem; font-weight:700; text-transform:uppercase; letter-spacing:.1em;
          padding:.25rem .6rem; border-radius:999px; border:1px solid var(--rule); color:var(--muted); }

  section { background:var(--panel); border:1px solid var(--rule); border-radius:12px;
            box-shadow:var(--shadow); padding:1.2rem 1.3rem; margin-bottom:1.1rem; }
  h2 { font-size:.74rem; font-weight:700; text-transform:uppercase; letter-spacing:.14em;
       color:var(--muted); margin-bottom:.8rem; }

  .verdict { font-size:1.5rem; font-weight:600; letter-spacing:-.02em; line-height:1.34; }
  .verdict .when { color:var(--good); }
  .sub { color:var(--ink-soft); font-size:.95rem; margin-top:.5rem; }

  /* Wide content scrolls inside its own box. The page body never scrolls sideways. */
  .chart { overflow-x:auto; margin:.5rem 0 0; }
  .chart svg { min-width:600px; display:block;
               font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,sans-serif; }
  .ax { font-size:11px; fill:var(--muted); }
  .val { font-size:11px; font-weight:700; fill:var(--ink-soft);
         font-variant-numeric:tabular-nums; }
  .gf { font-size:9.5px; opacity:.75; }
  .rowlab { font-size:12px; font-weight:700; fill:var(--ink-soft); }

  .tiles { display:grid; gap:.9rem; grid-template-columns:repeat(auto-fit,minmax(11rem,1fr)); }
  .tile { border:1px solid var(--rule); border-radius:10px; padding:.7rem .85rem; }
  .tile .k { font-size:.68rem; font-weight:700; text-transform:uppercase;
             letter-spacing:.11em; color:var(--muted); }
  .tile .v { font-size:1.5rem; font-weight:600; letter-spacing:-.02em; margin-top:.15rem; }
  .tile .n { font-size:.82rem; color:var(--ink-soft); }

  /* Month switcher, CSS only — no script has to run for the page to work. */
  .radios { position:absolute; opacity:0; pointer-events:none; }
  .tabs { display:flex; flex-wrap:wrap; gap:.3rem; margin-bottom:.4rem; }
  .tabs label { font-size:.8rem; padding:.22rem .55rem; border-radius:7px; cursor:pointer;
                border:1px solid var(--rule); color:var(--muted); user-select:none; }
  .tabs label:hover { color:var(--ink); }
  .panel { display:none; }
  .breeze { font-size:.95rem; color:var(--ink-soft); margin-top:.4rem; }

  table { border-collapse:collapse; width:100%; font-size:.86rem; min-width:640px; }
  th,td { padding:.4rem .55rem; text-align:right; border-top:1px solid var(--rule-soft);
          white-space:nowrap; }
  th { font-size:.68rem; font-weight:700; text-transform:uppercase; letter-spacing:.09em;
       color:var(--muted); border-top:0; }
  th:first-child, td:first-child { text-align:left; }
  tbody tr.on td { background:color-mix(in srgb, var(--water) 8%, transparent); }
  .num { font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
         font-variant-numeric:tabular-nums; }
  .warn { color:var(--rust); font-size:.88rem; }
  .hint { font-size:.85rem; color:var(--muted); margin:.6rem 0 0; }
  ul.notes { font-size:.9rem; color:var(--ink-soft); padding-left:1.1rem; margin:.5rem 0 0; }
  ul.notes li { margin:.35rem 0; }
"""


def _tabs_css(count: int) -> str:
    """One rule per month — cheaper than a script, and it works with JavaScript off."""
    rules = []
    for i in range(1, count + 1):
        rules.append(f"#mo{i}:checked ~ .panels .panel:nth-of-type({i}) {{ display:block; }}")
        rules.append(f"#mo{i}:checked ~ .tabs label[for=mo{i}] "
                     f"{{ background:var(--water); border-color:var(--water); "
                     f"color:var(--panel); font-weight:600; }}")
    return "\n  ".join(rules)


def render(offshore, berth, boat, default_month: int = 10) -> str:
    """The whole page, as one string. `offshore` is the planning data; `berth` is the foil."""
    limits = boat.limits.as_dict()
    good = season_mod.season(offshore)
    best = max(offshore, key=lambda m: m.full_day_pct)
    worst = min(offshore, key=lambda m: m.full_day_pct)
    oct_ = offshore[default_month - 1]
    run = _run_of(good)

    winter = [m for m in offshore if m.month in (12, 1, 2)]
    winter_sea = sum(m.fail_sea_pct for m in winter) / len(winter)
    winter_wind = sum(m.fail_wind_pct for m in winter) / len(winter)
    summer = [m for m in offshore if m.month in (6, 7, 8)]
    constancy = sum(m.constancy for m in summer) / len(summer)

    tabs = "".join(
        f'<input class="radios" type="radio" name="mo" id="mo{m.month}"'
        f'{" checked" if m.month == default_month else ""}>' for m in offshore)
    labels = "".join(f'<label for="mo{m.month}">{SHORT[m.month]}</label>' for m in offshore)
    # One ceiling for all twelve panels: switching months has to compare, not rescale.
    peak = max(h["gust_kn"] for m in offshore for h in m.diurnal)
    ceiling = math.ceil(peak / 2) * 2

    panels = "".join(
        f'<div class="panel">{diurnal_chart(m, limits, ceiling)}'
        f'<p class="breeze"><strong>{m.name}</strong> — morning wind from the '
        f'{m.morning_from}, afternoon from the {m.afternoon_from}, building '
        f'<strong>{de(m.breeze_build_kn)} kn</strong> between 07–10h and 13–17h. '
        f'Direction constancy <strong>{de(m.constancy, 2)}</strong> '
        f'({_constancy_words(m.constancy)}).</p></div>' for m in offshore)

    rows = "".join(
        f'<tr class="{"on" if m.month in good else ""}"><td>{m.name}</td>'
        f'<td class="num">{de(m.full_day_pct, 0)} %</td>'
        f'<td class="num">{de(m.full_days)}</td>'
        f'<td class="num">{de(m.good_pct, 0)} %</td>'
        f'<td class="num">{de(m.median_window_h, 0)} h</td>'
        f'<td class="num">{de(m.wind_only_pct, 0)} %</td>'
        f'<td class="num">{de(m.median)}</td>'
        f'<td class="num">{de(m.p90)}</td>'
        f'<td class="num">{de(m.strongest, 0)}</td>'
        f'<td class="num">{de(m.breeze_build_kn)}</td>'
        f'<td class="num">{m.afternoon_from}</td>'
        f'<td class="num">{de(m.air_c)}</td>'
        f'<td class="num">{de(m.sea_c) if m.sea_c is not None else "—"}</td>'
        f'<td class="num">{de(m.wet_days_pct, 0)} %</td></tr>' for m in offshore)

    forecast_label = boat.forecast_point_name or "the forecast point"
    berth_label = boat.berth_name or "the berth"
    title = f"{boat.vessel.name} — the season"
    boat_line = " · ".join(part for part in (boat.vessel.kind, forecast_label) if part) or \
        boat.vessel.name

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{title}</title>
<style>{CSS}
  {_tabs_css(len(offshore))}
</style>
</head>
<body>
<div class="wrap">

<header>
  <span class="boat">{boat_line} · ERA5 reanalysis
    {season_mod.WIND_FROM:%Y}–{season_mod.WIND_TO:%Y}</span>
  <h1>The season</h1>
  <span class="pill">{len(good)} months of 12</span>
</header>

<section>
  <h2>The answer</h2>
  <div class="verdict">The season is <span class="when">{run}</span>.
    {best.name} is the best of it at {de(best.full_day_pct, 0)} % of days;
    {worst.name} is the worst at {de(worst.full_day_pct, 0)} %.</div>
  <p class="sub">A day counts when it holds <strong>{season_mod.FULL_DAY_H} unbroken
    hours</strong> inside the limits already set in this boat's profile — up to
    {limits['max_wind_kn']:.0f} kn, gusts {limits['max_gust_kn']:.0f} kn, sea
    {de(limits['max_wave_m'])} m, dry, between {limits['daylight'][0]} and
    {limits['daylight'][1]}h. Change those limits and every number on this page moves.</p>

  <div class="tiles" style="margin-top:1.1rem">
    <div class="tile"><div class="k">{season_mod.MONTHS[default_month]}, asked for</div>
      <div class="v">{de(oct_.full_day_pct, 0)} %</div>
      <div class="n">{de(oct_.full_days)} days of the month with a six-hour window.
        Sea {de(oct_.sea_c)} °C, {de(oct_.wet_days_pct, 0)} % of days wet.</div></div>
    <div class="tile"><div class="k">What stops you in winter</div>
      <div class="v">{"the sea" if winter_sea >= winter_wind else "the wind"}</div>
      <div class="n">Dec–Feb: {de(winter_sea, 0)} % of daylight hours over
        {de(limits['max_wave_m'])} m, against {de(winter_wind, 0)} % over the wind limit.</div></div>
    <div class="tile"><div class="k">Summer afternoon breeze</div>
      <div class="v">{de(constancy, 2)}</div>
      <div class="n">Direction constancy Jun–Aug. 1.00 would be the same wind every
        afternoon; 0.00 would be no prevailing direction at all.</div></div>
  </div>
</section>

<section>
  <h2>Every month — weather you can use, and water you can swim in</h2>
  {season_chart(offshore, threshold=70.0)}
  <p class="hint">Bars: share of days holding a six-hour window inside the limits.
     Line: mean sea surface temperature. A month is worth the berth fee when both are high —
     which is why the season ends later than the weather alone suggests.</p>
</section>

<section>
  <h2>What actually stops you</h2>
  {limits_chart(offshore, limits)}
  <p class="hint">Share of daylight hours over each limit taken on its own. An hour can fail
     more than one, so these do not add up — they say <em>which</em> thing to check.</p>
</section>

<section>
  <h2>How hard it actually blows</h2>
  {wind_box(offshore, limits)}
  <p class="hint">Daylight hours only. The box is the middle half, the whisker p10–p90, the
     heavy line the median. The spread matters more than the average: two months with the
     same mean can be a steady nine knots or a calm alternating with a gale.</p>
</section>

<section>
  <h2>The sea breeze, hour by hour</h2>
  <div style="position:relative">
    {tabs}
    <div class="tabs">{labels}</div>
    <div class="panels">{panels}</div>
  </div>
  <p class="hint">Constancy is the speed-weighted resultant over the scalar sum: 1.00 would
     be the same direction every hour of every day of that month, 0.00 wind from everywhere.
     It is the number that says whether an afternoon plan is a plan or a hope.</p>
</section>

<section>
  <h2>⚠️ The berth is not the sea</h2>
  <p>A marina close to open water can still sit inside an ERA5 grid cell that is mostly
     land. Point an archive API at the berth's own coordinates — the obvious thing to do —
     and it can return well under the wind the open sea nearby is making, and gusts too
     large, because it is modelling turbulence over hot ground. Every other number on this
     page is taken at {forecast_label}, {boat.forecast_point[0]}°N {boat.forecast_point[1]}°E
     — the profile's own forecast point, chosen to be open water.</p>
  {compare_chart(offshore, berth, forecast_label)}
  <p class="hint">Small figures under each month are the {berth_label} cell's gust factor —
     mean gust over mean wind. About 1.4 is normal over open water; a berth cell running
     much higher than that in summer is land physics, not weather. It is the error that
     matters most, because it trips the gust limit on days that were never windy.</p>
</section>

<section>
  <h2>The numbers</h2>
  <div class="chart">
  <table>
    <thead><tr>
      <th>Month</th><th>≥6 h</th><th>such<br>days</th><th>≥3 h</th><th>typical<br>window</th>
      <th>≥3 h<br>wind only</th><th>median<br>kn</th><th>p90<br>kn</th><th>max<br>kn</th>
      <th>breeze<br>build</th><th>pm<br>from</th><th>air<br>°C</th><th>sea<br>°C</th>
      <th>wet<br>days</th>
    </tr></thead>
    <tbody>{rows}</tbody>
  </table>
  </div>
  <p class="hint">Shaded rows are the season. <strong>≥3 h wind only</strong> is the same
     test over the full wind record with the sea state removed — where it disagrees loudly
     with the shorter wave-backed column, the shorter column is the one to distrust.</p>
</section>

<section>
  <h2>What this is not</h2>
  <ul class="notes">
    <li>ERA5 is a <strong>~28 km reanalysis</strong>. It has no thunderstorm in it, no gust
        front, and no idea about local shelter the coastline actually gives. It describes
        the region well over decades. It does not describe Tuesday — for that, a real
        forecast.</li>
    <li>The full verdict rests on however many years of wave data are available since
        {WAVE_START:%Y} ({season_mod.WIND_TO.year - WAVE_START.year + 1} seasons); the wind
        columns rest on the full ten-year window. The wave archive does not go back
        further.</li>
    <li>The limits are <strong>one skipper's comfort</strong>, not the boat's capability —
        set in the profile, deliberately conservative.</li>
    <li>Sea temperature is a model surface value, not a thermometer in the water.</li>
    <li>Nothing here is a forecast, and nothing here is a route. No land, no depth, no
        restricted areas.</li>
  </ul>
</section>

</div>
</body>
</html>
"""


def _run_of(months: list[int]) -> str:
    """`May to November` when the good months are contiguous, a list when they are not."""
    if not months:
        return "nowhere near a full year"
    names = season_mod.MONTHS
    contiguous = months == list(range(months[0], months[-1] + 1))
    if contiguous and len(months) > 2:
        return f"{names[months[0]]} to {names[months[-1]]}"
    return ", ".join(names[m] for m in months)


def _constancy_words(value: float) -> str:
    if value >= 0.8:
        return "the same wind nearly every day"
    if value >= 0.5:
        return "a clear prevailing direction"
    if value >= 0.3:
        return "a weak preference"
    return "no prevailing direction at all"
