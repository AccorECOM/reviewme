"""Tests for the multi-reviewer fan-out.

Covers the three mechanisms that are expensive when they fail:
  - SELECTION: a reviewer that fires on every PR makes the project pay for nothing;
  - the CAP: four overzealous reviewers drown the developer and the bot gets disabled;
  - ISOLATION: two reviewers on the same line must not cancel each other out.

Run: `pytest tests/` or `python tests/test_fanout.py`.
"""
import logging
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reviewme.config import Config
from reviewme.diff_utils import LinePos
from reviewme.models import Finding, ReviewResult, Severity
from reviewme.projects import (
    ProjectConfig,
    ProjectConfigError,
    ReviewerSpec,
    load_project,
    select_reviewers,
)
from reviewme.reconciler import (
    PrContext,
    _apply_cap,
    marker_key,
    prepare,
)

LOGGER = logging.getLogger("test")
LOGGER.addHandler(logging.NullHandler())
PR = {"number": 7, "head": {"sha": "abcdef1234567890"}, "title": "PR de test"}


def _spec(rid, **kw):
    return ReviewerSpec(id=rid, project="p", system_prompt="x", **kw)


def _project(*specs):
    return ProjectConfig(name="p", directory=None, reviewers=tuple(specs))


def _config(**kw):
    base = dict(github_token="t", github_repo="o/r", confidence_threshold=80)
    base.update(kw)
    return Config(**base)


# --------------------------------------------------------------- selection

def test_reviewer_without_when_always_fires():
    kept, skipped = select_reviewers(_project(_spec("tech")), ["src/Main.swift"])
    assert [s.id for s in kept] == ["tech"] and not skipped


def test_when_paths_avoids_paying_for_nothing():
    project = _project(_spec("i18n", when_paths=("**/*.strings", "**/values*/strings.xml")))
    kept, skipped = select_reviewers(project, ["src/Main.swift", "README.md"])
    assert not kept and "when.paths" in skipped[0][1]

    kept, _ = select_reviewers(project, ["src/Main.swift", "fr.lproj/Localizable.strings"])
    assert [s.id for s in kept] == ["i18n"]


def test_when_paths_matches_at_root_and_nested():
    project = _project(_spec("i18n", when_paths=("**/*.strings",)))
    for path in ("Localizable.strings", "a/b/c/Localizable.strings"):
        kept, _ = select_reviewers(project, [path])
        assert [s.id for s in kept] == ["i18n"], path


def test_missing_requires_skips_without_error():
    """Jira not configured or ticket not found: the `us` reviewer is skipped, not failed."""
    project = _project(_spec("tech"), _spec("us", requires=("jira_ticket",)))
    kept, skipped = select_reviewers(project, ["a.py"], available_context=set())
    assert [s.id for s in kept] == ["tech"]
    assert skipped[0][0].id == "us" and "jira_ticket" in skipped[0][1]

    kept, _ = select_reviewers(project, ["a.py"], available_context={"jira_ticket"})
    assert [s.id for s in kept] == ["tech", "us"]


def test_order_by_priority():
    project = _project(_spec("i18n", priority=30), _spec("tech", priority=10), _spec("us", priority=20))
    kept, _ = select_reviewers(project, ["a.py"])
    assert [s.id for s in kept] == ["tech", "us", "i18n"]


# --------------------------------------------------------------- cap

def _item(reviewer, sev, conf, line):
    return {"path": "a.swift", "line": line, "side": "RIGHT", "body": "b",
            "_severity": sev, "_confidence": conf, "_reviewer": reviewer}


class _Prep:
    def __init__(self, rid, batch):
        self.reviewer_id, self.batch, self.counts = rid, batch, {}


def test_cap_keeps_the_most_severe():
    a = _Prep("tech", [_item("tech", Severity.MINOR, 99, 1), _item("tech", Severity.BLOCKER, 85, 2)])
    b = _Prep("i18n", [_item("i18n", Severity.IMPORTANT, 90, 3)])
    kept = _apply_cap([a, b], 2, LOGGER, 7)
    assert [c["_severity"] for c in kept] == [Severity.BLOCKER, Severity.IMPORTANT]
    assert a.counts["capped"] == 1 and b.counts["capped"] == 0


def test_cap_breaks_ties_by_confidence():
    a = _Prep("tech", [_item("tech", Severity.BLOCKER, 82, 1), _item("tech", Severity.BLOCKER, 97, 2)])
    kept = _apply_cap([a], 1, LOGGER, 7)
    assert kept[0]["_confidence"] == 97


def test_no_cap_when_under_the_limit():
    a = _Prep("tech", [_item("tech", Severity.MINOR, 90, 1)])
    assert len(_apply_cap([a], 10, LOGGER, 7)) == 1 and a.counts == {}


# --------------------------------------------------------------- preparation

def _result(findings, summary="Résumé.", parsed_ok=True):
    return ReviewResult(status="COMMENT", summary=summary, findings=findings, parsed_ok=parsed_ok)


