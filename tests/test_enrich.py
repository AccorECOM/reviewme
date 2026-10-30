"""Tests for enrichment (`enrich.py`), the quality block and the export.

What matters: telling a remark that was acted on from one that was ignored, a reply that
contests from one that approves, and counting human comments where the bot stayed
silent — without ever naming a developer for a negative remark.

Run: `pytest tests/`.
"""
import logging
import os
import sys
import tarfile
import tempfile
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reviewme import enrich as en
from reviewme import export as ex
from reviewme.ledger import FINDINGS_FILE, RUNS_FILE, write_part
from reviewme.quality import band, compute
from reviewme.reconciler import fingerprint_hash

LOGGER = logging.getLogger("test-enrich")
LOGGER.addHandler(logging.NullHandler())
TS = "2099-01-01T00:00:00+00:00"           # inside any sliding window


def _row(cid, line, fate="posted", conf=90, rule="r1", sev="IMPORTANT", content=None,
         reviewer="tech", variant="live", head="h1", dry=False):
    fp = fingerprint_hash("a.swift", content) if content else ""
    return {"schema_version": 1, "run_id": "x", "ts": TS, "repo": "o/r", "pr": 7,
            "head_sha": head, "variant": variant, "reviewer": reviewer, "fingerprint": fp,
            "path": "a.swift", "line": line, "severity": sev, "confidence": conf,
            "rule_id": rule, "fate": fate, "comment_id": cid, "in_summary": False,
            "dry_run": dry}


class _GH:
    def __init__(self, file_content, comments, reactions=None, threads=None, perm="write",
                 reviews=None, updated=None, comments_by_pr=None):
        self.file_content, self.comments = file_content, comments
        self.reactions, self.threads, self.perm = reactions or {}, threads or [], perm
        self.reviews, self.updated = reviews or {}, updated or []
        self.comments_by_pr = comments_by_pr or {}
        self.calls: list[tuple] = []

    def get_pr(self, n):
        self.calls.append(("get_pr", n))
        return {"number": n, "head": {"sha": "h2"}, "user": {"login": "alice"},
                "state": "closed", "merged_at": "2099-01-02T00:00:00Z",
                "created_at": "2098-12-31T08:00:00Z", "additions": 40, "deletions": 2,
                "changed_files": 3}

    def list_review_comments(self, n):
        self.calls.append(("comments", n))
        return self.comments_by_pr.get(n, self.comments)

    def list_pr_reviews(self, n):
        self.calls.append(("reviews", n))
        return self.reviews.get(n, [])

    def list_prs_updated_since(self, since):
        self.calls.append(("list", since))
        return self.updated

    def review_threads(self, n):
        return self.threads

    def list_comment_reactions(self, cid):
        return self.reactions.get(cid, [])

    def list_pr_commits(self, n):
        return [{"sha": "h1"}, {"sha": "h2"}]

    def get_file_at(self, path, ref):
        return self.file_content

    def get_permission(self, login):
        return self.perm


def _bot(cid, line):
    return {"id": cid, "path": "a.swift", "line": line, "in_reply_to_id": None,
            "body": f"**[IMPORTANT]** x\n<!-- reviewme:tech:{'a' * 10} -->",
            "user": {"login": "bot"}}


def _human(cid, line, body, parent=None, user="dev"):
    return {"id": cid, "path": "a.swift", "line": line, "in_reply_to_id": parent,
            "body": body, "user": {"login": user}, "created_at": "2099-01-01T01:00:00Z"}


def test_negation_is_not_a_label():
    assert en.label_reply("pas corrigé pour l'instant") == "other"
    assert en.label_reply("pas faux, je change") == "other"
    assert en.label_reply("not fixed yet") == "other"
    assert en.label_reply("Ce n'est pas le cas ici") == "false_positive"


def test_deleted_file_is_not_follow_through():
    """A 404 (file deleted or renamed) does not prove the remark was addressed."""
    rows = [_row(1, 10, content="let x = y!")]
    gh = _GH(None, [_bot(1, 10)])
    (line,), _ = en.enrich_pr(gh, 7, rows, {}, with_text=False, logger=LOGGER)
    assert line["fingerprint_gone_at"] is None and line["file_gone_at"] == "h2"


