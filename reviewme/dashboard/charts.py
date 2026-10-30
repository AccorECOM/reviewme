"""Palette, CSS and the few chart forms the dashboard uses. Inline SVG/HTML, no library.

Form rules (shared with the legacy `report.py`):
- totals are **numbers** (tiles), not charts: a one-bar histogram teaches nothing;
- a chart carries ONE series, so it needs no legend and no categorical color: the title
  names what is plotted. Two measures of different scale (dollars, findings) are two
  charts, never one chart with two axes;
- every mark has a native tooltip (`<title>`): hover works without JavaScript;
- text wears ink colors, never the series color.

Palette validated on the cream background (lightness band, chroma, color-blind
separation, normal-vision floor, contrast). Do not change it without revalidating.

Small screens: a chart keeps a minimum width and scrolls INSIDE its card, a table too.
The page itself never scrolls sideways.
"""
from __future__ import annotations

import html
from collections.abc import Callable, Sequence

ACCENT = "#7C3AED"          # violet — the single series color
ENCRE = "#111111"           # ink
MUET = "#55504C"            # muted ink (axes, secondary text, config markers)
FOND = "#FAF4F0"            # cream background
CARTE = "#FFFFFF"           # card surface
GRILLE = "#DDD3CC"          # grid lines and table rules
TETE = "#F3EAE4"            # table header

CSS = """
*,*::before,*::after{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;padding:28px;background:%(fond)s;color:%(encre)s;
  font:14px/1.55 -apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",sans-serif}
.wrap{max-width:1180px;margin:0 auto}
h1{font-size:clamp(26px,4vw,36px);font-weight:900;letter-spacing:-.02em;margin:0 0 4px}
h2{font-size:22px;font-weight:900;letter-spacing:-.01em;margin:0 0 14px}
h3{font-size:15px;font-weight:800;margin:26px 0 10px}
.sub{color:%(muet)s;margin:0 0 24px}
.sub span{white-space:nowrap}
.layout{display:grid;grid-template-columns:190px minmax(0,1fr);gap:28px;align-items:start}
nav ul{list-style:none;margin:0;padding:0;position:sticky;top:20px}
nav a{display:block;padding:7px 12px;margin-bottom:6px;color:%(encre)s;text-decoration:none;
  font-weight:700;border:2px solid transparent;border-radius:3px}
nav a:hover{border-color:%(encre)s}
nav a.on{background:%(carte)s;border-color:%(encre)s;box-shadow:3px 3px 0 %(encre)s}
section{margin-bottom:44px;min-width:0}
.js section{display:none}
.js section.on{display:block}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.tile{background:%(carte)s;border:2px solid %(encre)s;border-radius:3px;
  box-shadow:4px 4px 0 %(encre)s;padding:14px 16px;min-width:0}
.tile .n{font-size:26px;font-weight:900;font-variant-numeric:tabular-nums;line-height:1.1;
  overflow-wrap:anywhere}
.tile .l{font-size:10.5px;font-weight:800;text-transform:uppercase;letter-spacing:.09em;
  color:%(muet)s;margin-top:6px}
.tile .s{font-size:12px;color:%(muet)s;margin-top:4px}
.card{background:%(carte)s;border:2px solid %(encre)s;border-radius:3px;
  box-shadow:4px 4px 0 %(encre)s;padding:16px;overflow-x:auto;margin-bottom:8px}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px}
.grid2>div{min-width:0}
.chart svg{display:block}
.note{font-size:12px;color:%(muet)s;margin:6px 0 0}
.note a{color:%(encre)s;font-weight:700}
h3 .tag{display:inline-block;font-size:10.5px;font-weight:800;text-transform:uppercase;
  letter-spacing:.09em;color:%(muet)s;border:1.5px solid %(muet)s;border-radius:3px;
  padding:0 6px;margin-left:6px;vertical-align:2px;white-space:nowrap}
.empty{color:%(muet)s;margin:0}
.reading{margin:10px 0 0;padding:2px 0 2px 10px;border-left:3px solid %(accent)s;
  font-size:13px;color:%(muet)s}
.debrief{margin:0;padding-left:18px;line-height:1.5}
.debrief li{margin:2px 0}
table{border-collapse:collapse;width:100%%;font-size:13px}
th{text-align:left;font-size:10.5px;text-transform:uppercase;letter-spacing:.09em;
  background:%(tete)s;padding:8px 10px;border-bottom:2px solid %(encre)s;white-space:nowrap}
td{padding:7px 10px;border-bottom:1px solid %(grille)s;font-variant-numeric:tabular-nums;
  white-space:nowrap}
th.r,td.r{text-align:right}
.hb{display:grid;grid-template-columns:minmax(80px,180px) minmax(0,1fr);gap:6px 12px;
  align-items:center;font-size:12.5px}
.hb .k{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hb .b{display:flex;align-items:center;gap:8px;min-width:0}
.hb .b i{display:block;height:16px;border-radius:0 4px 4px 0;background:%(accent)s;
  flex:none;min-width:2px}
.hb .b em{font-style:normal;color:%(muet)s;font-variant-numeric:tabular-nums;
  white-space:nowrap}
.hof{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px}
.hof .tile .t{font-size:15px;font-weight:900}
.hof .tile .n{font-size:22px}
.hof .tile .who{font-size:18px;font-weight:800;margin-top:8px;overflow-wrap:anywhere}
.foot{margin-top:32px;padding-top:14px;border-top:2px solid %(encre)s;
  font-size:11.5px;color:%(muet)s}
@media (max-width:760px){
  body{padding:16px}
  .layout{grid-template-columns:minmax(0,1fr);gap:16px}
  nav ul{position:static;display:flex;gap:6px;overflow-x:auto;padding-bottom:6px}
  nav a{white-space:nowrap;margin:0}
  .grid2{grid-template-columns:minmax(0,1fr)}
  .tiles{grid-template-columns:repeat(auto-fit,minmax(140px,1fr))}
  .hb{grid-template-columns:minmax(60px,110px) minmax(0,1fr)}
}
""" % {"fond": FOND, "encre": ENCRE, "muet": MUET, "carte": CARTE, "grille": GRILLE,
       "tete": TETE, "accent": ACCENT}