def _finding(line=10, sev=Severity.BLOCKER, conf=95, path="a.swift", snippet="let x = y!"):
    return Finding(path=path, line=line, severity=sev, message="Souci ici",
                   snippet=snippet, confidence=conf)


def _ctx():
    # `valid_positions` returns {path: {line: LinePos}}: here line 10 can be commented on.
    return PrContext(valid={"a.swift": {10: LinePos(10, "let x = y!", added=True)}})


def test_global_mode_produces_no_inline():
    """A `us` reviewer in global mode posts ONE comment: "AC3 not covered" has no line."""
    prep = prepare(PR, _config(), _ctx(), _result([_finding()]), LOGGER, "us", output_mode="global")
    assert prep.batch == [] and prep.global_body and prep.global_update_id is None
    assert marker_key(prep.global_body) == ("us", marker_key(prep.global_body)[1])


def test_global_mode_updates_instead_of_stacking():
    ctx = _ctx()
    from reviewme.reconciler import summary_fingerprint
    marker = summary_fingerprint("us")
    ctx.global_by_marker[marker_key(marker)] = {"id": 4242, "body": marker}
    prep = prepare(PR, _config(), ctx, _result([]), LOGGER, "us", output_mode="global")
    assert prep.global_update_id == 4242


def test_two_reviewers_same_line_do_not_cancel_out():
    """The bug D3 prevents: without the reviewer in the key, the second one would be swallowed."""
    ctx = _ctx()
    tech = prepare(PR, _config(), ctx, _result([_finding()]), LOGGER, "tech", prefix="[TECH]")
    # simulate that the `tech` comment is already live on the PR
    for item in tech.batch:
        ctx.inline_by_marker[marker_key(item["body"])] = {"id": 1, "position": 3, "body": item["body"]}

    again = prepare(PR, _config(), ctx, _result([_finding()]), LOGGER, "tech")
    assert again.batch == [] and again.counts["deduped"] == 1        # dedup within the same reviewer

    other = prepare(PR, _config(), ctx, _result([_finding()]), LOGGER, "i18n", prefix="[I18N]")
    assert len(other.batch) == 1 and other.counts["deduped"] == 0    # isolation


def test_confidence_threshold_enforced():
    prep = prepare(PR, _config(), _ctx(), _result([_finding(conf=50)]), LOGGER, "tech")
    assert prep.batch == [] and prep.counts["dropped_low"] == 1


def test_imprecise_line_is_reanchored_by_snippet():
    """The LLM is off by a few lines: the right one is found by its content."""
    prep = prepare(PR, _config(), _ctx(), _result([_finding(line=999)]), LOGGER, "tech")
    assert len(prep.batch) == 1 and prep.batch[0]["line"] == 10


def test_out_of_diff_finding_goes_to_summary():
    """A file absent from the diff cannot be commented on (anti-422): it goes to the summary."""
    outside = _finding(path="jamais/touche.swift", snippet="autre chose")
    prep = prepare(PR, _config(), _ctx(), _result([outside]), LOGGER, "tech")
    assert prep.batch == [] and prep.counts["out_of_diff"] == 1
    assert "jamais/touche.swift" in prep.summary


def test_unreadable_json_falls_back_to_global_comment():
    prep = prepare(PR, _config(), _ctx(), _result([], parsed_ok=False), LOGGER, "tech")
    assert prep.counts["fallback"] and prep.global_body and not prep.batch


def test_cli_error_posts_nothing():
    """A gateway 401 relayed by the CLI must never land on the PR as a comment."""
    err = _result([], parsed_ok=False)
    err.raw = "Failed to authenticate. API Error: 401 Authentication Error, Key Hash = abc"
    err.metadata["is_error"] = True
    prep = prepare(PR, _config(), _ctx(), err, LOGGER, "tech")
    assert prep.counts["cli_error"] and not prep.counts["fallback"]
    assert prep.global_body is None and not prep.batch


def test_prefix_only_in_multi_reviewer():
    solo = prepare(PR, _config(), _ctx(), _result([_finding()]), LOGGER, "tech")
    multi = prepare(PR, _config(), _ctx(), _result([_finding()]), LOGGER, "tech", prefix="[TECH]")
    assert "[TECH]" not in solo.batch[0]["body"] and "[TECH]" in multi.batch[0]["body"]


# --------------------------------------------------------------- git context robustness

def test_base_branch_fetch_never_breaks_the_review():
    """On a shallow CI clone or a large repository, this fetch can drag on. It is
    best-effort: its failure must not bring down the review, which does not depend on it."""
    import subprocess as sp
    import types
    from unittest.mock import patch

    import reviewme.run as R

    class _GH:
        def get_pr_diff(self, n):
            return ""            # exits right after the fetch: that is exactly what we test
        def get_pr_files(self, n):
            return []

    pr = {"number": 1, "head": {"sha": "a" * 40}, "base": {"ref": "develop"},
          "title": "t", "user": {}}
    cfg = types.SimpleNamespace(repo_path=".", dry_run=True)

    for failure in (sp.TimeoutExpired("git fetch", 30), OSError("git not found")):
        with patch.object(R.subprocess, "run", side_effect=failure):
            out = R.run_review(pr, cfg, _GH(), LOGGER, force=True)
        assert "error" not in out, f"{type(failure).__name__} brought down the review: {out}"


