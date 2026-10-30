"""One-line readings: what a chart or a table shows, in plain words.

The pages are dense; a reader skimming them wants the gist of each picture without
decoding axes. Each reading is generated from the numbers the section already drew,
deterministic (same ledger, same words), under 20 words, numbers formatted like the
tiles. Four generic readers cover every picture of the dashboard:

- `trend`: a series over periods, first vs last value, a direction word, the peak;
  within 5 % of each other the series reads as flat (noise is not a trend);
- `compare`: one metric for two groups (reviewed vs not, before vs after);
- `top`: the highest and lowest item of a distribution or a ranked table;
- `share`: a part of a whole.

A reading never quotes a finding, a reply or a title: only labels already on the page.
`charts.card(..., reading=)` places it under the picture, styled apart from the
methodological notes (`.reading` vs `.note`).
"""
from __future__ import annotations

from collections.abc import Callable, Sequence

from . import charts as C
from .i18n import texts

#: Relative change under which first and last values read as "flat".
FLAT = 0.05

_T = {
    "en": {
        "few": "Not enough data yet.", "nothing": "Nothing recorded yet.",
        "up": "Up from {a} to {b} since {p}; highest in {peak}.",
        "down": "Down from {a} to {b} since {p}; highest in {peak}.",
        "flat": "Flat around {b} since {p}; highest in {peak}.",
        "zero": "Zero in every period since {p}.",
        "running": " {p} is still running.",
        "compare": "{m}: {va} for {la} against {vb} for {lb}.",
        "top": "{what}: highest {l} ({v}), lowest {l2} ({v2}).",
        "only": "{what}: only {l} ({v}).",
        "size": "{what}: largest {l} ({v}), smallest {l2} ({v2}).",
        "even": "{what}: {v} for each.",
        "share": "{what}: {pct} ({n} of {total}).",
        "versus": "{a} vs {b}",
    },
    "fr": {
        "few": "Pas encore assez de données.", "nothing": "Rien d'enregistré pour l'instant.",
        "up": "En hausse de {a} à {b} depuis {p} ; maximum en {peak}.",
        "down": "En baisse de {a} à {b} depuis {p} ; maximum en {peak}.",
        "flat": "Stable autour de {b} depuis {p} ; maximum en {peak}.",
        "zero": "Nul sur chaque période depuis {p}.",
        "running": " {p} en cours.",
        "compare": "{m} : {va} pour {la} contre {vb} pour {lb}.",
        "top": "{what} : le plus haut {l} ({v}), le plus bas {l2} ({v2}).",
        "only": "{what} : seulement {l} ({v}).",
        "size": "{what} : le plus grand {l} ({v}), le plus petit {l2} ({v2}).",
        "even": "{what} : {v} pour chacun.",
        "share": "{what} : {pct} ({n} sur {total}).",
        "versus": "{a} contre {b}",
    },
}

Fmt = Callable[[float], str]


def few(language: str) -> str:
    return texts(_T, language)["few"]


def direction(a: float, b: float) -> str:
    """`up`, `down` or `flat`: flat when the change is within `FLAT` of the larger."""
    if abs(b - a) <= FLAT * max(abs(a), abs(b)):
        return "flat"
    return "up" if b > a else "down"


def trend(points: Sequence[tuple[str, float]], fmt: Fmt, language: str, *,
          current: str | None = None) -> str:
    """First vs last value of a series, its direction and its peak period.

    `current` is the label of the period still running (this week, this sprint, this
    quarter): a partial period always reads as a collapse, so when it is the last point
    it is left out of the comparison and named as running instead."""
    T = texts(_T, language)
    pts = [(p, v) for p, v in points if v is not None]
    tail = ""
    if current and pts and pts[-1][0] == current:
        pts = pts[:-1]
        tail = T["running"].format(p=current)
    if len(pts) < 2:
        return T["few"] + tail
    (p0, a), (_, b) = pts[0], pts[-1]
    if all(v == 0 for _, v in pts):
        return T["zero"].format(p=p0) + tail
    peak = max(pts, key=lambda pv: pv[1])[0]        # first period at the maximum
    return T[direction(a, b)].format(a=fmt(a), b=fmt(b), p=p0, peak=peak) + tail


def versus(a: float | None, b: float | None, fmt: Fmt, language: str) -> str:
    return texts(_T, language)["versus"].format(a=fmt(a), b=fmt(b))


def compare(metric: str, label_a: str, a: float | None, label_b: str, b: float | None,
            fmt: Fmt, language: str) -> str:
    """One metric for two groups; not enough data when either side is unknown."""
    T = texts(_T, language)
    if a is None or b is None:
        return T["few"]
    return T["compare"].format(m=metric, va=fmt(a), la=label_a, vb=fmt(b), lb=label_b)


def top(what: str, items: Sequence[tuple], fmt: Fmt, language: str, *,
        size: bool = False) -> str:
    """Highest and lowest of `(label, value)` or `(label, value, text)` items, the text
    (when given) being how the picture prints the value. Ties keep the first listed;
    all equal reads "N for each" rather than naming one row twice. `size`: the values
    are magnitudes of signed gaps, so "largest / smallest", not "highest / lowest"."""
    T = texts(_T, language)
    known = [i for i in items if i[1] is not None]
    if not known or all(not i[1] for i in known):
        return T["nothing"]

    def shown(i: tuple) -> str:
        return i[2] if len(i) > 2 else fmt(i[1])

    hi = max(known, key=lambda i: i[1])
    if len(known) == 1:
        return T["only"].format(what=what, l=hi[0], v=shown(hi))
    lo = min(known, key=lambda i: i[1])
    if hi[1] == lo[1]:
        return T["even"].format(what=what, v=shown(hi))
    return T["size" if size else "top"].format(what=what, l=hi[0], v=shown(hi), l2=lo[0], v2=shown(lo))


def share(what: str, n: int, total: int, language: str) -> str:
    T = texts(_T, language)
    if not total:
        return T["nothing"]
    return T["share"].format(what=what, pct=C.pct(C.ratio(n, total)), n=n, total=total)
