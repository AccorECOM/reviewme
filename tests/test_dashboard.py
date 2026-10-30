"""Tests for the ledger-driven dashboard (`reviewme/dashboard/`).

What matters: each section computes the right numbers from the same window; the page
shows exactly the sections the project enabled, in its order; and nothing written by a
person or the model (finding message, suggestion, reply, PR title) ever reaches the page,
even when the ledger keeps it (`[ledger] include_text = true`).

Run: `pytest tests/`.
"""
import json
import os
import re
import sys
import tempfile
import types
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dashboard_fixture import NOW, SECRETS, make_ledger

from reviewme.dashboard import StatsSettings, build, write
from reviewme.dashboard.data import Context, sprint_bounds, sprint_number
from reviewme.dashboard.sections import MODULES
from reviewme.dashboard.settings import Era
from reviewme.projects import STATS_SECTIONS

ALL = StatsSettings(sections=STATS_SECTIONS)


@pytest.fixture(scope="module")
def ledger():
    with tempfile.TemporaryDirectory() as tmp:
        yield make_ledger(Path(tmp) / "ledger")


def _build(ledger, settings=ALL, language="en"):
    return build(ledger, settings, language, now=NOW)


def _sections(page: str) -> list[str]:
    return re.findall(r'<section id="([^"]+)"', page)


# --------------------------------------------------------------- numbers per section

def test_summary(ledger):
    s = _build(ledger)[1]["summary"]
    assert (s["prs"], s["runs"], s["posted"], s["followed"]) == (3, 4, 5, 1)
    assert s["cost_usd"] == 2.2 and s["cost_per_pr"] == pytest.approx(0.7333)
    assert (s["blockers_posted"], s["blockers_fixed_before_merge"]) == (2, 1)
    assert s["human_signal_rate"] == 0.8
    assert s["periods"] == ["2026-W41", "2026-W42", "2026-W43"]
    assert s["cost_by_period"] == [1.2, 0.3, 0.7]
    assert s["followed_by_period"] == [1, 0, 0]
    assert s["config_changes"] == [1]                 # cfgA -> cfgB in W42


def test_quality(ledger):
    q = _build(ledger)[1]["quality"]
    assert q["by_reviewer"]["us"]["contested"] == 2
    assert q["judgment"] == {"useful": 1, "wrong": 2, "off_topic": 0, "saved_me": 1,
                             "other": 0}
    assert q["reply_labels"] == {"false_positive": 1, "intended": 1, "unimportant": 0,
                                 "question": 1, "other": 0}
    assert q["misses"] == {"human_comments": 3, "on_silent_lines": 1,
                           "on_dropped_findings": 1}
    v = q["verification"]
    assert (v["confirmed"], v["refuted"], v["confirmed_contested_rate"]) == (5, 1, 0.6)


def test_judgment_vocabulary_is_the_projects(ledger):
    """A project mapping 🚀 to `useful` gets it counted there, generically."""
    s = StatsSettings(judgment=(("useful", ("rocket",)), ("wrong", ("-1",))))
    q = _build(ledger, s)[1]["quality"]
    assert q["judgment"]["useful"] == 1 and q["judgment"]["saved_me"] == 0
    assert q["judgment"]["other"] == 1                   # the 👍 is now unmapped


def test_usage(ledger):
    u = _build(ledger)[1]["usage"]
    assert u["per_dev"]["alice"] == {"prs": 2, "runs": 3, "findings": 3, "followed": 1,
                                     "cost_usd": 1.5, "followed_rate": 0.3333}
    assert u["runs_per_pr_hist"] == {"1": 2, "2": 1}
    assert (u["silent_runs"], u["silent_share"], u["silent_cost_usd"]) == (1, 0.25, 0.4)
    assert u["participants"] == 2 and u["remember"] == 1 and u["threads_resolved"] == 1
    assert u["by_weekday"][:2] == [2, 2] and u["by_hour"][9] == 1


def test_cost(ledger):
    c = _build(ledger)[1]["cost"]
    assert c["total_usd"] == 2.2 and c["per_run"] == 0.55 and c["per_followed"] == 2.2
    assert c["per_100_lines"] == 0.275                   # 800 lines over 4 runs
    assert c["verification_share"] == pytest.approx(0.1 / 2.2, abs=1e-4)
    assert c["cache_ratio"] == 0.12 and c["max_turns_hits"] == 1
    assert set(c["models_by_reviewer"]) == {"tech"}      # only tech ran on two models
    assert c["models_by_reviewer"]["tech"]["m2"]["posted"] == 2


def test_reliability(ledger):
    r = _build(ledger)[1]["reliability"]
    assert (r["is_error"], r["post_failed"], r["parse_failed"], r["max_turns"]) == (1, 1, 1, 1)
    assert r["in_summary"] == 1 and r["context_bytes"] == 1000
    assert r["durations"]["tech"] == {"runs": 4, "p50_s": 60, "p95_s": 120}
    assert [p["incidents"] for p in r["per_period"]] == [1, 1, 0]


def test_tuning_and_baseline(ledger):
    t = _build(ledger)[1]["tuning"]
    assert t["baseline_vs_live"]["tech"] == {"prs": 1, "baseline": 1, "live": 3, "both": 1}
    assert [(c["config_hash"], c["runs"], c["posted"], c["cost_usd"]) for c in t["configs"]] \
        == [("cfgA", 2, 2, 1.2), ("cfgB", 2, 3, 1.0)]
    assert t["remembered"] == [{"pr": 102, "user": "alice", "trusted": True}]


def test_baseline_never_counts_as_operations(ledger):
    """The baseline (0.50 $, same PR) must not show in the team's cost."""
    res = _build(ledger)[1]
    assert res["cost"]["total_usd"] == 2.2 and res["tuning"]["baseline_cost_usd"] == 0.5


def test_results_are_json(ledger):
    json.dumps(_build(ledger)[1])


# --------------------------------------------------------------- page