#: viewBox widths: the SVG scales to its card, so these are chosen so 12 px text stays
#: about 12 px in a full-width card (WIDE) and in a half-width one (HALF).
WIDE, HALF = 900, 460


def esc(value) -> str:
    return html.escape(str(value))


# --------------------------------------------------------------------------- formats

def money(x: float | None, digits: int = 2) -> str:
    return "—" if x is None else f"{x:.{digits}f} $"


def pct(rate: float | None) -> str:
    return "—" if rate is None else f"{round(rate * 100)} %"


def ratio(n: float, d: float) -> float | None:
    return round(n / d, 4) if d else None


def num(x: float | None, digits: int = 1) -> str:
    if x is None:
        return "—"
    return str(int(x)) if float(x).is_integer() else f"{x:.{digits}f}"


def hours(x: float | None) -> str:
    """A delay in hours, the same unit at every size: `h` reads alike in both languages."""
    return "—" if x is None else f"{num(round(x, 1))} h"


# --------------------------------------------------------------------------- blocks

def empty(text: str) -> str:
    return f'<p class="empty">{esc(text)}</p>'


def tile(value: str, label: str, sub: str = "") -> str:
    s = f'<div class="s">{esc(sub)}</div>' if sub else ""
    return (f'<div class="tile"><div class="n">{esc(value)}</div>'
            f'<div class="l">{esc(label)}</div>{s}</div>')


def tiles(items: Sequence[tuple]) -> str:
    """`(value, label)` or `(value, label, sub)` -> a row of number tiles."""
    return '<div class="tiles">' + "".join(tile(*i) for i in items) + "</div>"


#: What makes a card a picture that needs a reading (`readings.py`): a chart or a table.
_PICTURES = ("<svg", "<table", 'class="hb"')


def card(body: str, title: str = "", note: str = "", reading: str = "") -> str:
    """A titled card. `reading` (one plain sentence on what the picture shows) sits right
    under the picture, before the methodological `note`, and only when the body holds a
    chart or a table: under an empty state it would only repeat "no data"."""
    h = f"<h3>{esc(title)}</h3>" if title else ""
    n = f'<p class="note">{esc(note)}</p>' if note else ""
    r = f'<p class="reading">{esc(reading)}</p>' \
        if reading and any(p in body for p in _PICTURES) else ""
    return f'{h}<div class="card">{body}{r}{n}</div>'