def test_other_bot_comment_is_not_a_miss():
    rows = [_row(1, 10, content="let x = y!")]
    other = _human(8, 90, "Sonar: complexity")
    other["user"] = {"login": "sonarcloud[bot]", "type": "Bot"}
    _, pr = en.enrich_pr(_GH("", [_bot(1, 10), other]), 7, rows, {}, with_text=False,
                         logger=LOGGER)
    assert pr["human_comments"] == 0 and pr["human_comments_on_silent_lines"] == 0


def test_reply_labels():
    assert en.label_reply("Faux positif, ce n'est pas nil ici") == "false_positive"
    assert en.label_reply("C'est volontaire, on garde") == "intended"
    assert en.label_reply("Corrigé dans le commit suivant") == "fixed"
    assert en.label_reply("nit, plus tard") == "unimportant"
    assert en.label_reply("Tu peux préciser ?") == "question"
    assert en.label_reply("ok") == "other"


def test_window():
    assert en.parse_since("14d") == timedelta(days=14)
    assert en.parse_since("36h") == timedelta(hours=36)


def _enrich_one(with_text=False):
    rows = [_row(1, 10, content="let x = y!"), _row(2, 20, content="let z = 1"),
            _row(None, 40, fate="dropped_low", conf=40, content="let w = 2")]
    comments = [
        _bot(1, 10), _bot(2, 20),
        _human(3, 10, "Corrigé, merci", parent=1),
        _human(4, 20, "Faux positif : c'est voulu", parent=2, user="alice"),
        _human(5, 41, "A weak self is missing here"),          # near a dropped finding
        _human(6, 90, "Naming?"),                            # where the bot said nothing
        _human(7, 21, "@reviewme remember No force unwrap in tests", parent=2),
    ]
    gh = _GH("let z = 1\nlet w = 2\n", comments,
             reactions={1: [{"content": "+1", "user": {"login": "dev"}}],
                        2: [{"content": "-1", "user": {"login": "alice"}}]},
             threads=[{"isResolved": True, "resolvedBy": {"login": "dev"},
                       "comments": {"nodes": [{"databaseId": 1}]}}])
    return en.enrich_pr(gh, 7, rows, {}, with_text=with_text, logger=LOGGER)


def test_follow_through_and_reactions():
    lines, _ = _enrich_one()
    by = {line["comment_id"]: line for line in lines}
    assert by[1]["fingerprint_gone_at"] == "h2"          # `let x = y!` is gone
    assert by[2]["fingerprint_gone_at"] is None          # `let z = 1` is still there
    assert by[1]["reactions"] == [{"content": "+1", "user": "dev", "role": "other",
                                   "ts": None}]
    assert by[2]["reactions"][0]["role"] == "pr_author"
    assert by[1]["thread_resolved"] is True and by[1]["resolved_by"] == "dev"
    assert by[2]["replies"][0]["contests"] is True
    # Without `include_text`, the rule itself is not written: it is PR text.
    assert by[2]["replies"][1]["remember"] == {"trusted": True}
    assert "text" not in by[1]["replies"][0]


def test_per_pr_misses_and_filter_cost():
    _, pr = _enrich_one()
    assert pr["state"] == "merged" and pr["commits_after_first_review"] == 1
    assert pr["human_comments"] == 2
    assert pr["human_comments_on_dropped_findings"] == 1
    assert pr["human_comments_on_silent_lines"] == 1
    assert "human_comments_text" not in pr


def test_texts_only_when_requested():
    lines, pr = _enrich_one(with_text=True)
    assert lines[0]["replies"][0]["text"] == "Corrigé, merci"
    assert lines[1]["replies"][1]["remember"]["rule"] == "No force unwrap in tests"
    assert {c["text"] for c in pr["human_comments_text"]} >= {"Naming?"}