def test_default_sections_and_order(ledger):
    page = _build(ledger, StatsSettings())[0]
    assert _sections(page) == ["summary", "quality", "usage", "cost", "roi", "reliability",
                               "tuning", "history", "hall_of_fame"]
    custom = StatsSettings(sections=("cost", "summary"))
    page = _build(ledger, custom)[0]
    assert _sections(page) == ["cost", "summary"]
    assert re.findall(r'<a href="#([^"]+)"', page) == ["cost", "summary"]   # menu too
    assert 'id="quality"' not in page


def test_page_is_self_contained(ledger):
    page = _build(ledger)[0]
    assert page.startswith("<!doctype html>")
    assert "<title>ReviewMe — dashboard</title>" in page
    for external in ("http://", "https://", "src=", "@import", "<link"):
        assert external not in page, f"the page is not self-contained: {external}"
    assert "#7C3AED" in page                          # validated palette


def test_no_text_from_the_ledger(ledger):
    """The fixture rows carry message, suggestion, reply and remember texts."""
    page, res = _build(ledger)
    blob = page + json.dumps(res)
    for secret in SECRETS:
        assert secret not in blob, f"ledger text leaked into the dashboard: {secret}"


def test_masked_names(ledger):
    page, res = _build(ledger, StatsSettings(sections=STATS_SECTIONS, names="masked"))
    blob = page + json.dumps(res)
    for login in ("alice", "bob", "dave"):
        assert login not in blob
    assert "dev_1" in page and "masked names" in page
    # alice appears first (run r1): dev_1 everywhere, stable within the render
    assert res["usage"]["per_dev"]["dev_1"]["runs"] == 3


def test_french(ledger):
    page = _build(ledger, language="fr")[0]
    assert '<html lang="fr">' in page
    assert "Synthèse" in page and "remarques postées" in page and "Réglages" in page


def test_every_section_has_both_languages():
    for sid, m in MODULES.items():
        assert {"en", "fr"} <= set(m.TITLE), sid
        mod = m if isinstance(m, types.ModuleType) else sys.modules[type(m).__module__]
        for table in ("_T", "_TITLES"):
            T = getattr(mod, table, None)
            if T:
                assert set(T["en"]) == set(T["fr"]), f"{sid}.{table}"


def test_empty_ledger_renders():
    with tempfile.TemporaryDirectory() as tmp:
        page, res = build(Path(tmp) / "nothing", ALL, "en", now=NOW)
    assert _sections(page) == list(STATS_SECTIONS)
    assert res["summary"]["runs"] == 0


def test_write(ledger):
    with tempfile.TemporaryDirectory() as tmp:
        dest = write(Path(tmp) / "out" / "dashboard.html", ledger, ALL, "en", now=NOW)
        assert dest.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_window_excludes_old_rows(ledger):
    s = StatsSettings(window="10d")                   # from 2026-10-18: only r4
    res = _build(ledger, s)[1]
    assert res["summary"]["prs"] == 1 and res["summary"]["cost_usd"] == 0.7


# --------------------------------------------------------------- periods

def test_sprint_periods(ledger):
    from datetime import date
    s = StatsSettings(period="sprint", sprint_anchor=date(2026, 9, 28),
                      sprint_anchor_number=10, sprint_length_days=14)
    res = _build(ledger, s)[1]
    assert res["summary"]["periods"] == ["Sprint 10", "Sprint 11"]
    assert sprint_number(date(2026, 10, 12), date(2026, 9, 28), 10, 14) == 11
    assert sprint_bounds(11, date(2026, 9, 28), 10, 14) == (date(2026, 10, 12),
                                                          date(2026, 10, 25))
    assert sorted(["Sprint 10", "Sprint 9"], key=Context.period_order) == ["Sprint 9",
                                                                           "Sprint 10"]


# --------------------------------------------------------------- hall of fame

def test_hall_of_fame(ledger):
    h = _build(ledger)[1]["hall_of_fame"]
    cats = {c["id"]: c for c in h["categories"]}
    assert cats["golden-dev"]["who"] == "alice" and cats["golden-dev"]["raw"] == 1.5
    assert cats["token-monster"]["who"] == "bob"
    assert cats["tamer"]["who"] == "alice" and cats["tamer"]["raw"] == 2   # 2 pushbacks
    assert cats["chosen-one"]["who"] == "alice"           # BLOCKER of PR 101, pre-merge
    assert cats["ghost"]["who"] == "bob"                  # never reacted nor replied
    assert cats["serial-pusher"]["detail"] == "PR #101"
    assert "bargain" not in cats                          # one author with 2+ PRs only
    assert [r["dev"] for r in h["leaderboard"]] == ["alice", "bob"]
    assert h["leaderboard"][0]["record_pr"] == 101


def test_hall_of_fame_titles_per_language(ledger):
    en = _build(ledger)[0]
    fr = _build(ledger, language="fr")[0]
    assert "The Golden Dev" in en and "The Silent Type" in en
    assert "Le Dev en Or" in fr and "Le Discret" in fr