def test_dry_run_shows_what_would_be_posted():
    """Without the content, a dry run allows neither judging the review nor tuning the threshold."""
    import logging as _logging

    from reviewme.reconciler import post_all

    lines = []

    class _Catch(_logging.Handler):
        def emit(self, record):
            lines.append(record.getMessage())

    logger = _logging.getLogger("test-dryrun")
    logger.addHandler(_Catch())
    logger.setLevel(_logging.INFO)   # otherwise INFO records are filtered by the root level
    prep = prepare(PR, _config(dry_run=True), _ctx(), _result([_finding()]), logger, "tech")
    post_all(PR, _config(dry_run=True), None, [prep], logger)

    joint = "\n".join(lines)
    assert "a.swift:10" in joint          # where
    assert "Souci ici" in joint           # what
    assert "95" in joint                  # with which confidence
    assert "reviewme:" not in joint       # the technical marker stays hidden


def test_unknown_requires_is_rejected_at_load():
    """`requires = ["jira"]` instead of "jira_ticket" would make the reviewer inert forever."""
    import reviewme.projects as P
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_project(root, "p", {"us": ('requires = ["jira"]\n', "persona")})
        try:
            P.load_project("p", root)
        except P.ProjectConfigError as e:
            assert "jira_ticket" in str(e)      # the message gives the expected value
        else:
            raise AssertionError("ProjectConfigError expected")


def test_reviewer_id_is_free_form():
    """The names tech/us/i18n are conventions, not mandatory values."""
    import reviewme.projects as P
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_project(root, "p", {"securite-mobile": ('output_mode = "inline"\n', "persona"),
                                   "perf": ("", "persona")})
        assert sorted(r.id for r in P.load_project("p", root).reviewers) == ["perf", "securite-mobile"]


def test_inline_and_global_are_counted_separately():
    """A global comment read as "1 inline" although no finding had gone through."""
    from reviewme.reconciler import post_all

    class _GH:
        def __init__(self):
            self.global_count = 0
        def create_review(self, *a, **k):
            raise AssertionError("no inline comment should have been posted")
        def post_issue_comment(self, pr, body):
            self.global_count += 1
        def update_issue_comment(self, cid, body):
            self.global_count += 1

    # a `global` reviewer that finds nothing still posts its summary comment
    prep = prepare(PR, _config(), _ctx(), _result([]), LOGGER, "us", output_mode="global")
    gh = _GH()
    totals = post_all(PR, _config(), gh, [prep], LOGGER)

    assert gh.global_count == 1
    assert totals["posted_global"] == 1
    assert totals["posted_inline"] == 0        # that is the missing distinction
    assert totals["posted"] == 1               # the total stays correct


def test_review_body_signed_once():
    """Several speaking reviewers share one review body: one footer, not one each."""
    from reviewme.reconciler import post_all

    class _GH:
        def __init__(self):
            self.bodies = []
        def create_review(self, pr, sha, body, comments, event):
            self.bodies.append(body)
            return {}
        def list_review_comments(self, pr, review_id=None):
            return []

    ctx = _ctx()
    tech = prepare(PR, _config(), ctx, _result([_finding()]), LOGGER, "tech", prefix="[TECH]")
    i18n = prepare(PR, _config(), ctx, _result([_finding()]), LOGGER, "i18n", prefix="[I18N]")
    gh = _GH()
    post_all(PR, _config(dry_run=False), gh, [tech, i18n], LOGGER)
    body = gh.bodies[0]
    assert "— tech" in body and "— i18n" in body
    assert body.count("automated review_") == 1 and body.rstrip().endswith(f"`{PR['head']['sha'][:8]}`")


def test_empty_summary_leaves_only_the_signature():
    """Findings that say it all: no bare reviewer title, the review body is the signature."""
    from reviewme.reconciler import post_all

    class _GH:
        def __init__(self):
            self.bodies = []
        def create_review(self, pr, sha, body, comments, event):
            self.bodies.append(body)
            return {}

    prep = prepare(PR, _config(), _ctx(), _result([_finding()], summary=""), LOGGER, "tech")
    assert prep.summary == "" and len(prep.batch) == 1
    gh = _GH()
    post_all(PR, _config(dry_run=False), gh, [prep], LOGGER)
    assert gh.bodies == [f"_ReviewMe · automated review_ · commit `{PR['head']['sha'][:8]}`"]


def test_suggestion_on_its_own_line():
    """The fix is set apart from the defect: the reader finds it without reading it all."""
    f = _finding()
    f.suggestion = "Unwrap with `guard let`."
    prep = prepare(PR, _config(), _ctx(), _result([f]), LOGGER, "tech")
    body = prep.batch[0]["body"]
    assert body.index("Souci ici") < body.index("_Suggestion:_ Unwrap with `guard let`.")


# --------------------------------------------------------------- lifecycle on a new commit

