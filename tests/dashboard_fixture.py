"""Synthetic ledger for the dashboard tests (and the sample page).

3 PRs (101, 102 by alice; 103 by bob), reviewers `tech` and `us`, two settings states
(cfgA then cfgB), one error run, one baseline run, a BLOCKER fixed before merge,
reactions 👍 / 👎 / 🚀, replies with labels, a `remember` request, run triggers (`pr_build`,
and r4 without one), PR cycle fields, and a control group: PR 104 by carol, merged without
any review (coverage 1 of 2 merged PRs). The rows carry
`message` / `suggestion` / reply `text` as with `[ledger] include_text = true`: none of it
may reach the page.

Triage (`reviewme triage`): comments 1-3 and 5 classified (comment 3 twice, the later
row wins), one patterns row; its `reason` and pattern texts are secrets too, shown only
with `include_text`.

History (`reviewme history`): PRs 1-5 long before the bot (2025; PR 5 by dependabot, PR 4
closed unmerged), PR 101 opened the day before the first live run (r1, 2026-10-05 09:00),
then 104 (unreviewed) and 105 (reviewed per the scan) after it; bug tickets for three
months.
"""
from datetime import UTC, datetime
from pathlib import Path

from reviewme.enrich import ENRICH_FILE, PRS_FILE
from reviewme.ledger import FINDINGS_FILE, RUNS_FILE, write_part
from reviewme.pr_history import HISTORY_BUGS_FILE, HISTORY_FILE
from reviewme.triage import TRIAGE_FILE

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
SECRETS = ("SECRET-MESSAGE", "SECRET-SUGGESTION", "SECRET-REPLY", "SECRET-RULE",
           "SECRET-TITLE", "SECRET-NEARBY", "SECRET-REASON", "SECRET-PATTERN",
           "SECRET-SUGGESTED")

# run_id -> (ts, pr, head, config_hash)
RUNS = {
    "r1": ("2026-10-05T09:00:00+00:00", 101, "h101a", "cfgA"),   # Monday, W41
    "r2": ("2026-10-06T14:00:00+00:00", 101, "h101b", "cfgA"),   # Tuesday, W41
    "r3": ("2026-10-12T10:00:00+00:00", 102, "h102", "cfgB"),    # Monday, W42
    "r4": ("2026-10-20T16:00:00+00:00", 103, "h103", "cfgB"),    # Tuesday, W43
    "b1": ("2026-10-20T16:05:00+00:00", 103, "h103", "cfgB"),    # baseline
}


def _base(run_id: str, variant: str = "live") -> dict:
    ts, pr, head, chash = RUNS[run_id]
    row = {"schema_version": 1, "run_id": run_id, "ts": ts, "repo": "o/r", "pr": pr,
           "head_sha": head, "base_sha": "b", "variant": variant, "engine_commit": "e",
           "config_hash": chash, "dry_run": variant == "baseline", "status": "ok"}
    if run_id != "r4":                    # r4: written before `trigger` existed
        row["trigger"] = "pr_build"
    return row


def _run(run_id, reviewer, cost, *, model="m1", variant="live", **kw) -> dict:
    row = {**_base(run_id, variant), "reviewer": reviewer, "model": model,
           "parsed_ok": True, "num_turns": 10, "max_turns": 30, "subtype": "success",
           "is_error": False, "duration_s": 60, "cost_usd": cost, "input_tokens": 0,
           "output_tokens": 0, "cache_read_tokens": 0, "cache_create_tokens": 0,
           "diff_files": 2, "diff_lines": 100, "context_files": [], "verification": None,
           "threshold": 80, "findings": 1, "fates": {}, "advice": 0}
    row.update(kw)
    return row


def _finding(run_id, reviewer, fp, fate, *, cid=None, sev="IMPORTANT", conf=90,
             rule="null-check", status="confirmed", model="m1", variant="live",
             in_summary=False) -> dict:
    return {**_base(run_id, variant), "reviewer": reviewer, "model": model,
            "fingerprint": fp, "path": "a.swift", "line": 10, "severity_raw": sev,
            "severity": sev, "confidence": conf, "rule_id": rule, "has_evidence": True,
            "verification": {"status": status, "confidence": 90}, "fate": fate,
            "comment_id": cid, "in_summary": in_summary, "preexisting": False,
            "message": "SECRET-MESSAGE about the code", "suggestion": "SECRET-SUGGESTION"}