def test_hall_of_fame_falls_back_to_stats_json(ledger):
    """No `prs.jsonl` for a PR: the author comes from `stats.json`, else `?`."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "l"
        from dashboard_fixture import runs

        from reviewme.ledger import RUNS_FILE, write_part
        write_part(root, RUNS_FILE, [r for r in runs() if r["variant"] == "live"])
        stats = Path(tmp) / "stats.json"
        stats.write_text(json.dumps({"runs": [{"pr": 103, "auteur": "zoe"}]}))
        res = build(root, ALL, "en", stats_file=stats, now=NOW)[1]
    devs = {r["dev"] for r in res["hall_of_fame"]["leaderboard"]}
    assert devs == {"zoe"}                                # PRs 101/102 unknown: skipped
    assert set(res["usage"]["per_dev"]) == {"zoe", "?"}


# --------------------------------------------------------------- wiring

def test_export_writes_the_dashboard(ledger):
    import tarfile
    from datetime import timedelta

    from reviewme import export as ex
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out"
        archive, _ = ex.export(ledger, out, timedelta(days=3650), repo="o/r",
                               stats_file=Path(tmp) / "absent.json")
        page = (out / "dashboard.html").read_text(encoding="utf-8")
        with tarfile.open(archive) as tar:
            member = next(m for m in tar.getmembers() if m.name.endswith("dashboard.html"))
            archived = tar.extractfile(member).read().decode()
    assert "window 3650d" in page and "alice" in page
    # The archive is pseudonymized: its page masks names like its JSONL.
    assert "alice" not in archived and "dev_1" in archived
    for secret in SECRETS:
        assert secret not in page + archived


def test_server_serves_the_dashboard(ledger, monkeypatch):
    from io import BytesIO

    from reviewme.web import server
    monkeypatch.setenv("REVIEWME_LEDGER_DIR", str(ledger))
    page, results = server.build_dashboard()
    assert page.startswith("<!doctype html>") and "summary" in results

    def get(path: str) -> tuple[bytes, bytes]:
        h = server.DashboardHandler.__new__(server.DashboardHandler)
        h.path, h.command, h.request_version = path, "GET", "HTTP/1.1"
        h.requestline, h.client_address, h.wfile = f"GET {path} HTTP/1.1", ("127.0.0.1", 0), BytesIO()
        h.do_GET()
        head, _, body = h.wfile.getvalue().partition(b"\r\n\r\n")
        return head, body

    head, body = get("/dashboard")
    assert b" 200 " in head.split(b"\r\n")[0] and b"text/html" in head
    assert body.startswith(b"<!doctype html>")
    head, body = get("/api/dashboard")
    assert b" 200 " in head.split(b"\r\n")[0]
    assert set(json.loads(body)) >= {"summary", "cost", "hall_of_fame"}


# --------------------------------------------------------------- collection additions

def test_coverage_and_triggers(ledger):
    u = _build(ledger)[1]["usage"]
    c = u["coverage"]
    assert (c["available"], c["merged"], c["reviewed"], c["rate"]) == (True, 2, 1, 0.5)
    assert c["by_dev"] == {"alice": {"merged": 1, "reviewed": 1, "rate": 1.0},
                           "carol": {"merged": 1, "reviewed": 0, "rate": 0.0}}
    assert u["by_trigger"] == {"pr_build": 3, "unknown": 1}
    page = _build(ledger)[0]
    assert "merged PRs reviewed" in page and "Runs by trigger" in page
    assert "carol" in page                     # the coverage gap is shown, with 0 runs
    s = _build(ledger)[1]["summary"]
    assert s["coverage_rate"] == 0.5 and "Coverage: 50 %" in page


def test_coverage_needs_the_control_group():
    """Without unreviewed PRs recorded, every merged PR is a reviewed one: no 100 %."""
    from dashboard_fixture import prs

    from reviewme.enrich import PRS_FILE
    from reviewme.ledger import write_part
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "l"
        write_part(root, PRS_FILE, [{**p, "control_group": False} for p in prs()
                                    if p["reviewed"]])
        res = build(root, ALL, "en", now=NOW)[1]
    assert res["usage"]["coverage"] == {"available": False}
    assert res["summary"]["coverage_rate"] is None


def test_judgment_delay(ledger):
    """From the posting run to the first reaction or reply: 30 h, 1 h, 1 h, 4 h."""
    res = _build(ledger)[1]
    assert res["quality"]["judgment_delay_median_h"] == 2.5
    assert res["quality"]["judgment_delay_n"] == 4
    assert res["summary"]["judgment_delay_median_h"] == 2.5


def test_pr_cycles(ledger):
    from reviewme.dashboard.data import load_context, pr_cycles
    ctx = load_context(ledger, ALL, now=NOW)
    cycles = {c["pr"]: c for c in pr_cycles(ctx)}
    assert list(cycles) == [101, 102, 103, 104]
    a = cycles[101]
    assert (a["reviewed"], a["size"], a["human_reviews"]) == (True, 150, 2)
    assert a["hours_to_first_human_review"] == 28.0 and a["hours_to_merge"] == 64.0
    assert a["first_bot_run_at"] == "2026-10-05T09:00:00+00:00"
    # Older line without the field: the first live run in the window stands in.
    assert cycles[102]["first_bot_run_at"] == "2026-10-12T10:00:00+00:00"
    u = cycles[104]
    assert (u["reviewed"], u["size"], u["first_bot_run_at"], u["hours_to_merge"]) == (
        False, None, None, 48.0)
    assert pr_cycles(ctx) is pr_cycles(ctx)             # computed once per render


def test_contested_rule_points_at_its_prompt_file():
    """A rule contested 3 times: the first prompt file naming it as a whole word."""
    import dataclasses

    from reviewme.enrich import ENRICH_FILE
    from reviewme.ledger import FINDINGS_FILE, write_part
    with tempfile.TemporaryDirectory() as tmp:
        root, proj = Path(tmp) / "l", Path(tmp) / "project"
        (proj / "common").mkdir(parents=True)
        (proj / "reviewers" / "tech").mkdir(parents=True)
        # The reviewer's own folder comes first, but only names a LONGER rule id.
        (proj / "reviewers" / "tech" / "system.md").write_text("- force-unwrap-strict: …")
        (proj / "common" / "rules.md").write_text("## Rules\n- `force-unwrap`: never `!`\n")
        write_part(root, FINDINGS_FILE, [
            {"ts": "2026-10-20T10:00:00+00:00", "run_id": "z", "pr": 1, "variant": "live",
             "reviewer": "tech", "rule_id": rid, "fate": "posted", "comment_id": cid,
             "confidence": 90} for cid, rid in ((1, "force-unwrap"), (2, "force-unwrap"),
                                                 (3, "force-unwrap"), (4, "naming"),
                                                 (5, "naming"), (6, "naming"))])
        write_part(root, ENRICH_FILE, [
            {"ts": "2026-10-21T00:00:00+00:00", "comment_id": cid, "replies": [],
             "reactions": [{"content": "-1", "user": "x"}]} for cid in range(1, 7)])
        with_dir = dataclasses.replace(ALL, project_dir=proj)
        page, res = build(root, with_dir, "en", now=NOW)
        bare = build(root, ALL, "en", now=NOW)[1]
    files = {r["rule"]: r["file"] for r in res["tuning"]["contested_rules"]}
    assert files == {"tech:force-unwrap": "common/rules.md", "tech:naming": None}
    assert "common/rules.md" in page
    assert {r["file"] for r in bare["tuning"]["contested_rules"]} == {None}


# --------------------------------------------------------------- triage

def test_quality_reads_triage(ledger):
    page, res = _build(ledger)
    t = res["quality"]["triage"]
    assert t["classified"] == 4                         # comment 3: the later row wins
    assert t["causes"]["false_positive"] == 2 and t["causes"]["other"] == 0
    assert t["by_reviewer"]["us"]["false_positive"] == 1
    assert t["by_reviewer"]["tech"]["fixed"] == 1
    # Without include_text, the patterns are counts only, in the page and in the API.
    assert t["patterns"] == [{"count": 2, "examples": 2}] and t["texts_shown"] is False
    assert "Causes of contested findings (LLM triage)" in page
    assert "Recurring misses" in page and "Pattern 1" in page
    assert "Replies by label" not in page              # the triage chart replaces it
    # The keyword labels are still computed for the API.
    assert res["quality"]["reply_labels"]["intended"] == 1


def test_triage_texts_only_with_include_text(ledger):
    import dataclasses
    page, res = _build(ledger, dataclasses.replace(ALL, include_text=True))
    p = res["quality"]["triage"]["patterns"][0]
    assert (p["title"], p["suggested_rule"]) == ("SECRET-PATTERN", "SECRET-SUGGESTED")
    assert "SECRET-PATTERN" in page and "SECRET-SUGGESTED" in page
    assert "summarised in the model" in page
    # A per-finding reason is never printed, even then.
    assert "SECRET-REASON" not in page + json.dumps(res)
    assert "SECRET-PATTERN" not in _build(ledger)[0]


def test_quality_without_triage_keeps_keyword_labels():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "ledger"
        make_ledger(root)
        for part in (root / "triage").glob("*.jsonl"):
            part.unlink()
        page, res = _build(root)
    assert res["quality"]["triage"] is None
    assert "Replies by label" in page and "Recurring misses" not in page
    assert "Main cause" not in page


def test_tuning_main_cause_from_triage():
    from reviewme.enrich import ENRICH_FILE
    from reviewme.ledger import FINDINGS_FILE, write_part
    from reviewme.triage import TRIAGE_FILE
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "l"
        write_part(root, FINDINGS_FILE, [
            {"ts": "2026-10-20T10:00:00+00:00", "run_id": "z", "pr": 1, "variant": "live",
             "reviewer": "tech", "rule_id": rid, "fate": "posted", "comment_id": cid,
             "confidence": 90} for cid, rid in ((1, "force-unwrap"), (2, "force-unwrap"),
                                                 (3, "force-unwrap"), (4, "naming"),
                                                 (5, "naming"), (6, "naming"))])
        write_part(root, ENRICH_FILE, [
            {"ts": "2026-10-21T00:00:00+00:00", "comment_id": cid, "replies": [],
             "reactions": [{"content": "-1", "user": "x"}]} for cid in range(1, 7)])
        write_part(root, TRIAGE_FILE, [
            {"ts": "2026-10-22T00:00:00+00:00", "kind": "finding", "comment_id": cid,
             "reviewer": "tech", "rule_id": "force-unwrap", "cause": cause}
            for cid, cause in ((1, "missing_context"), (2, "missing_context"),
                               (3, "false_positive"))])
        page, res = build(root, ALL, "fr", now=NOW)
    causes = {r["rule"]: r["main_cause"] for r in res["tuning"]["contested_rules"]}
    assert causes == {"tech:force-unwrap": "missing_context", "tech:naming": None}
    assert "Cause principale (tri)" in page and "contexte manquant" in page


def test_archive_strips_triage_text(ledger):
    import dataclasses
    import tarfile
    from datetime import timedelta

    from reviewme import export as ex

    def archived(settings) -> list[dict]:
        with tempfile.TemporaryDirectory() as tmp:
            archive, _ = ex.export(ledger, Path(tmp) / "out", timedelta(days=3650),
                                   settings=settings, stats_file=Path(tmp) / "absent.json")
            with tarfile.open(archive) as tar:
                member = next(m for m in tar.getmembers() if m.name.endswith("triage.jsonl"))
                return [json.loads(x) for x in tar.extractfile(member).read().decode()
                        .splitlines()]

    rows = archived(ALL)
    assert {r["cause"] for r in rows if r["kind"] == "finding"} >= {"fixed", "false_positive"}
    assert not any("reason" in r for r in rows)
    (patterns,) = [r for r in rows if r["kind"] == "patterns"]
    assert patterns["patterns"] == [{"count": 2, "example_comment_ids": [900, 901]}]
    assert "SECRET" not in json.dumps(rows)
    kept = archived(dataclasses.replace(ALL, include_text=True))
    assert any(r.get("reason") == "SECRET-REASON" for r in kept)


# --------------------------------------------------------------- debrief

def _words(text: str) -> int:
    return len(re.findall(r"[A-Za-zÀ-ÿ_']+", text))


def test_debrief_card_on_the_fixture(ledger):
    page, res = _build(ledger)
    d = {x["id"]: x["text"] for x in res["summary"]["debrief"]}
    assert list(d) == ["volume", "quality", "cost", "roi", "history", "reliability"]
    assert d["volume"] == ("3 PRs reviewed in the window (4 runs); 50 % of merged PRs "
                           "covered (1 of 2).")
    assert d["quality"] == ("5 findings posted: 20 % acted upon, 60 % contested, first "
                            "human signal after 2.5 h (median).")
    assert d["cost"] == "Cost 2.20 $: 0.73 $ per PR, 2.20 $ per finding acted upon."
    # Control group: only the pair known on both sides (no unreviewed first review).
    assert d["roi"] == "Reviewed vs unreviewed PRs (medians): merged after 64 h vs 48 h."
    assert d["history"] == ("Before vs after (medians): to merge 56 h → 36 h, to first "
                            "human review 24 h → 6 h; a trend, not a cause.")
    assert d["reliability"] == ("Incidents over 6 reviewer runs: CLI errors 1, max_turns "
                                "cuts 1.")
    assert all(_words(t) < 25 for t in d.values())
    sec = page[page.index('<section id="summary"'):]
    sec = sec[:sec.index("</section>")]
    # At the top, above the tiles, one line per sentence.
    assert sec.index("<h3>Debrief</h3>") < sec.index('class="tile"')
    assert sec.count("<li>") == 6
    fr = _build(ledger, language="fr")[1]["summary"]["debrief"]
    assert fr[0]["text"].startswith("3 PR relues sur la fenêtre (4 passages)")
    assert all(_words(x["text"]) < 25 for x in fr)


def test_debrief_hidden_by_the_flag(ledger):
    import dataclasses
    page, res = _build(ledger, dataclasses.replace(ALL, debrief=False))
    assert res["summary"]["debrief"] == [] and "Debrief" not in page


def test_debrief_skips_missing_data():
    import dataclasses
    with tempfile.TemporaryDirectory() as tmp:
        root = make_ledger(Path(tmp) / "l", with_history=False)
        ids = [x["id"] for x in build(root, ALL, "en", now=NOW)[1]["summary"]["debrief"]]
        # No history: no history sentence; fewer than 3 contestations: no rule named.
        assert "history" not in ids and "contested" not in ids
        roi = dataclasses.replace(ALL, roi=(("hourly_cost", 80.0),
                                            ("human_first_review_hours", 20.0),
                                            ("minutes_per_blocker", 240.0),
                                            ("minutes_per_finding", 12.0),
                                            ("usd_rate", 0.9)))
        # Window with no run at all: nothing but what exists (here, nothing).
        empty = build(root, roi, "en", now=NOW.replace(year=2030))[1]["summary"]
    assert empty["debrief"] == []


def test_debrief_roi_estimate_and_contested_rule(ledger):
    """Without a control group the estimate speaks; a rule contested 3 times is named."""
    import dataclasses

    from reviewme.dashboard import debrief
    from reviewme.dashboard.data import load_context
    from reviewme.dashboard.sections import summary
    s = dataclasses.replace(ALL, roi=(("hourly_cost", 80.0), ("human_first_review_hours", 20.0),
                                      ("minutes_per_blocker", 240.0),
                                      ("minutes_per_finding", 12.0), ("usd_rate", 0.9)),
                            roi_currency="EUR")
    ctx = load_context(ledger, s, "en", now=NOW)
    ctx.prs = {k: {**v, "control_group": False} for k, v in ctx.prs.items()}
    ctx.quality["contested_rules"] = [{"rule": "null-check", "contested": 4, "posted": 9}]
    d = dict(debrief.sentences(ctx, summary.compute(ctx)))
    assert d["roi"] == ("Estimated 4 h of human time saved (320 EUR) for 2 EUR of API "
                        "cost; see ROI.")
    assert d["contested"] == "Most contested rule: null-check, 4 times out of 9 posted."


# --------------------------------------------------------------- history

def test_history_before_and_after(ledger):
    h = _build(ledger)[1]["history"]
    assert h["available"] and h["prs"] == 7                  # dependabot left out
    assert h["bot_start"] == "2026-10-05T09:00:00+00:00"      # first live run (r1)
    assert h["bot_start_source"] == "ledger"
    b, a = h["before"], h["after"]
    # PR 101 was opened the day before the first run: it is a "before" PR.
    assert (b["merged"], b["median_merge_h"], b["median_first_review_h"]) == (4, 56.0, 24.0)
    assert (b["no_human_review_share"], b["reviews_per_pr"], b["threads_per_pr"]) == (
        0.25, 1.25, 2.25)
    assert (b["threads_per_100_lines"], b["hotfix_share"], b["bugs_per_month"]) == (
        2.25, 0.25, 3.0)
    assert b["merged_per_month"] == 0.2                       # 4 PRs over ~20.8 months
    # PR 3 is `fix/login`: an ordinary fix, not a hotfix, whatever its stored flag says.
    assert (b["fix_share"], a["hotfix_share"], a["fix_share"]) == (0.25, 0.0, 0.5)
    assert (a["merged"], a["median_merge_h"], a["median_first_review_h"]) == (2, 36.0, 6.0)
    assert (a["threads_per_pr"], a["threads_per_100_lines"], a["bugs_per_month"]) == (
        0.5, 0.33, 1.0)
    assert h["after_reviewed"]["merged"] == 1 and h["after_reviewed"]["median_merge_h"] == 24.0
    assert h["after_unreviewed"]["median_merge_h"] == 48.0


def test_history_quarters_and_marker(ledger):
    h = _build(ledger)[1]["history"]
    assert h["granularity"] == "quarter"
    assert h["periods"] == ["2025-Q1", "2025-Q2", "2025-Q3", "2025-Q4", "2026-Q1",
                            "2026-Q2", "2026-Q3", "2026-Q4"]
    assert h["start_period"] == "2026-Q4"
    merged = h["series"]["merged_per_month"]
    # The running quarter counts only its elapsed part (27 days): no false collapse.
    assert [v for _, v in merged["points"]] == [0.7, 0.3, 0, 0, 0, 0, 0, 3.4]
    assert merged["marker"] == [7]
    merge_h = h["series"]["median_merge_h"]
    assert merge_h["points"] == [("2025-Q1", 36.0), ("2025-Q2", 72.0), ("2026-Q4", 48.0)]
    assert merge_h["marker"] == [2]
    assert [p for p, _ in h["series"]["bugs_per_month"]["points"]] == ["2025-Q1", "2026-Q4"]
    page = _build(ledger)[0]
    sec = page[page.index('<section id="history"'):]
    sec = sec[:sec.index("</section>")]
    assert "ReviewMe starts" in sec and 'stroke-dasharray="4 4"' in sec
    assert "Before/after is not proof" in sec and 'href="#roi"' in sec
    assert "After, not reviewed" in sec
    assert "Share of fix/ branches (ordinary fixes, not hotfixes)" in sec
    assert "fix/ branches (ordinary fixes, not hotfixes)" in sec
    assert [v for _, v in h["series"]["fix_share"]["points"]] == [0.0, 1.0, 0.3333]


def test_history_bot_since_override(ledger):
    from datetime import date
    h = _build(ledger, StatsSettings(sections=("history",),
                                     bot_since=date(2025, 4, 1)))[1]["history"]
    assert h["bot_start"] == "2025-04-01T00:00:00+00:00"
    assert h["bot_start_source"] == "settings" and h["start_period"] == "2025-Q2"
    assert h["before"]["merged"] == 2 and h["after"]["merged"] == 4
    assert h["series"]["merged_per_month"]["marker"] == [1]


ERAS = (Era("prototype", date(2025, 4, 1), "ai-review-poc"),
        Era("pause", date(2026, 7, 1)),
        Era("ReviewMe", date(2026, 10, 10), "review-me"))


@pytest.fixture(scope="module")
def era_ledger():
    """The fixture ledger, plus what three eras need: PR 3 carries the prototype's label,
    PR 105 ReviewMe's, and PR 6 is a v0 PR nobody labelled."""
    from dashboard_fixture import history

    from reviewme.ledger import write_part
    from reviewme.pr_history import HISTORY_FILE
    rows = {r["pr"]: r for r in history()}
    later = "2026-10-27T06:00:00+00:00"
    extra = [{**rows[3], "ts": later, "labels": ["ai-review-poc"]},
             {**rows[105], "ts": later, "labels": ["review-me"]},
             {**rows[104], "ts": later, "pr": 6, "created_at": "2025-04-15T09:00:00Z",
              "merged_at": "2025-04-16T09:00:00Z", "head_ref": "feature/y"}]
    with tempfile.TemporaryDirectory() as tmp:
        root = make_ledger(Path(tmp) / "ledger")
        write_part(root, HISTORY_FILE, extra)
        yield root