def _existing_comment(ctx, finding, reviewer="tech", position=3, cid=1):
    """Simulate a comment already posted by the bot on this finding."""
    from reviewme.reconciler import fingerprint_hash
    ctx.inline_by_marker[(reviewer, fingerprint_hash(finding.path, "let x = y!"))] = {
        "id": cid, "position": position, "path": finding.path, "line": finding.line,
        "body": "**[BLOCKER]** Souci ici", "user": {"login": "bot"},
    }


def test_new_commit_issue_still_there_line_unchanged():
    """Case 1: nothing moves -> dedup, no new comment, no noise."""
    ctx = _ctx()
    f = _finding()
    _existing_comment(ctx, f, position=3)
    prep = prepare(PR, _config(), ctx, _result([f]), LOGGER, "tech")
    assert prep.batch == [] and prep.replies == []
    assert prep.counts["deduped"] == 1 and prep.counts["resolus"] == 0


def test_new_commit_issue_still_there_line_moved():
    """Case 2: the line moved but the issue remains -> reply in the thread,
    never a new top-level comment (otherwise the PR fills up with duplicates)."""
    ctx = _ctx()
    f = _finding()
    _existing_comment(ctx, f, position=None)      # GitHub marked the comment as outdated
    prep = prepare(PR, _config(), ctx, _result([f]), LOGGER, "tech")
    assert prep.batch == []
    assert len(prep.replies) == 1
    assert "Still relevant" in prep.replies[0][1]
    assert prep.counts["resolus"] == 0


def test_new_commit_issue_fixed():
    """Case 3: the line changed AND the finding disappeared -> reply that it is resolved."""
    ctx = _ctx()
    _existing_comment(ctx, _finding(), position=None)
    prep = prepare(PR, _config(), ctx, _result([]), LOGGER, "tech")   # no finding anymore
    assert len(prep.replies) == 1
    assert "no longer appears" in prep.replies[0][1]
    assert prep.counts["resolus"] == 1


def test_finding_gone_but_line_unchanged_concludes_nothing():
    """Safeguard: a model that varies from one run to the next must not announce an
    imaginary fix. Without a change on the line, stay silent."""
    ctx = _ctx()
    _existing_comment(ctx, _finding(), position=3)   # the line did NOT move
    prep = prepare(PR, _config(), ctx, _result([]), LOGGER, "tech")
    assert prep.replies == [] and prep.counts["resolus"] == 0


def test_another_reviewer_does_not_close_the_first_ones_comments():
    ctx = _ctx()
    _existing_comment(ctx, _finding(), reviewer="tech", position=None)
    prep = prepare(PR, _config(), ctx, _result([]), LOGGER, "architect")
    assert prep.replies == [] and prep.counts["resolus"] == 0


def test_feedback_produces_attributed_rules():
    """A rule without an author cannot be challenged: attribution is the mechanism."""
    from reviewme.feedback import collecter, en_regles
    from reviewme.reconciler import fingerprint

    marqueur = fingerprint("a.swift", "let x = y!", "tech")

    class _GH:
        def list_review_comments(self, n):
            return [
                {"id": 1, "in_reply_to_id": None, "path": "a.swift", "line": 10,
                 "body": f"**[BLOCKER]** Force-unwrap ici.\n{marqueur}", "user": {"login": "bot"}},
                {"id": 2, "in_reply_to_id": 1,
                 "body": "Toléré dans les tests, c'est notre convention.",
                 "user": {"login": "alice"}},
                {"id": 3, "in_reply_to_id": None, "path": "b.swift",
                 "body": "discussion entre humains", "user": {"login": "bob"}},
            ]

    exchanges = collecter(_GH(), 42, LOGGER)
    assert len(exchanges) == 1                      # the purely human thread is ignored
    assert exchanges[0]["auteur"] == "alice"
    assert exchanges[0]["reviewer"] == "tech"

    rules = en_regles(exchanges, 42)
    assert "alice" in rules and "#42" in rules    # who, and on which PR
    assert "a.swift" in rules
    assert "Review before keeping" in rules                     # proposed, not decided
    assert marqueur not in rules


# --------------------------------------------------------------- statistics

def test_stats_have_no_content_and_are_idempotent():
    """These counters go through a CI cache: no PR content gets in.
    And replaying a build must not inflate the totals."""
    from reviewme.stats import enregistrer, resume

    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "stats.json"
        for _ in range(2):                        # same (PR, commit) twice
            enregistrer(147, "abc123def456", "alice", "p", ["tech"],
                        {"posted": 3, "deduped": 0, "dropped_low": 1}, 0.29, 120_000, 4.3,
                        LOGGER, f)
        enregistrer(148, "ff00aa11bb22", "bob", "p", ["tech", "i18n"],
                    {"posted": 0, "deduped": 3}, 1.44, 90_000, 2.9, LOGGER, f)

        raw = f.read_text(encoding="utf-8")
        for forbidden in ("summary", "pr_title", "message", "snippet"):
            assert forbidden not in raw, f"review content in the stats: {forbidden}"

        r = resume(f)
        assert r["runs"] == 2 and r["prs"] == 2          # the replay did not double
        assert r["remarques_postees"] == 3
        assert r["remarques_dedupliquees"] == 3          # what we avoided reposting
        assert r["cout_total_usd"] == 1.73
        assert set(r["par_auteur"]) == {"alice", "bob"}