def findings() -> list[dict]:
    return [
        _finding("r1", "tech", "fp1", "posted", cid=1, sev="BLOCKER", conf=95),
        _finding("r1", "tech", "fp2", "dropped_low", conf=40, status="skipped"),
        _finding("r1", "us", "fp3", "posted", cid=2, rule="ac-missing", conf=80),
        _finding("r2", "tech", "fp1", "deduped", cid=1, sev="BLOCKER", conf=95),
        _finding("r3", "us", "fp4", "posted", cid=3, sev="MINOR", rule="naming", conf=75),
        _finding("r3", "us", "fp5", "dropped_unverified", rule="naming", status="refuted"),
        _finding("r4", "tech", "fp6", "posted", cid=4, sev="BLOCKER", model="m2"),
        _finding("r4", "tech", "fp7", "posted", cid=5, model="m2"),
        _finding("r4", "tech", "fp8", "posted", cid=None, model="m2", status="skipped",
                 in_summary=True),
        _finding("b1", "tech", "fp6", "posted", sev="BLOCKER", variant="baseline"),
    ]


def runs() -> list[dict]:
    return [
        _run("r1", "tech", 0.5, input_tokens=1000, cache_read_tokens=600, output_tokens=200,
             duration_s=90, diff_lines=200,
             context_files=[{"path": "AGENTS.md", "bytes": 1000, "truncated": False,
                             "source": "context.read"}],
             verification={"status": "ok", "confirmed": 1, "cost_usd": 0.1}),
        _run("r1", "us", 0.3, duration_s=40, diff_lines=200),
        _run("r2", "tech", 0.4, status="post_failed", diff_lines=200, findings=1),
        _run("r3", "tech", 0.2, is_error=True, parsed_ok=False, subtype="error_max_turns",
             findings=0),
        _run("r3", "us", 0.1, duration_s=30),
        _run("r4", "tech", 0.7, model="m2", diff_lines=300, input_tokens=4000,
             output_tokens=1000, duration_s=120, findings=3),
        _run("b1", "tech", 0.5, variant="baseline"),
    ]


def enrich() -> list[dict]:
    def line(cid, pr, **kw):
        row = {"schema_version": 1, "ts": "2026-10-27T03:00:00+00:00", "comment_id": cid,
               "pr": pr, "reviewer": "tech", "fingerprint_gone_at": None,
               "file_gone_at": None, "reactions": [], "replies": [],
               "thread_resolved": False, "resolved_by": None,
               "human_comments_nearby": [{"comment_id": 900, "user": "dave", "line": 12,
                                          "text": "SECRET-NEARBY"}]}
        row.update(kw)
        return row

    return [
        line(1, 101, fingerprint_gone_at="h101b", thread_resolved=True, resolved_by="alice",
             reactions=[{"content": "+1", "user": "dave", "role": "other"},
                        {"content": "rocket", "user": "alice", "role": "pr_author"}],
             replies=[{"user": "alice", "role": "pr_author", "ts": "2026-10-06T15:00:00Z",
                       "label": "fixed", "contests": False, "text": "SECRET-REPLY"}]),
        line(2, 101, reactions=[{"content": "-1", "user": "alice", "role": "pr_author"}],
             replies=[{"user": "alice", "role": "pr_author", "ts": "2026-10-05T10:00:00Z",
                       "label": "false_positive", "contests": True, "text": "SECRET-REPLY"},
                      {"user": "dave", "role": "other", "ts": "2026-10-05T11:00:00Z",
                       "label": "question", "contests": False, "text": "SECRET-REPLY"}]),
        line(3, 102, replies=[{"user": "alice", "role": "pr_author",
                               "ts": "2026-10-12T11:00:00Z", "label": "intended",
                               "contests": True, "text": "SECRET-REPLY",
                               "remember": {"trusted": True, "rule": "SECRET-RULE"}}]),
        line(4, 103),
        line(5, 103, reactions=[{"content": "-1", "user": "dave", "role": "other"}],
             first_signal_at="2026-10-20T20:00:00+00:00"),
    ]


def prs() -> list[dict]:
    def pr(n, author, state, merged=None, **kw):
        row = {"schema_version": 1, "ts": "2026-10-27T03:00:00+00:00", "pr": n,
               "state": state, "merged_at": merged, "author": author, "head_sha": "h",
               "commits_after_first_review": 1, "human_comments": 0, "human_replies": 0,
               "human_comments_on_silent_lines": 0, "human_comments_on_dropped_findings": 0,
               "human_comments_text": [{"text": "SECRET-TITLE"}], "reviewed": True,
               "control_group": True}
        row.update(kw)
        return row

    return [pr(101, "alice", "merged", "2026-10-07T00:00:00Z",
               created_at="2026-10-04T08:00:00Z", first_human_review_at="2026-10-05T12:00:00Z",
               human_reviews=2, first_bot_run_at="2026-10-05T09:00:00+00:00", additions=120,
               deletions=30, changed_files=4),
            pr(102, "alice", "open"),
            pr(103, "bob", "open", human_comments=3, human_comments_on_silent_lines=1,
               human_comments_on_dropped_findings=1),
            # Control group: never reviewed. Its comments are not misses.
            pr(104, "carol", "merged", "2026-10-15T09:00:00Z", reviewed=False,
               created_at="2026-10-13T09:00:00Z", first_human_review_at=None,
               human_reviews=0, human_comments=4, additions=None, deletions=None)]