def test_era_assignment():
    from datetime import UTC, datetime

    from reviewme.dashboard.data import EraSpan, era_of
    eras = [EraSpan(e.name, datetime(e.since.year, e.since.month, e.since.day, tzinfo=UTC),
                    e.label) for e in ERAS]
    at = lambda s: datetime.fromisoformat(s)       # noqa: E731
    assert era_of(eras, at("2025-03-31T23:59:59+00:00")) is None          # before
    assert era_of(eras, at("2025-04-01T00:00:00+00:00")).name == "prototype"
    assert era_of(eras, at("2026-06-30T12:00:00+00:00")).name == "prototype"
    assert era_of(eras, at("2026-07-01T00:00:00+00:00")).name == "pause"
    assert era_of(eras, at("2026-10-14T09:00:00+00:00")).name == "ReviewMe"
    assert era_of(eras, None) is None


def test_history_per_era_table(era_ledger):
    s = StatsSettings(sections=("history", "roi"), eras=ERAS)
    page, res = build(era_ledger, s, "en", now=NOW)
    h = res["history"]
    cols = ("prs", "merged", "median_merge_h", "median_first_review_h", "reviews_per_pr",
            "threads_per_pr", "revert_share", "hotfix_only_share", "fix_share")
    table = {(e["era"], e["variant"]): tuple(e[c] for c in cols) for e in h["per_era"]}
    assert list(table) == [(None, "all"), ("prototype", "with"),
                           ("prototype", "without"), ("pause", "all"),
                           ("ReviewMe", "with"), ("ReviewMe", "without")]
    # Before: PRs 1 and 2 (2 is a hotfix branch).
    assert table[(None, "all")] == (2, 2, 36.0, 24.0, 1.0, 2.0, 0.0, 0.5, 0.0)
    # v0: PR 3 has the prototype's label (and a fix/ branch); PRs 4 (never merged) and 6
    # do not.
    assert table[("prototype", "with")] == (1, 1, 72.0, 12.0, 1.0, 2.0, 0.0, 0.0, 1.0)
    assert table[("prototype", "without")] == (
        2, 1, 24.0, None, 0.0, 0.0, 0.0, 0.0, 0.0)
    # pause: PR 101, reviewed in the ledger but in an era without a label: one row.
    assert table[("pause", "all")] == (1, 1, 64.0, 28.0, 2.0, 3.0, 0.0, 0.0, 0.0)
    # ReviewMe: PR 105 by its label, PR 104 (bugfix/) without. PR 105's stored
    # `bot_reviewed` is not what decides: with eras, ledger or era label only.
    assert table[("ReviewMe", "with")] == (1, 1, 24.0, 6.0, 1.0, 1.0, 0.0, 0.0, 0.0)
    assert table[("ReviewMe", "without")] == (1, 1, 48.0, None, 0.0, 0.0, 0.0, 0.0, 1.0)
    # The ROI page compares "before" with the LAST era, and says which one.
    roi = res["roi"]["over_time"]
    assert roi["after_name"] == "ReviewMe" and roi["bot_start"].startswith("2026-10-10")
    assert roi["numbers"]["median_merge_h"] == {"before": 36.0, "after": 36.0}
    assert roi["numbers"]["hotfix_share"] == {"before": 0.5, "after": 0.0}
    assert "before → ReviewMe" in page
    sec = page[page.index('<section id="history"'):]
    sec = sec[:sec.index("</section>")]
    assert "Per era (by opening date)" in sec and "Before and after" not in sec
    assert "ReviewMe, reviewed (review-me or a ledger run)" in sec
    assert "prototype, not reviewed" in sec
    # Its reading: the last labelled era, reviewed against not.
    assert ("Median hours open to merge: 24 h for PRs with review-me against 48 h for "
            "those without.") in sec
    assert "Eras ([stats.eras]): prototype from 2025-04-01 (label ai-review-poc)" in sec