def test_engagement_counters_take_the_last_run_of_each_pr():
    """`remarques_*` are a per-PR snapshot: last run per PR, summed across PRs.
    A `max` over every run reported the busiest PR alone."""
    from reviewme.stats import enregistrer, resume

    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "stats.json"
        enregistrer(1, "a" * 12, "x", "p", [], {"remarques_ouvertes": 5,
                                                "remarques_avec_reponse": 1}, 0, 0, 0, LOGGER, f)
        enregistrer(1, "b" * 12, "x", "p", [], {"remarques_ouvertes": 3,
                                                "remarques_avec_reponse": 2}, 0, 0, 0, LOGGER, f)
        enregistrer(2, "c" * 12, "y", "p", [], {"remarques_ouvertes": 4,
                                                "remarques_avec_reponse": 1}, 0, 0, 0, LOGGER, f)
        r = resume(f)
    assert r["remarques_ouvertes"] == 7               # 3 (PR 1, last run) + 4 (PR 2)
    assert r["remarques_avec_reponse"] == 3           # 2 + 1


def test_stats_record_the_real_run_duration(monkeypatch):
    """The run lasts as long as its slowest reviewer; `counts` never carried it."""
    import reviewme.run as R

    monkeypatch.setenv("FETCH_BASE_TIMEOUT", "0")
    specs = [_spec("tech"), _spec("us")]
    project = ProjectConfig(name="p", directory=None, reviewers=tuple(specs))
    durations = {"tech": 42_000, "us": 9_000}

    def one(spec, *a, **kw):
        res = _result([])
        res.metadata.update({"duration_ms": durations[spec.id], "cost_usd": 0.1})
        return spec, res

    captured = {}
    monkeypatch.setattr(R, "config_from_base", lambda config, *a, **kw: config)
    monkeypatch.setattr(R, "resolve_project", lambda config: project)
    monkeypatch.setattr(R, "_gather_context", lambda *a: ({}, set()))
    monkeypatch.setattr(R, "build_history_context", lambda *a: "")
    monkeypatch.setattr(R, "_run_one", one)
    monkeypatch.setattr(R, "load_pr_context", lambda *a, **kw: _ctx())
    monkeypatch.setattr(R, "_tag_preexisting", lambda *a, **kw: None)
    monkeypatch.setattr(R, "post_all", lambda *a, **kw: {"posted": 0})
    monkeypatch.setattr(R, "save_review", lambda n, payload: captured.update(review=payload))
    monkeypatch.setattr(R, "mark_reviewed", lambda *a, **kw: None)
    monkeypatch.setattr(R, "enregistrer_stats",
                        lambda *a, **kw: captured.update(stats=a))
    import reviewme.ledger as L
    monkeypatch.setattr(L, "record", lambda *a, **kw: "run")

    class _GH:
        def get_pr_diff(self, n):
            return "+++ b/a.swift\n@@ -1 +1 @@\n+let x = 1\n"

        def get_pr_files(self, n):
            return [{"filename": "a.swift"}]

    pr = {"number": 7, "head": {"sha": "a" * 40}, "base": {"ref": "main"}, "title": "t",
          "user": {"login": "alice"}}
    out = R._run_review(pr, _config(dry_run=True), _GH(), LOGGER, force=True, workdir="/tmp")
    assert "error" not in out, out
    assert captured["stats"][7] == 42_000             # duree_ms argument of `enregistrer`
    assert captured["review"]["duration_ms"] == 42_000


def test_unreadable_stats_do_not_blockk():
    from reviewme.stats import enregistrer, resume

    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "stats.json"
        f.write_text("{ceci n'est pas du json", encoding="utf-8")
        enregistrer(1, "a" * 12, "x", "p", [], {}, 0.0, 0, 0.0, LOGGER, f)
        assert resume(f)["runs"] == 1               # restarted cleanly


def test_html_report_is_self_contained_and_has_no_content():
    """The page gets shared: no network request, and no PR content in it."""
    from reviewme.report import generer
    from reviewme.stats import enregistrer

    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "stats.json"
        enregistrer(147, "abc123def456", "alice", "p", ["tech"],
                    {"posted": 3, "deduped": 2, "dropped_low": 1}, 0.29, 120_000, 4.3, LOGGER, f)
        page = generer(f)

    assert page.startswith("<!doctype html>")
    for external in ("http://", "https://", "<script"):
        assert external not in page, f"the page is not self-contained: {external}"
    assert "alice" in page and "0.29" in page          # the counters are there
    assert "#7C3AED" in page                            # palette validated by the skill script


# --------------------------------------------------------------- context wiring

