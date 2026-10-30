"""Tests for `[stats]` in `project.toml` and the dashboard settings built from it.

What matters: a project that says nothing gets the default page, and a typo (unknown
section, unknown key, bad value) fails at load time instead of silently hiding a page.

Run: `pytest tests/`.
"""
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reviewme.dashboard.settings import StatsSettings
from reviewme.projects import (
    STATS_DEFAULT_SECTIONS,
    STATS_SECTIONS,
    ProjectConfigError,
    load_project,
)


def _load(toml: str):
    with tempfile.TemporaryDirectory() as tmp:
        pdir = Path(tmp) / "app"
        (pdir / "reviewers" / "tech").mkdir(parents=True)
        (pdir / "reviewers" / "tech" / "system.md").write_text("persona", encoding="utf-8")
        (pdir / "project.toml").write_text(toml, encoding="utf-8")
        return load_project("app", Path(tmp))


def test_defaults_when_absent():
    p = _load("")
    s = StatsSettings.from_project(p)
    assert s.sections == STATS_DEFAULT_SECTIONS
    assert "roi" in s.sections and "history" in s.sections
    assert s.roi == () and p.stats_roi_currency == ""
    assert s.bot_since is None
    assert s.names == "login" and s.period == "week" and s.window_delta == timedelta(days=90)
    assert s.judgment_of("+1") == "useful" and s.judgment_of("rocket") == "saved_me"
    assert s.judgment_of("eyes") is None
    assert s.project == "app"
    assert p.stats_control_group is True
    assert s.project_dir == p.directory and s.project_dir is not None
    assert StatsSettings.default() == StatsSettings()


def test_full_table():
    p = _load("""
[stats]
sections = ["hall_of_fame", "summary", "roi"]
names = "masked"
window = "36h"
period = "sprint"
sprint_anchor = "2026-03-02"
sprint_anchor_number = 40
sprint_length_days = 7
control_group = false

[stats.judgment]
useful = ["heart"]

[stats.roi]
hourly_cost = 80
minutes_per_finding = 12.5
currency = "CHF"
""")
    s = StatsSettings.from_project(p)
    assert s.sections == ("hall_of_fame", "summary", "roi")      # order = menu order
    assert s.names == "masked" and s.period == "sprint"
    assert s.window_delta == timedelta(hours=36)
    assert s.sprint_anchor == date(2026, 3, 2) and s.sprint_anchor_number == 40
    assert s.sprint_length_days == 7
    assert s.judgment_of("heart") == "useful" and s.judgment_of("+1") is None
    assert s.judgment_of("-1") == "wrong"                         # others keep the default
    assert dict(s.roi) == {"hourly_cost": 80.0, "minutes_per_finding": 12.5,
                           "minutes_per_blocker": 240.0, "usd_rate": 1.0}   # defaults filled
    assert s.roi_currency == "CHF"
    assert p.stats_control_group is False
    hash(p)                                                       # still hashable


@pytest.mark.parametrize("toml, needle", [
    ('[stats]\nsections = ["summary", "hall-of-fame"]', "hall-of-fame"),
    ('[stats]\nwindows = "14d"', "windows"),
    ('[stats]\nnames = "anonymous"', "names"),
    ('[stats]\nperiod = "month"', "period"),
    ('[stats]\nwindow = "two weeks"', "window"),
    ('[stats]\nsprint_anchor = "05/01/2026"', "sprint_anchor"),
    ('[stats]\nsprint_length_days = 0', "sprint_length_days"),
    ('[stats.judgment]\nmeh = ["eyes"]', "meh"),
    ('[stats.roi]\nhourly_cost = "eighty"', "hourly_cost"),
    ('[stats.roi]\nhourly_cost = 0', "> 0"),
    ('[stats.roi]\nhourly_cost = 80\nminutes_per_blocker = -1', "minutes_per_blocker"),
    ('[stats.roi]\nhourly_cost = 80\nusd_rate = true', "usd_rate"),
    ('[stats.roi]\nhourly_rate = 80', "hourly_rate"),
    ('[stats.roi]\nminutes_per_finding = 10', "hourly_cost` is required"),
    ('[stats.roi]\nhourly_cost = 80\ncurrency = 978', "currency"),
    ('[stats]\nroi = 3', "roi"),
    ('[stats]\ncontrol_group = "yes"', "control_group"),
    ('[stats]\nbot_since = "October 2026"', "bot_since"),
])
def test_invalid_values_fail_early(toml, needle):
    with pytest.raises(ProjectConfigError) as e:
        _load(toml)
    assert needle in str(e.value)


def test_bot_since():
    s = StatsSettings.from_project(_load('[stats]\nbot_since = "2026-10-01"'))
    assert s.bot_since == date(2026, 10, 1)
    s = StatsSettings.from_project(_load("[stats]\nbot_since = 2026-10-01"))   # TOML date
    assert s.bot_since == date(2026, 10, 1)


def test_unknown_section_lists_known_ids():
    with pytest.raises(ProjectConfigError) as e:
        _load('[stats]\nsections = ["costs"]')
    for sid in STATS_SECTIONS:
        assert sid in str(e.value)