def test_history_one_marker_per_era(era_ledger):
    s = StatsSettings(sections=("history",), eras=ERAS)
    page, res = build(era_ledger, s, "en", now=NOW)
    merged = res["history"]["series"]["merged_per_month"]
    assert merged["marker"] == [1, 6, 7]                   # 2025-Q2, 2026-Q3, 2026-Q4
    assert merged["captions"] == ["prototype", "pause", "ReviewMe"]
    sec = page[page.index('<section id="history"'):]
    sec = sec[:sec.index("</section>")]
    chart = sec[sec.index("PRs merged per month"):]
    chart = chart[:chart.index("</svg>")]
    assert chart.count('stroke-dasharray="4 4"') == 3
    for name in ("prototype", "pause", "ReviewMe"):
        assert f">{name}</text>" in chart
    assert "ReviewMe starts" not in sec                    # the single marker is gone
    assert "Dashed lines: the period each era starts in." in sec


def test_an_era_without_prs_yet_is_not_the_roi_after(era_ledger):
    eras = (*ERAS, Era("next", date(2027, 1, 1), "next-label"))
    res = build(era_ledger, StatsSettings(sections=("history", "roi"), eras=eras), "en",
                now=NOW)[1]
    roi = res["roi"]["over_time"]
    assert roi["after_name"] == "ReviewMe"
    assert roi["numbers"]["median_merge_h"] == {"before": 36.0, "after": 36.0}