def test_all_context_blockks_reach_the_prompt():
    """The real risk of the fan-out: a blockk computed but never passed to the model.

    Checks the full wiring (repository conventions, precheck facts, external
    context, history) all the way to the prompt actually passed to the CLI.
    """
    from unittest.mock import patch

    import reviewme.run as R

    capture = {}

    def _fake_reviewer(pr_number, pr_title, pr_diff, config, spec=None, extra_context=""):
        capture["extra"] = extra_context
        capture["spec"] = spec
        return _result([])

    with tempfile.TemporaryDirectory() as tmp:
        repo_dir = Path(tmp)
        (repo_dir / "AGENTS.md").write_text("Règle maison : 4 espaces.", encoding="utf-8")
        rdir = repo_dir / "r"
        rdir.mkdir()
        (rdir / "p.py").write_text(
            "import sys\nd = sys.stdin.read()\n"
            "print('FAITS: ' + str(len(d.splitlines())) + ' lignes de diff')\n",
            encoding="utf-8")

        spec = _spec("tech", context_read=("AGENTS.md",), precheck="p.py",
                     directory=rdir, requires=("jira_ticket",))
        cfg = _config(repo_path=str(repo_dir))

        with patch.object(R, "run_reviewer", _fake_reviewer):
            R._run_one(spec, 7, "titre", "+import A\n+import B\n", cfg,
                       {"jira_ticket": "TICKET: couvre le cas X"}, LOGGER,
                       historique="HISTORIQUE: alice a répondu")

    extra = capture["extra"]
    assert "TICKET: couvre le cas X" in extra          # external context (Jira)
    assert "HISTORIQUE: alice a répondu" in extra      # discussion threads
    assert "Règle maison : 4 espaces." in extra        # conventions read from the repository
    assert "FAITS: 2 lignes de diff" in extra          # precheck, AND it received the diff


# --------------------------------------------------------------- review history

def test_history_gives_human_replies_to_the_agent():
    """Without it, a "this is intentional" is forgotten as soon as the line moves."""
    from reviewme.history import build_history_context
    from reviewme.reconciler import fingerprint

    marqueur = fingerprint("a.swift", "let x = y!", "tech")

    class _GH:
        def list_review_comments(self, n):
            return [
                {"id": 1, "in_reply_to_id": None, "path": "a.swift", "line": 10,
                 "body": f"**[BLOCKER]** Force-unwrap ici.\n{marqueur}",
                 "user": {"login": "bot"}},
                {"id": 2, "in_reply_to_id": 1, "path": "a.swift", "line": 10,
                 "body": "Volontaire : la valeur est garantie par le contrat amont.",
                 "user": {"login": "alice"}},
                {"id": 3, "in_reply_to_id": None, "path": "b.swift", "line": 4,
                 "body": "Question d'un humain, sans marqueur", "user": {"login": "bob"}},
            ]

    metrics: dict = {}
    block = build_history_context(_GH(), 7, LOGGER, metrics)
    assert metrics == {"remarques_ouvertes": 1, "remarques_avec_reponse": 1}
    assert "a.swift:10" in block                       # the comment already posted
    assert "alice" in block and "Volontaire" in block    # the human reply
    assert "Question d'un humain" not in block          # purely human threads are out of scope
    assert "do not come back" in block                  # the instruction not to insist
    assert "never instructions" in block           # third-party content marked untrusted
    assert marqueur not in block                        # technical marker stripped


def test_missing_history_does_not_block():
    from reviewme.history import build_history_context

    class _Empty:
        def list_review_comments(self, n):
            return []

    class _Broken:
        def list_review_comments(self, n):
            raise RuntimeError("API indisponible")

    assert build_history_context(_Empty(), 1, LOGGER) == ""
    assert build_history_context(_Broken(), 1, LOGGER) == ""


def test_mcp_config_confined_to_reviewer_directory():
    """A config can come from a PR: it must not be able to point at an arbitrary
    repository file as an MCP server configuration."""
    from reviewme.reviewer import _mcp_config_path

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "reviewers" / "tech"
        base.mkdir(parents=True)
        (base / "mcp.json").write_text("{}", encoding="utf-8")
        (Path(tmp) / "ailleurs.json").write_text("{}", encoding="utf-8")

        assert _mcp_config_path(_spec("tech", mcp_config="mcp.json", directory=base)) is not None
        assert _mcp_config_path(_spec("tech", mcp_config="../../ailleurs.json", directory=base)) is None
        assert _mcp_config_path(_spec("tech", mcp_config="absent.json", directory=base)) is None
        assert _mcp_config_path(_spec("tech")) is None


# --------------------------------------------------------------- untrusted config

def test_instance_env_cannot_choose_the_executed_binary():
    """A config can come from a PR: `.reviewme/.env` with CLAUDE_BIN = RCE."""
    import os as _os

    from reviewme.config import _load_instance_env

    with tempfile.TemporaryDirectory() as tmp:
        env = Path(tmp) / ".env"
        env.write_text("CLAUDE_BIN=/bin/sh\nGITHUB_TOKEN=vole\nCONFIDENCE_THRESHOLD=1\n",
                       encoding="utf-8")
        for k in ("CLAUDE_BIN", "GITHUB_TOKEN", "CONFIDENCE_THRESHOLD"):
            _os.environ.pop(k, None)
        try:
            _load_instance_env(env)
            assert "CLAUDE_BIN" not in _os.environ      # what gets executed
            assert "GITHUB_TOKEN" not in _os.environ    # where we authenticate
            assert _os.environ["CONFIDENCE_THRESHOLD"] == "1"   # harmless setting: read
        finally:
            for k in ("CLAUDE_BIN", "GITHUB_TOKEN", "CONFIDENCE_THRESHOLD"):
                _os.environ.pop(k, None)