def test_quality_and_calibration():
    lines, pr = _enrich_one()
    rows = [_row(1, 10, content="let x = y!"), _row(2, 20, content="let z = 1"),
            _row(None, 40, fate="dropped_low", conf=40, content="let w = 2")]
    q = compute(rows, [], lines, [pr])
    t = q["by_reviewer"]["tech"]
    assert t["posted"] == 2 and t["followed"] == 1 and t["contested"] == 1
    assert t["approved"] == 1
    assert q["calibration"]["0-50"]["fates"] == {"dropped_low": 1}
    assert q["calibration"]["86-100"]["followed"] == 1
    assert q["misses"]["on_dropped_findings"] == 1
    assert band(70) == "51-70" and band(71) == "71-85"


def test_global_and_fallback_excluded_from_denominator():
    """A finding delivered in a global comment has no line to track."""
    rows = [_row(1, 10, content="a"), {**_row(55, 20, content="b"), "in_summary": True},
            {**_row(55, 30, content="c"), "in_summary": True}]
    q = compute(rows, [], [], [])
    assert q["by_reviewer"]["tech"]["posted"] == 1 and q["posted_in_summary"] == 2


def test_calibration_counts_a_finding_once():
    """Reproduced on every push, a finding weighs only once in the calibration."""
    rows = [{**_row(None, 40, fate="dropped_low", conf=40, content="a"), "run_id": str(i),
             "ts": f"2099-01-0{i + 1}T00:00:00+00:00"} for i in range(3)]
    assert compute(rows, [], [], [])["calibration"]["0-50"]["findings"] == 1


def test_question_does_not_approve():
    rows = [_row(1, 10, content="a")]
    enrich = [{"comment_id": 1, "reactions": [],
               "replies": [{"label": "question", "contests": False}]}]
    assert compute(rows, [], enrich, [])["by_reviewer"]["tech"]["approved"] == 0


def test_baseline_vs_live():
    rows = [_row(None, 10, content="a", dry=True), _row(None, 20, content="b", dry=True),
            _row(None, 10, content="a", variant="baseline", dry=True),
            _row(None, 30, content="c", variant="baseline", dry=True)]
    v = compute(rows, [], [], [])["baseline_vs_live"]["tech"]
    assert (v["baseline"], v["live"], v["both"], v["prs"]) == (2, 2, 1, 1)


def test_rule_contested_three_times():
    rows = [_row(i, i, rule="r-noisy") for i in range(1, 4)]
    enrich = [{"comment_id": i, "replies": [{"label": "false_positive", "contests": True}],
               "reactions": []} for i in range(1, 4)]
    q = compute(rows, [], enrich, [])
    assert q["contested_rules"][0]["rule"] == "tech:r-noisy"


def test_export_archive_and_report():
    lines, pr = _enrich_one()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "ledger"
        write_part(root, FINDINGS_FILE, [_row(1, 10, content="let x = y!", sev="BLOCKER"),
                                       _row(2, 20, content="let z = 1")])
        write_part(root, RUNS_FILE, [{"ts": TS, "pr": 7, "variant": "live", "reviewer": "tech",
                                    "cost_usd": 0.8, "duration_s": 60, "num_turns": 14,
                                    "diff_lines": 120}])
        write_part(root, en.ENRICH_FILE, lines)
        write_part(root, en.PRS_FILE, [pr])
        archive, report = ex.export(root, Path(tmp) / "out", timedelta(days=7), repo="o/r",
                                     stats_file=Path(tmp) / "absent.json")
        with tarfile.open(archive) as tar:
            names = {Path(n).name for n in tar.getnames()}
            member = next(m for m in tar.getmembers() if m.name.endswith("enrich.jsonl"))
            archive_text = tar.extractfile(member).read().decode()
        text = report.read_text()
    assert {"findings.jsonl", "runs.jsonl", "enrich.jsonl", "prs.jsonl", "quality.json",
            "SCHEMA.md", "weekly-report.md"} <= names
    assert "## Usual stats" in text and "## Fun stats" in text
    assert "Save of the week" in text and "discussion_r1" in text
    # Participation only: the author who contested is not singled out.
    assert "alice" not in text.split("## Fun stats")[0]
    assert "dev" not in archive_text and "u-" in archive_text


def test_report_in_french():
    lines, pr = _enrich_one()
    data = {"findings.jsonl": [_row(1, 10, content="let x = y!")], "runs.jsonl": [],
            "enrich.jsonl": lines, "prs.jsonl": [pr]}
    text = ex.report(data, ex.quality_of(data), "o/r", "", "fr")
    assert "## Stats classiques" in text and "## Stats fun" in text


