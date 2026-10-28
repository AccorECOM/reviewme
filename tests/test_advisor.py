"""Tests for the "Review tip": a point a human must check on the PR.

What matters: it only appears when it is actionable (anchored in files of the PR),
never as an inline remark, and on its own it is enough to get the summary posted.

Run: `pytest tests/`.
"""
import json
import logging
import os
import sys
from typing import ClassVar

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reviewme.config import Config
from reviewme.diff_utils import LinePos
from reviewme.models import Advice, ReviewResult, parse_review_output
from reviewme.reconciler import PrContext, post_all, prepare

LOGGER = logging.getLogger("test-advisor")
LOGGER.addHandler(logging.NullHandler())
PR = {"number": 7, "head": {"sha": "abcdef1234567890"}, "title": "Test PR"}


def _config(**kw):
    base = dict(github_token="t", github_repo="o/r", confidence_threshold=50, dry_run=False)
    base.update(kw)
    return Config(**base)


def _ctx():
    return PrContext(valid={"src/payments/refund.py": {3: LinePos(3, "x", added=True)}})


def test_advice_parsing():
    r = parse_review_output(json.dumps({"summary": "s", "findings": [], "advice": [
        {"topic": "Refund ordering", "why": "Two calls swapped.",
         "paths": "src/payments/refund.py"},
        {"topic": "", "why": "no topic"},
        {"topic": "a", "why": "b"}, {"topic": "c", "why": "d"}, {"topic": "e", "why": "f"},
    ]}))
    assert len(r.advice) == 3
    assert r.advice[0].paths == ["src/payments/refund.py"]


def test_unanchored_advice_dropped():
    result = ReviewResult("COMMENT", "s", [], advice=[
        Advice("Test well", "It matters.", ["elsewhere/x.py"]),
        Advice("Refund ordering", "The refund runs before the cancellation.",
               ["src/payments/refund.py", "missing.py"]),
    ])
    prep = prepare(PR, _config(), _ctx(), result, LOGGER, "advisor", advice=True)
    assert prep.counts["advice"] == 1
    assert "Review tip" in prep.summary
    assert "Refund ordering" in prep.summary and "Test well" not in prep.summary
    assert "missing.py" not in prep.summary and prep.batch == []


def test_advice_off_by_default_outside_dedicated_reviewer():
    result = ReviewResult("COMMENT", "s", [], advice=[
        Advice("Ordering", "Why.", ["src/payments/refund.py"])])
    prep = prepare(PR, _config(), _ctx(), result, LOGGER, "tech")
    assert prep.counts["advice"] == 0 and "Review tip" not in prep.summary


def test_advisor_reviewer_skeleton():
    from reviewme.projects import TEMPLATES_DIR, _load_reviewer
    spec = _load_reviewer("p", TEMPLATES_DIR, "advisor", "")
    assert not spec.findings and not spec.verify
    assert spec.output_mode == "global" and spec.silent_when_clean
    tech = _load_reviewer("p", TEMPLATES_DIR, "tech", "")
    assert tech.findings


def test_single_advice_posts_summary():
    class GH:
        posts: ClassVar[list] = []

        def list_issue_comments(self, n):
            return []

        def post_issue_comment(self, n, body):
            self.posts.append(body)
            return {"id": 1}

    result = ReviewResult("COMMENT", "s", [], advice=[
        Advice("Ordering", "Why.", ["src/payments/refund.py"])])
    prep = prepare(PR, _config(), _ctx(), result, LOGGER, "advisor", advice=True)
    gh = GH()
    post_all(PR, _config(), gh, [prep], LOGGER)
    assert len(gh.posts) == 1 and "Review tip" in gh.posts[0]


def test_silent_but_not_mute_on_advice():
    result = ReviewResult("COMMENT", "Everything is covered.", [], advice=[
        Advice("AC2 to test by hand", "The offline case has no test.",
               ["src/payments/refund.py"])])
    prep = prepare(PR, _config(), _ctx(), result, LOGGER, "advisor", output_mode="global",
                   silent_when_clean=True, advice=True)
    assert prep.global_body and "AC2 to test" in prep.global_body