def triage() -> list[dict]:
    def row(cid, pr, reviewer, rule, cause, ts="2026-10-27T06:00:00+00:00"):
        return {"schema_version": 1, "ts": ts, "kind": "finding", "repo": "o/r", "pr": pr,
                "comment_id": cid, "reviewer": reviewer, "rule_id": rule, "cause": cause,
                "confidence": 80, "reason": "SECRET-REASON", "model": "m1", "run_id": "t1"}

    return [
        row(3, 102, "us", "naming", "other", ts="2026-10-26T06:00:00+00:00"),
        row(1, 101, "tech", "null-check", "fixed"),
        row(2, 101, "us", "ac-missing", "missing_context"),
        row(3, 102, "us", "naming", "false_positive"),
        row(5, 103, "tech", "null-check", "false_positive"),
        {"schema_version": 1, "ts": "2026-10-27T06:00:00+00:00", "kind": "patterns",
         "repo": None, "run_id": "t1", "misses": 3, "threads": 4, "calls": 1,
         "cost_usd": 0.04, "model": "m1",
         "patterns": [{"title": "SECRET-PATTERN", "count": 2,
                       "example_comment_ids": [900, 901],
                       "suggested_rule": "SECRET-SUGGESTED"}]},
    ]


def history() -> list[dict]:
    def h(n, created, merged, *, first=None, reviews=0, threads=0, add=40, dele=10,
          hotfix=False, head=None, **kw):
        row = {"schema_version": 1, "ts": "2026-10-27T05:00:00+00:00", "repo": "o/r",
               "pr": n, "author": "alice", "author_is_bot": False, "created_at": created,
               "merged_at": merged, "closed_at": merged or created,
               "state": "merged" if merged else "closed", "is_draft": False,
               "base_ref": "main", "head_ref": head or ("hotfix/x" if hotfix else "feature/x"),
               "additions": add, "deletions": dele, "changed_files": 2, "commits": 2,
               "human_reviews": reviews, "first_human_review_at": first,
               "review_threads": threads, "bot_threads": 0, "human_threads": threads,
               "issue_comments": 1, "labels": [], "is_revert": False,
               "is_hotfix_branch": hotfix, "is_fix_branch": False, "bot_reviewed": False}
        row.update(kw)
        return row

    return [
        h(1, "2025-01-10T09:00:00Z", "2025-01-12T09:00:00Z", first="2025-01-11T09:00:00Z",
          reviews=2, threads=4, add=80, dele=20),
        h(2, "2025-02-03T09:00:00Z", "2025-02-04T09:00:00Z", hotfix=True),
        # Written by a scan from before the hotfix / fix/ split: the stored flag says
        # hotfix, the branch name says an ordinary fix, and the page reads the name.
        h(3, "2025-05-05T09:00:00Z", "2025-05-08T09:00:00Z", first="2025-05-05T21:00:00Z",
          reviews=1, threads=2, add=90, dele=10, head="fix/login", is_hotfix_branch=True),
        h(4, "2025-06-01T09:00:00Z", None),
        h(5, "2025-06-01T09:00:00Z", "2025-06-02T09:00:00Z", author="dependabot",
          author_is_bot=True),
        h(101, "2026-10-04T08:00:00Z", "2026-10-07T00:00:00Z",
          first="2026-10-05T12:00:00Z", reviews=2, threads=3, add=120, dele=30),
        h(104, "2026-10-13T09:00:00Z", "2026-10-15T09:00:00Z", add=80, dele=20,
          head="bugfix/crash"),
        h(105, "2026-10-14T09:00:00Z", "2026-10-15T09:00:00Z", first="2026-10-14T15:00:00Z",
          reviews=1, threads=1, add=150, dele=50, bot_reviewed=True, review_threads=3,
          bot_threads=2),
    ]


def history_bugs() -> list[dict]:
    return [{"schema_version": 1, "ts": "2026-10-27T05:00:00+00:00", "projects": "PROJ",
             "month": m, "bugs": n} for m, n in (("2025-01", 4), ("2025-02", 2),
                                                 ("2026-10", 1))]


def make_ledger(root: Path, *, with_history: bool = True) -> Path:
    write_part(root, FINDINGS_FILE, findings())
    write_part(root, RUNS_FILE, runs())
    write_part(root, ENRICH_FILE, enrich())
    write_part(root, PRS_FILE, prs())
    write_part(root, TRIAGE_FILE, triage())
    if with_history:
        write_part(root, HISTORY_FILE, history())
        write_part(root, HISTORY_BUGS_FILE, history_bugs())
    return root