def test_every_reaction_kept_except_machines():
    """🚀, 👀… are kept for the dashboard's vocabulary; other bots and the bot are not."""
    rows = [_row(1, 10, content="let x = y!")]
    reactions = {1: [
        {"content": "rocket", "user": {"login": "dev"}, "created_at": "2099-01-01T05:00:00Z"},
        {"content": "eyes", "user": {"login": "alice"}, "created_at": "2099-01-01T03:00:00Z"},
        {"content": "+1", "user": {"login": "copilot[bot]", "type": "Bot"},
         "created_at": "2099-01-01T00:30:00Z"},
        {"content": "heart", "user": {"login": "reviewme-bot"},
         "created_at": "2099-01-01T00:10:00Z"},
    ]}
    gh = _GH("", [_bot(1, 10), _human(3, 10, "ok", parent=1)], reactions=reactions)
    (line,), _ = en.enrich_pr(gh, 7, rows, {}, with_text=False, logger=LOGGER,
                              bot_login="reviewme-bot")
    assert [r["content"] for r in line["reactions"]] == ["rocket", "eyes"]
    assert line["reactions"][1] == {"content": "eyes", "user": "alice", "role": "pr_author",
                                    "ts": "2099-01-01T03:00:00Z"}
    # Earliest human signal: the reply at 01:00, before the reactions; machines ignored.
    assert line["first_signal_at"] == "2099-01-01T01:00:00Z"


def test_first_signal_survives_a_removed_reaction():
    rows = [_row(1, 10, content="let x = y!")]
    previous = {1: {"comment_id": 1, "first_signal_at": "2098-12-31T23:00:00+00:00"}}
    (line,), _ = en.enrich_pr(_GH("", [_bot(1, 10)]), 7, rows, previous, with_text=False,
                              logger=LOGGER)
    assert line["first_signal_at"] == "2098-12-31T23:00:00+00:00"
    assert en.earliest("2099-01-01T01:00:00Z", "2099-01-01T00:30:00+00:00", None,
                       "bad") == "2099-01-01T00:30:00+00:00"


def test_non_vote_reactions_keep_the_verdict():
    """👀 is a human signal (answered) but neither approves nor contests."""
    rows = [_row(1, 10, content="a")]
    enrich = [{"comment_id": 1, "replies": [],
               "reactions": [{"content": "eyes", "user": "dev", "role": "other"}]}]
    t = compute(rows, [], enrich, [])["by_reviewer"]["tech"]
    assert (t["approved"], t["contested"], t["answered"]) == (0, 0, 1)


def _review(rid, user, at, state="APPROVED", bot=False):
    return {"id": rid, "user": {"login": user, **({"type": "Bot"} if bot else {})},
            "state": state, "submitted_at": at}


def test_pr_cycle_fields():
    """First human review: neither the author, nor the bot (by its comments), nor a tool."""
    rows = [_row(1, 10, content="let x = y!")]
    bot = {**_bot(1, 10), "pull_request_review_id": 50}
    reviews = {7: [_review(50, "alice-pat", "2099-01-01T00:05:00Z", "COMMENTED"),  # the bot
                   _review(51, "alice", "2099-01-01T00:10:00Z", "COMMENTED"),      # author
                   _review(52, "sonar[bot]", "2099-01-01T00:20:00Z", bot=True),
                   _review(53, "carol", None, "PENDING"),
                   _review(54, "dev", "2099-01-01T09:00:00Z", "CHANGES_REQUESTED"),
                   _review(55, "carol", "2099-01-01T07:00:00Z")]}
    _, pr = en.enrich_pr(_GH("", [bot], reviews=reviews), 7, rows, {}, with_text=False,
                         logger=LOGGER)
    assert pr["reviewed"] is True and pr["control_group"] is False
    assert pr["created_at"] == "2098-12-31T08:00:00Z"
    assert pr["first_human_review_at"] == "2099-01-01T07:00:00Z" and pr["human_reviews"] == 2
    assert (pr["additions"], pr["deletions"], pr["changed_files"]) == (40, 2, 3)
    assert pr["first_bot_run_at"] == TS and pr["merged_at"] == "2099-01-02T00:00:00Z"