def test_precheck_cannot_escape_reviewer_directory():
    from reviewme.precheck import run_precheck

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "reviewers" / "tech"
        base.mkdir(parents=True)
        trap = Path(tmp) / "evasion.sh"
        trap.write_text("#!/bin/sh\necho pwned\n", encoding="utf-8")
        trap.chmod(0o755)
        spec = _spec("tech", precheck="../../evasion.sh", directory=base)
        assert run_precheck(spec, _config(repo_path=tmp), LOGGER) == ""


# --------------------------------------------------------------- plugins

def test_no_declared_plugin_no_install():
    from reviewme.plugins import ensure_plugins
    ensure_plugins(_spec("tech"), "/bin/false", LOGGER)   # must not launch anything, hence must not fail


def test_failing_plugin_raises_explicit_error():
    from reviewme.plugins import PluginError, ensure_plugins
    spec = _spec("tech", plugins_install=("plugin-qui-nexiste-pas",))
    try:
        ensure_plugins(spec, "/bin/false", LOGGER)        # /bin/false: always fails
    except PluginError as e:
        assert "plugin-qui-nexiste-pas" in str(e)
    else:
        raise AssertionError("PluginError expected")


def test_plugin_list_parsing():
    """`claude plugin list` mixes identifiers and detail lines."""
    import reviewme.plugins as P
    output = ("Installed plugins:\n\n"
              "  ❯ context7@claude-plugins-official\n"
              "    Version: unknown\n"
              "    Scope: user\n"
              "  ❯ autre-plugin@perso\n")
    original = P._run
    P._run = lambda cli, args, logger: (0, output)
    try:
        assert P.installed_plugins("claude", LOGGER) == {"context7", "autre-plugin"}
    finally:
        P._run = original


# --------------------------------------------------------------- LLM engine

def test_per_reviewer_model_overrides_global_variable():
    """A factual reviewer does not need the same model as an architecture analysis."""
    import os as _os

    from reviewme.reviewer import pick_model

    original = _os.environ.get("CLAUDE_MODEL")
    _os.environ["CLAUDE_MODEL"] = "modele-global"
    try:
        assert pick_model(_spec("i18n", model="modele-du-reviewer")) == "modele-du-reviewer"
        assert pick_model(_spec("tech")) == "modele-global"
        _os.environ.pop("CLAUDE_MODEL")
        assert pick_model(_spec("tech")) == ""          # empty: the CLI decides
    finally:
        _os.environ.pop("CLAUDE_MODEL", None)
        if original is not None:
            _os.environ["CLAUDE_MODEL"] = original


def test_claude_bin_allows_a_wrapper():
    """Point to an in-house wrapper (gateway, quotas, logs) without touching the code."""
    from reviewme.reviewer import _find_claude
    assert _find_claude(_config(claude_bin="/bin/echo")) == "/bin/echo"
    assert _find_claude(_config(claude_bin="  ")) == _find_claude(_config())   # empty = default


def test_claude_bin_not_found_clear_message():
    from reviewme.reviewer import _find_claude
    try:
        _find_claude(_config(claude_bin="/nexiste/pas/binaire"))
    except RuntimeError as e:
        assert "CLAUDE_BIN" in str(e)
    else:
        raise AssertionError("RuntimeError expected")


# --------------------------------------------------------------- repository conventions

def test_context_read_injects_files_and_lists_directories():
    from reviewme.repo_context import build_repo_context
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        (repo / "AGENTS.md").write_text("Règle : 4 espaces.", encoding="utf-8")
        (repo / "docs").mkdir()
        (repo / "docs" / "style.md").write_text("...", encoding="utf-8")

        spec = _spec("tech", context_read=("AGENTS.md", "docs/"))
        block = build_repo_context(spec, str(repo), LOGGER)
        assert "Règle : 4 espaces." in block          # file: content injected
        assert "`docs/style.md`" in block             # directory: listing only
        assert "..." not in block.replace("Règle : 4 espaces.", "")


def test_context_read_reports_a_dead_path():
    """The benefit of being declarative: a stale path shows up instead of silently starving the reviewer."""
    import logging as _logging

    from reviewme.repo_context import build_repo_context

    captured = []

    class _Catch(_logging.Handler):
        def emit(self, record):
            captured.append(record.getMessage())

    logger = _logging.getLogger("test-dead")
    logger.addHandler(_Catch())
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "AGENTS.md").write_text("x", encoding="utf-8")
        build_repo_context(_spec("tech", context_read=("AGENTS.md", "PARTI.md")), tmp, logger)
    assert any("PARTI.md" in m and "not found" in m for m in captured)