def table(headers: Sequence[str], rows: Sequence[Sequence], *, right: Sequence[int] = (),
          empty_text: str = "") -> str:
    """Plain table. `right` = indexes of numeric columns (right-aligned)."""
    if not rows:
        return empty(empty_text) if empty_text else ""
    r = set(right)
    head = "".join(f'<th{" class=r" if i in r else ""}>{esc(h)}</th>'
                   for i, h in enumerate(headers))
    body = "".join("<tr>" + "".join(f'<td{" class=r" if i in r else ""}>{esc(c)}</td>'
                                    for i, c in enumerate(row)) + "</tr>" for row in rows)
    return f"<table><tr>{head}</tr>{body}</table>"


def hbars(data: Sequence[tuple[str, float, str]], empty_text: str = "No data yet.") -> str:
    """Horizontal bars in HTML, not SVG: labels stay readable at any width, the value is
    written at the end of the bar. One series: one color, no legend."""
    if not data:
        return empty(empty_text)
    maxi = max(v for _, v, _ in data) or 1
    out = ['<div class="hb">']
    for label, val, text in data:
        w = max(0.5, 78 * val / maxi)          # 78 %: room left for the value text
        out.append(f'<div class="k" title="{esc(label)}">{esc(label)}</div>'
                   f'<div class="b"><i style="width:{w:.1f}%" title="{esc(label)} — '
                   f'{esc(text)}"></i><em>{esc(text)}</em></div>')
    out.append("</div>")
    return "".join(out)