def test_two_eras_in_one_period_share_a_marker(era_ledger):
    eras = (Era("a", date(2026, 10, 1)), Era("b", date(2026, 10, 10)))
    h = build(era_ledger, StatsSettings(sections=("history",), eras=eras), "en",
              now=NOW)[1]["history"]
    assert h["series"]["merged_per_month"]["captions"] == ["a / b"]


def _bot_login_ledger(tmp: Path, *, per_login: bool) -> Path:
    """Fixture ledger where the prototype bot posted under maria's account on PR 3 (labelled)
    and PR 6 (unlabelled: maria reviewing as a person); `per_login` False = rows of a
    scan from before `review_authors` / `thread_authors`."""
    from dashboard_fixture import history

    from reviewme.ledger import write_part
    from reviewme.pr_history import HISTORY_FILE
    rows = {r["pr"]: r for r in history()}
    later = "2026-10-27T06:00:00+00:00"
    pr3 = {**rows[3], "ts": later, "labels": ["ai-review-poc"], "human_reviews": 3,
           "human_threads": 5, "review_threads": 5}
    pr6 = {**rows[104], "ts": later, "pr": 6, "created_at": "2025-04-15T09:00:00Z",
           "merged_at": "2025-04-16T09:00:00Z", "head_ref": "feature/y",
           "human_reviews": 1, "human_threads": 1, "review_threads": 1}
    if per_login:
        pr3 |= {"review_authors": {"bob": 1, "maria": 2}, "thread_authors": {"bob": 2, "maria": 3},
                # maria (the bot) reviewed first; bob a day after the opening.
                "first_human_review_at": "2025-05-05T10:00:00Z",
                "review_first_at": {"bob": "2025-05-06T09:00:00Z",
                                    "maria": "2025-05-05T10:00:00Z"}}
        pr6 |= {"review_authors": {"maria": 1}, "thread_authors": {"maria": 1},
                "first_human_review_at": "2025-04-15T10:00:00Z",
                "review_first_at": {"maria": "2025-04-15T10:00:00Z"}}
    root = make_ledger(tmp / "ledger")
    write_part(root, HISTORY_FILE, [pr3, pr6])
    return root


