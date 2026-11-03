"""`[review] clean_note`: one short comment when every reviewer ran and none had a remark.

Without it a clean PR gets nothing, and "nothing" cannot be told apart from "the bot did
not run". With it the PR says the review is complete, once, updated commit after commit.
"""
import logging
import tempfile
from pathlib import Path

import pytest

from reviewme.config import Config
from reviewme.diff_utils import LinePos
from reviewme.models import Finding, ReviewResult, Severity
from reviewme.projects import ProjectConfigError, load_project
from reviewme.reconciler import PrContext, marker_key, post_all, prepare, summary_fingerprint

LOGGER = logging.getLogger("test")
LOGGER.addHandler(logging.NullHandler())
PR = {"number": 7, "head": {"sha": "abcdef1234567890"}, "title": "t"}


def _config(**kw):
    base = dict(github_token="t", github_repo="o/r", confidence_threshold=80, dry_run=False)
    base.update(kw)
    return Config(**base)


def _ctx():
    return PrContext(valid={"a.swift": {10: LinePos(10, "let x = y!", added=True)}})


def _clean(rid, lang="en"):
    result = ReviewResult(status="COMMENT", summary="", findings=[], parsed_ok=True)
    return prepare(PR, _config(), _ctx(), result, LOGGER, rid, language=lang)


def _with_finding(rid):
    f = Finding(path="a.swift", line=10, severity=Severity.BLOCKER, message="Bug",
                snippet="let x = y!", confidence=95)
    result = ReviewResult(status="COMMENT", summary="", findings=[f], parsed_ok=True)
    return prepare(PR, _config(), _ctx(), result, LOGGER, rid)


class _GH:
    def __init__(self, existing=None):
        self.posted, self.updated, self.reviews = [], [], []
        self.existing = existing or []

    def create_review(self, pr, sha, body, comments, event):
        self.reviews.append(body)
        return {}

    def list_review_comments(self, pr, review_id=None):
        return []

    def list_issue_comments(self, pr):
        return self.existing

    def post_issue_comment(self, pr, body):
        self.posted.append(body)
        return {"id": 1}

    def update_issue_comment(self, cid, body):
        self.updated.append((cid, body))
        return {"id": cid}


def test_clean_pr_gets_one_note_naming_the_reviewers():
    gh = _GH()
    totals = post_all(PR, _config(), gh, [_clean("architect"), _clean("tech")], LOGGER,
                      clean_note=["architect", "tech"])
    assert len(gh.posted) == 1 and not gh.reviews
    body = gh.posted[0]
    assert "no remarks" in body and "`architect`, `tech`" in body and "`abcdef12`" in body
    assert marker_key(body) == marker_key(summary_fingerprint("resume"))
    # Not a remark: the counters that feed the stats stay at zero.
    assert totals["posted"] == 0 and totals["posted_global"] == 0
    assert totals["clean_note"] is True


def test_off_by_default_clean_pr_stays_silent():
    gh = _GH()
    totals = post_all(PR, _config(), gh, [_clean("tech")], LOGGER)
    assert not gh.posted and not gh.reviews and totals["clean_note"] is False


def test_no_note_when_a_reviewer_has_something_to_say():
    gh = _GH()
    totals = post_all(PR, _config(), gh, [_clean("architect"), _with_finding("tech")], LOGGER,
                      clean_note=["architect", "tech"])
    assert len(gh.reviews) == 1 and not gh.posted and totals["clean_note"] is False


def test_note_replaces_an_earlier_summary_instead_of_piling_up():
    old = {"id": 42, "body": f"old summary\n\n{summary_fingerprint('resume')}"}
    gh = _GH(existing=[old])
    post_all(PR, _config(), gh, [_clean("tech")], LOGGER, clean_note=["tech"])
    assert not gh.posted and gh.updated[0][0] == 42 and "no remarks" in gh.updated[0][1]


def test_note_follows_the_project_language():
    gh = _GH()
    post_all(PR, _config(), gh, [_clean("tech", lang="fr")], LOGGER, clean_note=["tech"])
    assert "aucune remarque" in gh.posted[0] and "review automatisée" in gh.posted[0]


def test_dry_run_posts_nothing():
    gh = _GH()
    totals = post_all(PR, _config(dry_run=True), gh, [_clean("tech")], LOGGER,
                      clean_note=["tech"])
    assert not gh.posted and totals["clean_note"] is True


def _project(toml: str):
    tmp = tempfile.mkdtemp()
    pdir = Path(tmp) / "shop"
    pdir.mkdir()
    (pdir / "project.toml").write_text(f'[reviewers]\ncore = ["tech"]\n{toml}')
    return load_project("shop", Path(tmp))


def test_project_toml_switch(monkeypatch):
    monkeypatch.delenv("REVIEWME_CLEAN_NOTE", raising=False)
    assert _project("").clean_note is False
    assert _project("[review]\nclean_note = true\n").clean_note is True
    with pytest.raises(ProjectConfigError, match="clean_note"):
        _project('[review]\nclean_note = "yes"\n')
    monkeypatch.setenv("REVIEWME_CLEAN_NOTE", "1")
    assert _project("").clean_note is True