def _active(monkeypatch, tmp: Path, toml: str, *, reviewer: bool):
    """`active_settings` on a PROJECT folder of `tmp`, with a fake environment."""
    import types

    import reviewme.config
    from reviewme.dashboard.settings import active_settings

    pdir = tmp / "projects" / "app"
    pdir.mkdir(parents=True)
    if reviewer:
        (pdir / "reviewers" / "tech").mkdir(parents=True)
        (pdir / "reviewers" / "tech" / "system.md").write_text("persona", encoding="utf-8")
    (pdir / "project.toml").write_text(toml, encoding="utf-8")
    config = types.SimpleNamespace(project="app", config_home=str(tmp))
    monkeypatch.setattr(reviewme.config, "load_config", lambda **kw: config)
    warnings: list[str] = []
    return active_settings(warn=warnings.append), warnings


def test_stats_of_a_project_without_reviewer_are_kept_with_a_warning(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        (s, language), warnings = _active(
            monkeypatch, Path(tmp),
            '[review]\nlanguage = "fr"\n[stats]\nbot_since = "2026-10-01"', reviewer=False)
    # `load_project` still refuses the folder (no reviewer): the warning names that error,
    # and the page follows the folder's [stats] instead of the defaults.
    assert len(warnings) == 1 and "no reviewer" in warnings[0]
    assert "still uses its [stats]" in warnings[0]
    assert s.bot_since == date(2026, 10, 1) and language == "fr" and s.project == "app"


def test_broken_stats_fall_back_to_defaults_with_a_warning(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        (s, _), warnings = _active(monkeypatch, Path(tmp), '[stats]\nwindows = "30d"',
                                   reviewer=True)
    assert len(warnings) == 1 and "windows" in warnings[0]
    assert "default settings, [stats] ignored" in warnings[0]
    assert s == StatsSettings.default()


def test_valid_project_no_warning(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        (s, _), warnings = _active(monkeypatch, Path(tmp), '[stats]\nbot_since = "2026-10-01"',
                                   reviewer=True)
    assert warnings == [] and s.bot_since == date(2026, 10, 1)


def test_stats_html_prints_the_warning_on_stderr(monkeypatch, capsys):
    import types

    from reviewme import cli

    with tempfile.TemporaryDirectory() as tmp:
        _active(monkeypatch, Path(tmp), "[stats]\nbot_since = 2026-10-01", reviewer=False)
        monkeypatch.setenv("REVIEWME_LEDGER_DIR", str(Path(tmp) / "ledger"))
        out = Path(tmp) / "d.html"
        assert cli._stats(types.SimpleNamespace(html=str(out), json=False)) == 0
        assert out.is_file()
    err = capsys.readouterr().err
    assert "Warning: invalid project config" in err and "no reviewer" in err


ERAS_TOML = """
[[stats.eras]]
name = "ReviewMe"
since = "2026-11-03"
label = "review-me"
[[stats.eras]]
name = "prototype"
since = 2026-03-10
label = "ai-review-poc"
bot_login = "maria"
[[stats.eras]]
name = "pause"
since = "2026-10-09"
"""


def test_eras_are_parsed_and_sorted():
    from reviewme.dashboard.settings import Era
    p = _load(ERAS_TOML)
    assert p.stats_eras == (("prototype", "2026-03-10", "ai-review-poc", "maria"),
                            ("pause", "2026-10-09", "", ""),
                            ("ReviewMe", "2026-11-03", "review-me", ""))
    s = StatsSettings.from_project(p)
    assert s.eras == (Era("prototype", date(2026, 3, 10), "ai-review-poc", "maria"),
                      Era("pause", date(2026, 10, 9)),
                      Era("ReviewMe", date(2026, 11, 3), "review-me"))
    assert StatsSettings.from_project(_load("")).eras == ()


@pytest.mark.parametrize("toml,needle", [
    ('[[stats.eras]]\nname = "a"\nsince = "2026-01-01"\n'
     '[[stats.eras]]\nname = "b"\nsince = 2026-01-01', "two eras start on 2026-01-01"),
    ('[[stats.eras]]\nname = "a"\nsince = "January 2026"', "`since`: expected YYYY-MM-DD"),
    ('[[stats.eras]]\nname = "a"\nsince = "2026-02-30"', "`since`: expected YYYY-MM-DD"),
    ('[[stats.eras]]\nname = "a"', "`since` is required"),
    ('[[stats.eras]]\nsince = "2026-01-01"', "`name`"),
    ('[[stats.eras]]\nname = "a"\nsince = "2026-01-01"\nlabel = 3', "`label`"),
    ('[[stats.eras]]\nname = "a"\nsince = "2026-01-01"\nlabels = "x"', "labels"),
    ('[[stats.eras]]\nname = "a"\nsince = "2026-01-01"\n'
     '[[stats.eras]]\nname = "a"\nsince = "2026-02-01"', "unique"),
    ('[stats]\neras = "2026-01-01"', "array of tables"),
    ('[[stats.eras]]\nname = "a"\nsince = "2026-01-01"\nbot_login = "maria"',
     "`bot_login` needs a `label`"),
    ('[[stats.eras]]\nname = "a"\nsince = "2026-01-01"\nlabel = "x"\nbot_login = "a b"',
     "`bot_login` must be a GitHub login"),
    ('[stats]\ndebrief = "no"', "debrief"),
])
def test_invalid_eras_fail_early(toml, needle):
    with pytest.raises(ProjectConfigError) as e:
        _load(toml)
    assert needle in str(e.value)


def test_debrief_flag():
    assert StatsSettings.from_project(_load("")).debrief is True
    assert StatsSettings.from_project(_load("[stats]\ndebrief = false")).debrief is False
