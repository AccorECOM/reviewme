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
    def __init__(self, file_content, comments, reactions=None, threads=None, perm="write"):
        self.file_content, self.comments = file_content, comments
        self.reactions, self.threads, self.perm = reactions or {}, threads or [], perm

    def get_pr(self, n):
        return {"number": n, "head": {"sha": "h2"}, "user": {"login": "alice"},
                "state": "closed", "merged_at": "2099-01-02T00:00:00Z"}

    def list_review_comments(self, n):
        return self.comments

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
    assert by[1]["reactions"] == [{"content": "+1", "user": "dev", "role": "other"}]
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
