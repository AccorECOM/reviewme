"""Tests for the one-line readings under every chart and table (`dashboard/readings.py`).

What matters: the words say the right direction (flat within 5 %), name the right item,
come in both languages, and every chart or table of the page gets exactly one.

Run: `pytest tests/`.
"""
import os
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard_fixture import NOW, make_ledger

from reviewme.dashboard import StatsSettings, build
from reviewme.dashboard import charts as C
from reviewme.dashboard import readings as R
from reviewme.projects import STATS_SECTIONS

ALL = StatsSettings(sections=STATS_SECTIONS)


def test_trend_up_down_flat_and_too_few():
    up = [("2026-Q1", 27.8), ("2026-Q2", 50.0), ("2026-Q3", 42.0)]
    assert R.trend(up, C.hours, "en") == "Up from 27.8 h to 42 h since 2026-Q1; highest in 2026-Q2."
    assert R.trend(up, C.hours, "fr") == ("En hausse de 27.8 h à 42 h depuis 2026-Q1 ; "
                                          "maximum en 2026-Q2.")
    down = [("W1", 3.0), ("W2", 1.0)]
    assert R.trend(down, C.money, "en") == "Down from 3.00 $ to 1.00 $ since W1; highest in W1."
    flat = [("W1", 100.0), ("W2", 80.0), ("W3", 104.0)]        # +4 %: flat
    assert R.trend(flat, C.num, "en") == "Flat around 104 since W1; highest in W3."
    assert R.trend([("W1", 0), ("W2", 0)], C.num, "en") == "Zero in every period since W1."
    assert R.trend([("W1", 5)], C.num, "en") == "Not enough data yet."
    assert R.trend([], C.num, "fr") == "Pas encore assez de données."


def test_trend_leaves_the_running_period_out():
    pts = [("Sprint 16", 108.0), ("Sprint 17", 44.0), ("Sprint 18", 84.0), ("Sprint 19", 8.0)]
    # Sprint 19 is still running: its low value is not a drop, and it is named as such.
    assert R.trend(pts, C.money, "en", current="Sprint 19") == (
        "Down from 108.00 $ to 84.00 $ since Sprint 16; highest in Sprint 16. "
        "Sprint 19 is still running.")
    assert R.trend(pts, C.money, "fr", current="Sprint 19") == (
        "En baisse de 108.00 $ à 84.00 $ depuis Sprint 16 ; maximum en Sprint 16. "
        "Sprint 19 en cours.")
    # The running period is not the last point (no run yet this sprint): nothing changes.
    assert R.trend(pts, C.money, "en", current="Sprint 20") == (
        "Down from 108.00 $ to 8.00 $ since Sprint 16; highest in Sprint 16.")
    # One closed period plus the running one: not a trend yet.
    assert R.trend(pts[:2], C.money, "en", current="Sprint 17") == (
        "Not enough data yet. Sprint 17 is still running.")
    assert (R.direction(100, 105), R.direction(100, 106), R.direction(0, 1)) == (
        "flat", "up", "up")


def test_compare_top_share():
    assert R.compare("Median hours open to merge", "PRs with v0", 55, "those without", 41,
                     C.hours, "en") == \
        "Median hours open to merge: 55 h for PRs with v0 against 41 h for those without."
    assert R.compare("Merge", "a", None, "b", 3, C.hours, "en") == "Not enough data yet."
    days = [("Mon", 3), ("Tue", 9), ("Sat", 0)]
    assert R.top("Runs", days, C.num, "en") == "Runs: highest Tue (9), lowest Sat (0)."
    assert R.top("Passages", days, C.num, "fr") == \
        "Passages : le plus haut Tue (9), le plus bas Sat (0)."
    assert R.top("Runs", [("tech", 2)], C.num, "en") == "Runs: only tech (2)."
    assert R.top("Runs", [("a", 0)], C.num, "en") == "Nothing recorded yet."
    assert R.top("Gap", [("x", 0.25, "+25 %"), ("y", 0.1, "−10 %")], C.num, "en") == \
        "Gap: highest x (+25 %), lowest y (−10 %)."
    assert R.top("Runs", [("a", 2), ("b", 2)], C.num, "en") == "Runs: 2 for each."
    assert R.top("Gap", [("x", 0.3, "−30 %"), ("y", 0.1, "+10 %")], C.num, "en", size=True) \
        == "Gap: largest x (−30 %), smallest y (+10 %)."
    assert R.share("Runs with an incident", 1, 6, "en") == "Runs with an incident: 17 % (1 of 6)."


class _Cards(HTMLParser):
    """Per `.card`: does it hold a chart or a table, and how many `.reading` lines."""

    def __init__(self):
        super().__init__()
        self.stack: list[dict | None] = []
        self.cards: list[dict] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = (a.get("class") or "").split()
        if tag == "div":
            card = {"picture": False, "readings": 0, "text": []} if "card" in cls else None
            if card is not None:
                self.cards.append(card)
            self.stack.append(card)
        open_cards = [c for c in self.stack if c]
        if not open_cards:
            return
        if tag in ("svg", "table") or (tag == "div" and "hb" in cls):
            open_cards[-1]["picture"] = True
        if tag == "p" and "reading" in cls:
            open_cards[-1]["readings"] += 1

    def handle_endtag(self, tag):
        if tag == "div" and self.stack:
            self.stack.pop()


@pytest.mark.parametrize("language", ["en", "fr"])
def test_every_chart_and_table_has_exactly_one_reading(language):
    with tempfile.TemporaryDirectory() as tmp:
        page = build(make_ledger(Path(tmp) / "l"), ALL, language, now=NOW)[0]
    p = _Cards()
    p.feed(page)
    pictures = [c for c in p.cards if c["picture"]]
    assert len(pictures) > 30
    assert all(c["readings"] == 1 for c in pictures)
    assert all(c["readings"] == 0 for c in p.cards if not c["picture"])
    import re
    for text in re.findall(r'<p class="reading">(.*?)</p>', page):
        assert len(re.findall(r"[^\W\d]+", text)) < 20, text


def test_readings_on_the_fixture():
    with tempfile.TemporaryDirectory() as tmp:
        page = build(make_ledger(Path(tmp) / "l"), ALL, "en", now=NOW)[0]
        fr = build(make_ledger(Path(tmp) / "f"), ALL, "fr", now=NOW)[0]
    # History before/after (comparison), a history trend, the ROI control group.
    assert "Median hours open to merge: 56 h for before against 36 h for after." in page
    # Trends: up (merge hours per quarter), down (cost per week).
    # The running quarter (2026-Q4, 48 h) is left out of the comparison and named.
    assert "Up from 36 h to 72 h since 2025-Q1; highest in 2025-Q2. 2026-Q4 is still running." in page
    assert "Down from 1.20 $ to 0.70 $ since 2026-W41; highest in 2026-W41." in page
    # Top item, and the ROI gaps by size.
    assert "Cost: highest tech (1.80 $), lowest us (0.40 $)." in page
    assert "Gap: largest Human comments per PR (−75 %), smallest Median hours open to " \
           "merge (+33 %)." in page
    assert '<p class="reading">Median hours open to merge: 64 h for reviewed against ' \
           '48 h for not reviewed.</p>' in page
    assert "Heures médianes ouverture → merge : 56 h pour avant contre 36 h pour après." in fr