BOT_LOGIN_ERAS = (Era("prototype", date(2025, 4, 1), "ai-review-poc", "maria"),
                  Era("pause", date(2026, 7, 1)),
                  Era("ReviewMe", date(2026, 10, 10), "review-me"))


def test_era_bot_login_taken_out_of_human_counts():
    s = StatsSettings(sections=("history",), eras=BOT_LOGIN_ERAS)
    with tempfile.TemporaryDirectory() as tmp:
        page, res = build(_bot_login_ledger(Path(tmp), per_login=True), s, "en", now=NOW)
    h = res["history"]
    rows = {(e["era"], e["variant"]): e for e in h["per_era"]}
    w, wo = rows[("prototype", "with")], rows[("prototype", "without")]
    # PR 3: 3 reviews - maria's 2 = 1, 5 threads - maria's 3 = 2; reviewed by the label.
    assert (w["prs"], w["reviews_per_pr"], w["threads_per_pr"]) == (1, 1.0, 2.0)
    # PR 6 is not labelled: maria reviewed it as a person, nothing is taken out.
    assert (wo["merged"], wo["reviews_per_pr"], wo["threads_per_pr"]) == (1, 1.0, 1.0)
    # First human review: bob's, 24 h after opening, not the bot's after 1 h. PR 6 keeps
    # maria's (a person there): 1 h.
    assert w["median_first_review_h"] == 24.0 and wo["median_first_review_h"] == 1.0
    assert h["rescan_needed"] == {}
    sec = page[page.index('<section id="history"'):]
    assert "reviews and threads by maria on PRs labelled ai-review-poc" in sec
    assert "rescan needed" not in sec


def test_era_bot_login_only_reviewer_leaves_no_first_human_review():
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from reviewme.dashboard.data import EraSpan
    from reviewme.dashboard.sections.history import _corrected
    era = EraSpan("v0", datetime(2025, 1, 1, tzinfo=UTC), "ai-review-poc", "maria")
    ctx = SimpleNamespace(settings=SimpleNamespace(eras=(era,)), eras=[era])
    row = {"created_at": "2025-02-01T00:00:00Z", "labels": ["ai-review-poc"],
           "human_reviews": 1, "human_threads": 2, "first_human_review_at": "2025-02-01T01:00:00Z",
           "review_authors": {"maria": 1}, "thread_authors": {"maria": 2},
           "review_first_at": {"maria": "2025-02-01T01:00:00Z"}}
    out = _corrected(ctx, row)
    assert (out["human_reviews"], out["human_threads"], out["first_human_review_at"],
            out["bot_reviewed"]) == (0, 0, None, True)
    assert row["first_human_review_at"] == "2025-02-01T01:00:00Z"      # input untouched