def _scale(values: Sequence[float]) -> float:
    """Top of the axis. Counts get a multiple of 3, so the three steps stay whole numbers
    (a "0.7 runs" tick is noise)."""
    maxi = max(values, default=0) or 1
    if all(float(v).is_integer() for v in values):
        return max(3, -(-int(maxi) // 3) * 3)
    return maxi


def _axis(maxi: float, fmt: Callable[[float], str], x0: float, x1: float, top: float,
          plot_h: float) -> list[str]:
    out = []
    for i in range(4):                       # three steps, deliberately subtle
        y = top + plot_h * i / 3
        out.append(f'<line x1="{x0}" y1="{y:.1f}" x2="{x1}" y2="{y:.1f}" '
                   f'stroke="{GRILLE}" stroke-width="1"/>')
        out.append(f'<text x="{x0 - 8}" y="{y + 4:.1f}" text-anchor="end" font-size="12" '
                   f'fill="{MUET}">{esc(fmt(maxi * (3 - i) / 3))}</text>')
    return out


def _open_svg(width: int, height: int) -> str:
    """The SVG scales with its card, down to ~60 % of its viewBox: below that, a wide chart
    scrolls inside its card rather than shrinking its 12 px labels to unreadable."""
    return (f'<div class="chart"><svg viewBox="0 0 {width} {height}" width="100%" role="img" '
            f'style="font-family:inherit;min-width:{max(280, int(width * 0.6))}px">')


def _tick_every(n: int, room: int = 14) -> int:
    return max(1, -(-n // room))


def _marker_lines(markers: Sequence[int], xs: list[float], top: float, bottom: float,
                  label: str, caption: str = "", captions: Sequence[str] = ()) -> list[str]:
    """Settings changes: a dashed ink line, not a series (hence no legend), with a note.
    `caption` is written next to the line, for a marker that must read without hovering
    (the day the bot starts); it flips to the left of the line past mid-chart.
    `captions`, one per marker, names each line (eras) and overrides `caption` and
    `label`; with several, they alternate between two rows so that two eras a quarter
    apart do not print over each other."""
    out = []
    for n, i in enumerate(markers):
        if captions:
            label = caption = captions[n] if n < len(captions) else ""
        if 0 <= i < len(xs):
            x = xs[i] - (xs[1] - xs[0]) / 2 if len(xs) > 1 else xs[i]
            out.append(f'<line x1="{x:.1f}" y1="{top - 4}" x2="{x:.1f}" y2="{bottom}" '
                       f'stroke="{MUET}" stroke-width="1.5" stroke-dasharray="4 4">'
                       f'<title>{esc(label)}</title></line>')
            if caption:
                right = x < (xs[0] + xs[-1]) / 2
                y = top - 12 - (14 if len(captions) > 1 and n % 2 == 0 else 0)
                out.append(f'<text x="{x + (6 if right else -6):.1f}" y="{y}" '
                           f'text-anchor="{"start" if right else "end"}" font-size="12" '
                           f'fill="{MUET}">{esc(caption)}</text>')
    return out


def bars(data: Sequence[tuple[str, float]], fmt: Callable[[float], str] = num, *,
         width: int = WIDE, height: int = 200, markers: Sequence[int] = (), marker_label: str = "",
         empty_text: str = "No data yet.") -> str:
    """Vertical bars, a single series. 4 px rounded tops, a gutter between fills."""
    if not data:
        return empty(empty_text)
    left, bottom, top = 56, 30, 16
    plot_h = height - bottom - top
    maxi = _scale([v for _, v in data])
    step = (width - left - 12) / len(data)
    bar = max(4, min(40, step - 4))
    every = _tick_every(len(data), max(4, width // 56))
    out = [_open_svg(width, height)]
    out += _axis(maxi, fmt, left, width - 12, top, plot_h)
    xs = []
    for i, (label, val) in enumerate(data):
        h = max(1.5, plot_h * val / maxi) if val else 0
        x = left + i * step + (step - bar) / 2
        xs.append(x + bar / 2)
        y = top + plot_h - h
        if h:
            r = min(4, bar / 2, h)
            out.append(f'<path d="M{x:.1f},{top + plot_h:.1f} V{y + r:.1f} '
                       f'Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} H{x + bar - r:.1f} '
                       f'Q{x + bar:.1f},{y:.1f} {x + bar:.1f},{y + r:.1f} '
                       f'V{top + plot_h:.1f} Z" fill="{ACCENT}"/>')
        # Hit target the full column height: easier to hover than a 2 px bar.
        out.append(f'<rect x="{left + i * step:.1f}" y="{top}" width="{step:.1f}" '
                   f'height="{plot_h}" fill="transparent"><title>{esc(label)} — '
                   f'{esc(fmt(val))}</title></rect>')
        if i % every == 0:
            out.append(f'<text x="{x + bar / 2:.1f}" y="{height - 10}" text-anchor="middle" '
                       f'font-size="12" fill="{MUET}">{esc(label)}</text>')
    out += _marker_lines(markers, xs, top, top + plot_h, marker_label)
    out.append("</svg></div>")
    return "".join(out)


def line(data: Sequence[tuple[str, float]], fmt: Callable[[float], str] = num, *,
         width: int = WIDE, height: int = 200, markers: Sequence[int] = (), marker_label: str = "",
         marker_caption: str = "", marker_captions: Sequence[str] = (),
         empty_text: str = "No data yet.") -> str:
    """One series over the periods: 2 px line, 8 px markers, a tooltip per point."""
    if not data:
        return empty(empty_text)
    left, bottom, top = 56, 30, 16
    if (marker_caption or marker_captions) and markers:
        top = 34                             # room for the caption above the plot
        if len(marker_captions) > 1:
            top = 48                         # two rows of captions
    plot_h = height - bottom - top
    maxi = _scale([v for _, v in data])
    n = len(data)
    span = width - left - 28
    xs = [left + 14 + (span * i / (n - 1) if n > 1 else span / 2) for i in range(n)]
    ys = [top + plot_h - plot_h * v / maxi for _, v in data]
    every = _tick_every(n, max(4, width // 90))
    out = [_open_svg(width, height)]
    out += _axis(maxi, fmt, left, width - 12, top, plot_h)
    out += _marker_lines(markers, xs, top, top + plot_h, marker_label, marker_caption,
                         marker_captions)
    if n > 1:
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys, strict=True))
        out.append(f'<polyline points="{pts}" fill="none" stroke="{ACCENT}" stroke-width="2" '
                   f'stroke-linejoin="round" stroke-linecap="round"/>')
    for i, ((label, val), x, y) in enumerate(zip(data, xs, ys, strict=True)):
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{ACCENT}" '
                   f'stroke="{CARTE}" stroke-width="2"/>')
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="12" fill="transparent">'
                   f'<title>{esc(label)} — {esc(fmt(val))}</title></circle>')
        if i % every == 0:
            # The last label ends at its point: centred, it would run off the chart.
            anchor = "end" if i == n - 1 and n > 1 else "middle"
            tx = x + 10 if anchor == "end" else x
            out.append(f'<text x="{tx:.1f}" y="{height - 10}" text-anchor="{anchor}" '
                       f'font-size="12" fill="{MUET}">{esc(label)}</text>')
    out.append("</svg></div>")
    return "".join(out)
