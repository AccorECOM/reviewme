"""Tests for `reviewme demo-data` (`reviewme/demo.py`).

What matters: the same seed and date give the same ledger (a page can then be diffed
after a change), and the dashboard built from it has something real in every section:
the demo exists to judge the pages, an empty card would judge nothing.

Run: `pytest tests/`.
"""
import json
import os
import re
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reviewme.demo import build_demo, generate
from reviewme.export import load

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


def _ledger(seed: int) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        generate(Path(tmp) / "l", seed=seed, now=NOW)
        return json.dumps(load(Path(tmp) / "l"), sort_keys=True)


def test_same_seed_same_ledger():
    a, b = _ledger(3), _ledger(3)
    assert a == b
    assert a != _ledger(4)
    assert "demo" in a and "@" not in a          # fictional logins, no e-mail


def test_dashboard_from_the_demo_has_every_section_filled():
    from reviewme.dashboard import build
    from reviewme.dashboard.settings import StatsSettings
    from reviewme.demo import MAINTAINER, PROJECT_TOML
    from reviewme.projects import load_project_settings

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        r = build_demo(out, seed=1, now=NOW, language="fr")
        page_fr = Path(r["page"]).read_text(encoding="utf-8")
        home = out / "home" / "projects" / "demo"
        home.mkdir(parents=True)
        (home / "project.toml").write_text(PROJECT_TOML.format(
            language="en", days=90, maintainer=MAINTAINER), encoding="utf-8")
        s = StatsSettings.from_project(load_project_settings("demo", out / "home" / "projects"))
        page, res = build(out / "ledger", s, "en", out / "stats.json", now=NOW)
    assert 60 <= r["prs_reviewed"] <= 85 and 30 <= r["prs_unreviewed"] <= 50
    assert len(s.eras) == 3 and s.eras[0].bot_login == MAINTAINER
    for page_ in (page, page_fr):
        sections = re.findall(r'<section id="([^"]+)"', page_)
        assert len(sections) == 9
        for sid in sections:
            body = page_[page_.index(f'<section id="{sid}"'):]
            body = body[:body.index("</section>")]
            assert 'class="reading"' in body, sid              # a picture with its reading
            assert "could not be computed" not in body and "n'a pas pu" not in body
        # The only empty-state line: the triage titles hidden without include_text.
        empties = re.findall(r'<p class="empty">(.*?)</p>', page_)
        assert len(empties) == 1 and "include_text" in empties[0]
    q, summary = res["quality"], res["summary"]
    rates = {k: v["followed_rate"] for k, v in q["by_reviewer"].items()}
    assert rates["architect"] > rates["tech"] > rates["i18n"]
    assert q["contested_rules"][0]["contested"] == 4 and len(q["contested_rules"]) == 1
    assert (summary["blockers_posted"], summary["blockers_fixed_before_merge"]) == (3, 2)
    assert 0.6 <= res["cost"]["cache_ratio"] <= 0.8 and res["cost"]["max_turns_hits"] == 2
    # The last era starts after the generation date: ROI compares with the last one
    # that has PRs, and says which.
    assert res["roi"]["over_time"]["after_name"] == "pause"
    assert res["roi"]["control_group"]["available"]
    assert {e["era"] for e in res["history"]["per_era"]} >= {"prototype", "ReviewMe"}