def test_core_reviewers_enabled_by_project_toml():
    import tempfile
    from pathlib import Path

    import pytest

    from reviewme.projects import ProjectConfigError, load_project
    with tempfile.TemporaryDirectory() as tmp:
        pdir = Path(tmp) / "shop"
        pdir.mkdir()
        (pdir / "project.toml").write_text('[reviewers]\ncore = ["tech", "advisor"]\n')
        ids = sorted(s.id for s in load_project("shop", Path(tmp)).reviewers)
        assert ids == ["advisor", "tech"]

        # A local folder with the same name takes precedence over the core reviewer.
        local = pdir / "reviewers" / "tech"
        local.mkdir(parents=True)
        (local / "system.md").write_text("local persona")
        tech = next(s for s in load_project("shop", Path(tmp)).reviewers if s.id == "tech")
        assert tech.system_prompt == "local persona"

        (pdir / "project.toml").write_text('[reviewers]\ncore = ["missing"]\n')
        with pytest.raises(ProjectConfigError):
            load_project("shop", Path(tmp))


def test_core_reviewer_customized_by_project_toml():
    import tempfile
    from pathlib import Path

    import pytest

    from reviewme.projects import ProjectConfigError, load_project
    with tempfile.TemporaryDirectory() as tmp:
        pdir = Path(tmp) / "shop"
        pdir.mkdir()
        (pdir / "project.toml").write_text(
            '[reviewers]\ncore = ["tech"]\n\n[reviewers.tech]\n'
            'read = ["Documentation/ARCHITECTURE.md"]\npaths = ["src/**"]\n'
            'instructions = "Services live in src/services."\nmax_turns = 50\n')
        tech = load_project("shop", Path(tmp)).reviewers[0]
        assert tech.context_read[-1] == "Documentation/ARCHITECTURE.md"
        assert "CONTRIBUTING.md" in tech.context_read    # the core base is kept
        assert tech.when_paths == ("src/**",) and tech.max_turns == 50
        assert tech.system_prompt.endswith("Services live in src/services.")

        (pdir / "project.toml").write_text(
            '[reviewers]\ncore = ["tech"]\n\n[reviewers.tech]\nsystem = "x"\n')
        with pytest.raises(ProjectConfigError):
            load_project("shop", Path(tmp))


def test_language_of_posted_texts():
    """The bot's headings follow `[review] language`; English by default."""
    result = ReviewResult("COMMENT", "s", [], advice=[
        Advice("Ordering", "Why.", ["src/payments/refund.py"])])
    fr = prepare(PR, _config(), _ctx(), result, LOGGER, "advisor", advice=True, language="fr",
                 output_mode="global")
    assert "Conseil pour la relecture" in fr.global_body and "review automatisée" in fr.global_body
    en = prepare(PR, _config(), _ctx(), result, LOGGER, "advisor", advice=True,
                 output_mode="global")
    assert "Review tip" in en.global_body and "automated review" in en.global_body


def test_project_language():
    import tempfile
    from pathlib import Path

    import pytest

    from reviewme.projects import ProjectConfigError, load_project
    from reviewme.reviewer import _build_prompt
    with tempfile.TemporaryDirectory() as tmp:
        pdir = Path(tmp) / "shop"
        pdir.mkdir()
        (pdir / "project.toml").write_text('[review]\nlanguage = "fr"\n\n[reviewers]\ncore = ["tech"]\n')
        project = load_project("shop", Path(tmp))
        assert project.language == "fr" and project.reviewers[0].language == "fr"
        prompt = _build_prompt(1, "t", "/x.diff", _config(), project.reviewers[0])
        assert "in French" in prompt
        (pdir / "project.toml").write_text('[review]\nlanguage = "klingon"\n')
        with pytest.raises(ProjectConfigError):
            load_project("shop", Path(tmp))


def test_bot_recognized_whatever_the_language():
    from reviewme.enrich import _is_bot
    assert _is_bot({"body": "Still relevant. _ReviewMe · automated review_"})
    assert _is_bot({"body": "Toujours. _ReviewMe · review automatisée_"})


def test_global_advice_does_not_publish_clean_inline_summaries():
    """An advisor tip must not drag along the "nothing to report" of the other reviewers."""
    class GH:
        def __init__(self):
            self.posts = []

        def list_issue_comments(self, n):
            return []

        def post_issue_comment(self, n, body):
            self.posts.append(body)
            return {"id": len(self.posts)}

    clean = prepare(PR, _config(), _ctx(), ReviewResult("COMMENT", "Nothing to report.", []),
                    LOGGER, "tech", prefix="tech")
    advisor = prepare(PR, _config(), _ctx(), ReviewResult("COMMENT", "s", [], advice=[
        Advice("Ordering", "Why.", ["src/payments/refund.py"])]),
        LOGGER, "advisor", output_mode="global", silent_when_clean=True, advice=True)
    gh = GH()
    post_all(PR, _config(), gh, [clean, advisor], LOGGER)
    assert len(gh.posts) == 1 and "Review tip" in gh.posts[0]
    assert "Nothing to report." not in gh.posts[0]