def test_enrich_writes_reviewed_and_unreviewed_prs():
    """Control group: every PR updated in the window with no run of any kind."""
    from reviewme.ledger import read_all
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_part(root, FINDINGS_FILE, [_row(1, 10, content="let x = y!")])
        write_part(root, RUNS_FILE, [
            {"ts": "2098-06-01T00:00:00+00:00", "pr": 7, "variant": "live", "head_sha": "h0"},
            {"ts": TS, "pr": 7, "variant": "live", "head_sha": "h1"},
            {"ts": TS, "pr": 8, "variant": "live", "head_sha": "h8"},        # found nothing
            {"ts": TS, "pr": 9, "variant": "live", "head_sha": "h9", "dry_run": True}])
        human = _human(30, 5, "Why?", user="carol")
        gh = _GH("", [_bot(1, 10)], updated=[
            {"number": n, "user": {"login": "erin"}, "state": "closed",
             "merged_at": "2099-01-03T00:00:00Z", "created_at": "2099-01-01T00:00:00Z",
             "head": {"sha": f"x{n}"}} for n in (7, 8, 9, 20)] + [
            {"number": 21, "user": {"login": "dependabot[bot]", "type": "Bot"},
             "state": "open", "merged_at": None}],
            comments_by_pr={20: [human, _human(31, 5, "Because", parent=30, user="erin")]},
            reviews={20: [_review(60, "carol", "2099-01-02T00:00:00Z")]})
        r = en.enrich(gh, root, timedelta(days=3650), LOGGER, with_text=False,
                      control_group=True)
        lines = {p["pr"]: p for p in read_all(root, en.PRS_FILE)}
    assert r == {"prs": 2, "unreviewed": 1, "comments": 1}
    assert set(lines) == {7, 8, 20}                  # 9: dry-run only; 21: a tool's PR
    assert lines[7]["reviewed"] and lines[7]["control_group"]
    assert lines[7]["first_bot_run_at"] == "2098-06-01T00:00:00+00:00"   # before the window
    assert lines[8]["reviewed"] is True
    u = lines[20]
    assert (u["reviewed"], u["state"], u["author"], u["merged_at"]) == (
        False, "merged", "erin", "2099-01-03T00:00:00Z")
    assert (u["human_comments"], u["human_replies"], u["human_reviews"]) == (1, 1, 1)
    assert u["first_human_review_at"] == "2099-01-02T00:00:00Z"
    assert u["additions"] is None and u["first_bot_run_at"] is None
    # Bounded cost: no `get_pr` for an unreviewed PR, two calls each.
    assert [c for c in gh.calls if c[1] == 20] == [("comments", 20), ("reviews", 20)]


def test_control_group_off_and_single_pr():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_part(root, FINDINGS_FILE, [_row(1, 10, content="let x = y!")])
        gh = _GH("", [_bot(1, 10)], updated=[{"number": 20, "user": {"login": "erin"}}])
        r = en.enrich(gh, root, timedelta(days=3650), LOGGER, with_text=False)
        assert r["unreviewed"] == 0 and not any(c[0] == "list" for c in gh.calls)
        r = en.enrich(gh, root, timedelta(days=3650), LOGGER, with_text=False, only_pr=7,
                      control_group=True)
        assert r["unreviewed"] == 0 and not any(c[0] == "list" for c in gh.calls)


def test_control_group_setting(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.delenv("REVIEWME_CONTROL_GROUP", raising=False)
    assert en.control_group(None) is True
    assert en.control_group(SimpleNamespace(stats_control_group=False)) is False
    monkeypatch.setenv("REVIEWME_CONTROL_GROUP", "1")
    assert en.control_group(SimpleNamespace(stats_control_group=False)) is True


def test_control_group_is_not_a_miss():
    unreviewed = {"pr": 20, "reviewed": False, "human_comments": 5}
    reviewed = {"pr": 7, "human_comments": 2, "human_comments_on_silent_lines": 1}
    assert compute([], [], [], [unreviewed, reviewed])["misses"]["human_comments"] == 2