def test_context_read_refuses_to_leave_the_repo():
    from reviewme.repo_context import build_repo_context
    with tempfile.TemporaryDirectory() as tmp:
        spec = _spec("tech", context_read=("../../etc/passwd", "/etc/hosts"))
        assert build_repo_context(spec, tmp, LOGGER) == ""


def test_without_context_read_no_blockk():
    from reviewme.repo_context import build_repo_context
    with tempfile.TemporaryDirectory() as tmp:
        assert build_repo_context(_spec("tech"), tmp, LOGGER) == ""


# --------------------------------------------------------------- external repository

def test_external_config_home_overrides_core():
    """A separate config repository (one per organization) without forking the engine."""
    import reviewme.projects as P
    original = os.environ.pop("REVIEWME_CONFIG_HOME", None)
    try:
        assert P.projects_dir() == P.PROJECTS_DIR              # default: inside the core repo
        os.environ["REVIEWME_CONFIG_HOME"] = "/tmp/mon-instance"
        assert P.projects_dir() == Path("/tmp/mon-instance/projects")
        # Config overrides the environment (useful in tests and multi-instance setups)
        assert P.projects_dir(_config(config_home="/other/repo")) == Path("/other/repo/projects")
    finally:
        os.environ.pop("REVIEWME_CONFIG_HOME", None)
        if original is not None:
            os.environ["REVIEWME_CONFIG_HOME"] = original


def test_empty_config_home_falls_back_to_core():
    import reviewme.projects as P
    original = os.environ.pop("REVIEWME_CONFIG_HOME", None)
    try:
        os.environ["REVIEWME_CONFIG_HOME"] = "   "            # variable set but empty
        assert P.projects_dir() == P.PROJECTS_DIR
    finally:
        os.environ.pop("REVIEWME_CONFIG_HOME", None)
        if original is not None:
            os.environ["REVIEWME_CONFIG_HOME"] = original


# --------------------------------------------------------------- project config

def _write_project(root: Path, name: str, reviewers: dict[str, tuple[str, str]]):
    """A project = a `reviewers/` directory; each subdirectory IS a reviewer."""
    pdir = root / name
    (pdir / "reviewers").mkdir(parents=True)
    for rid, (toml, system) in reviewers.items():
        rdir = pdir / "reviewers" / rid
        rdir.mkdir()
        (rdir / "reviewer.toml").write_text(toml, encoding="utf-8")
        if system is not None:
            (rdir / "system.md").write_text(system, encoding="utf-8")
    return pdir


def test_project_loading_and_explicit_errors():
    import reviewme.projects as P
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        original = P.PROJECTS_DIR
        P.PROJECTS_DIR = root
        try:
            # reviewers are INFERRED from directories: nothing to declare elsewhere
            _write_project(root, "ok-repo", {
                "tech": ('output_mode = "inline"\npriority = 5\n', "persona"),
                "i18n": ('output_mode = "mixed"\n', "persona i18n"),
            })
            project = load_project("ok-repo")
            assert sorted(s.id for s in project.reviewers) == ["i18n", "tech"]
            assert project.reviewer("tech").priority == 5

            # `enabled = false` puts it to sleep without deleting the directory
            _write_project(root, "dormant", {
                "tech": ("", "persona"),
                "i18n": ("enabled = false\n", "persona i18n"),
            })
            assert [s.id for s in load_project("dormant").reviewers] == ["tech"]

            _write_project(root, "bad-mode", {"tech": ('output_mode = "carrier-pigeon"\n', "persona")})
            _expect_error(load_project, "bad-mode", "output_mode")

            _write_project(root, "no-persona", {"tech": ("", None)})
            _expect_error(load_project, "no-persona", "system.md")

            _write_project(root, "vide", {})
            _expect_error(load_project, "vide", "no reviewer")

            _write_project(root, "tous-eteints", {"tech": ("enabled = false\n", "persona")})
            _expect_error(load_project, "tous-eteints", "disabled")

            (root / "sans-dossier-reviewers").mkdir()
            (root / "sans-dossier-reviewers" / "common").mkdir()
            _expect_error(load_project, "sans-dossier-reviewers", "no reviewer")

            # empty directory = uninitialized git submodule: the message must say so
            (root / "submodule-vide").mkdir()
            _expect_error(load_project, "submodule-vide", "git submodule update --init")

            _expect_error(load_project, "jamais-cree", "not found")
        finally:
            P.PROJECTS_DIR = original


def _expect_error(fn, arg, needle):
    try:
        fn(arg)
    except ProjectConfigError as e:
        assert needle in str(e), f"unclear message for '{needle}': {e}"
    else:
        raise AssertionError(f"ProjectConfigError expected for {arg}")


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok   {name}")
            except AssertionError as e:
                fails += 1
                print(f"  FAIL {name}: {e}")
    print("\n" + ("ALL TESTS PASS" if not fails else f"{fails} FAILURE(S)"))
    sys.exit(1 if fails else 0)