def test_era_bot_login_on_old_rows_asks_for_a_rescan():
    s = StatsSettings(sections=("history",), eras=BOT_LOGIN_ERAS)
    with tempfile.TemporaryDirectory() as tmp:
        page, res = build(_bot_login_ledger(Path(tmp), per_login=False), s, "en", now=NOW)
    h = res["history"]
    w = next(e for e in h["per_era"] if e["era"] == "prototype"
             and e["variant"] == "with")
    assert (w["reviews_per_pr"], w["threads_per_pr"]) == (3.0, 5.0)      # stored totals
    assert h["rescan_needed"] == {"prototype": 1}                 # PR 3 only
    sec = page[page.index('<section id="history"'):]
    assert ("prototype: 1 labelled PR(s) were scanned before per-login counts "
            "existed, so maria still counts as a human reviewer there: rescan needed") in sec


def test_history_absent():
    with tempfile.TemporaryDirectory() as tmp:
        root = make_ledger(Path(tmp) / "l", with_history=False)
        page, res = build(root, ALL, "en", now=NOW)
        fr = build(root, ALL, "fr", now=NOW)[0]
    assert res["history"] == {"available": False}
    assert "reviewme history --since" in page and "Pas encore d&#x27;historique" in fr


# --------------------------------------------------------------- ROI

ROI = (("hourly_cost", 80.0), ("human_first_review_hours", 20.0),
       ("minutes_per_blocker", 240.0), ("minutes_per_finding", 12.0), ("usd_rate", 0.9))


def test_roi_control_group(ledger):
    r = _build(ledger)[1]["roi"]["control_group"]
    assert r["available"]
    assert r["reviewed"] == {"prs": 3, "merged": 1, "median_first_review_h": 28.0,
                             "median_merge_h": 64.0, "reviews_per_pr": 2.0,
                             "comments_per_pr": 1.0, "bot_first_run_min": 1500}
    assert r["unreviewed"] == {"prs": 1, "merged": 1, "median_first_review_h": None,
                               "median_merge_h": 48.0, "reviews_per_pr": 0.0,
                               "comments_per_pr": 4.0, "bot_first_run_min": None}
    # No delta against a missing or zero baseline.
    assert r["deltas"] == {"median_merge_h": 0.3333, "comments_per_pr": -0.75}


def test_roi_over_time(ledger):
    r = _build(ledger)[1]["roi"]["over_time"]
    assert r["available"] and r["bot_start"].startswith("2026-10-05")
    assert r["numbers"]["median_merge_h"] == {"before": 56.0, "after": 36.0}
    assert r["numbers"]["hotfix_share"] == {"before": 0.25, "after": 0.0}
    with tempfile.TemporaryDirectory() as tmp:
        root = make_ledger(Path(tmp) / "l", with_history=False)
        page, res = build(root, ALL, "en", now=NOW)
    assert res["roi"]["over_time"] == {"available": False, "reason": "no_history"}
    assert "reviewme history --since 2024-01-01" in page


def test_roi_estimate(ledger):
    import dataclasses
    s = dataclasses.replace(ALL, roi=ROI, roi_currency="EUR")
    page, res = _build(ledger, s)
    e = res["roi"]["estimate"]
    # 1 finding acted upon, and it is the BLOCKER fixed before merge: 4 h, counted once.
    assert (e["followed"], e["blockers_fixed_before_merge"], e["hours_saved"]) == (1, 1, 4.0)
    assert (e["value"], e["api_cost_usd"], e["api_cost"]) == (320.0, 2.2, 1.98)
    assert (e["ratio"], e["net"]) == (161.6, 318.02)
    assert (e["break_even_per_pr"], e["followed_per_pr"]) == (0.04, 0.33)
    assert (e["human_first_review_h"], e["human_first_review_source"]) == (20.0, "assumed")
    assert e["bot_first_run_min"] == 1500
    sec = page[page.index('<section id="roi"'):]
    sec = sec[:sec.index("</section>")]
    assert "Assumptions: 80 EUR per hour, 12 min per finding acted upon, 4 h per BLOCKER" in sec
    assert "1 USD = 0.90 EUR" in sec and "[stats.roi]" in sec
    assert "estimated" in sec and "measured, same repository, same period" in sec
    assert 'href="#history"' in sec
    summary = res["summary"]
    assert summary["roi_hours_saved"] == 4.0
    tiles = page[page.index('<section id="summary"'):]
    tiles = tiles[:tiles.index("</section>")]
    assert tiles.count('class="tile"') == 6 and "hours saved (estimated)" in tiles


def test_roi_estimate_hidden_without_settings(ledger):
    page, res = _build(ledger)
    assert res["roi"]["estimate"] == {"available": False}
    assert res["summary"]["roi_hours_saved"] is None
    sec = page[page.index('<section id="roi"'):]
    assert "No estimate: add [stats.roi]" in sec[:sec.index("</section>")]
    tiles = page[page.index('<section id="summary"'):]
    assert tiles[:tiles.index("</section>")].count('class="tile"') == 5


def test_roi_without_control_group():
    from dashboard_fixture import prs

    from reviewme.enrich import PRS_FILE
    from reviewme.ledger import write_part
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "l"
        write_part(root, PRS_FILE, [{**p, "control_group": False} for p in prs()
                                    if p["reviewed"]])
        page, res = build(root, ALL, "fr", now=NOW)
    assert res["roi"]["control_group"] == {"available": False}
    assert "Pas de groupe témoin" in page
