"""Output tests (PR 3): routing by severity, pre-existing code, Jira key.

What matters: only serious remarks introduced by the PR go inline;
the rest goes into the summary, without getting lost.

Run: `pytest tests/`.
"""
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reviewme.config import Config
from reviewme.context.jira import extract_ticket_key
from reviewme.diff_utils import LinePos
from reviewme.models import Finding, ReviewResult, Severity
from reviewme.preexisting import removed_lines, tag_preexisting
from reviewme.reconciler import PrContext, prepare

LOGGER = logging.getLogger("test-output")
LOGGER.addHandler(logging.NullHandler())
PR = {"number": 7, "head": {"sha": "abcdef1234567890"}, "title": "Test PR"}


def _config(**kw):
    base = dict(github_token="t", github_repo="o/r", confidence_threshold=50, dry_run=True)
    base.update(kw)
    return Config(**base)


def _ctx():
    return PrContext(valid={"a.swift": {n: LinePos(n, f"l{n}", added=True)
                                       for n in range(10, 14)}})


def _f(line, sev, **kw):
    return Finding(path="a.swift", line=line, severity=sev, message=f"m{line}",
                   confidence=90, **kw)


def test_minor_and_preexisting_in_summary():
    fs = [_f(10, Severity.BLOCKER), _f(11, Severity.MINOR),
          _f(12, Severity.IMPORTANT, preexisting=True)]
    prep = prepare(PR, _config(), _ctx(), ReviewResult("COMMENT", "s", fs), LOGGER, "tech")
    assert [c["line"] for c in prep.batch] == [10]
    assert [(f.fate, f.in_summary) for f in fs] == [("posted", False), ("posted", True),
                                                    ("posted", True)]
    assert "Minor findings" in prep.summary and "m11" in prep.summary
    assert "Pre-existing code" in prep.summary and "m12" in prep.summary


def test_configurable_inline_threshold():
    fs = [_f(11, Severity.MINOR)]
    prep = prepare(PR, _config(inline_min_severity="MINOR"), _ctx(),
                   ReviewResult("COMMENT", "s", fs), LOGGER, "tech")
    assert len(prep.batch) == 1


def test_coerce_no_longer_upgrades_warnings():
    assert Severity.coerce("warning") is Severity.MINOR
    assert Severity.coerce("medium") is Severity.MINOR
    assert Severity.coerce("high") is Severity.IMPORTANT
    assert Severity.coerce("critical") is Severity.BLOCKER


def test_jira_key_title_before_branch():
    pr = {"title": "feat(Search) - PROJ-4321 - sharing", "head": {"ref": "feat/PROJ-1111"},
          "body": "see also PROJ-2222"}
    assert extract_ticket_key(pr) == "PROJ-4321"
    pr["title"] = "List sharing"
    assert extract_ticket_key(pr) == "PROJ-1111"


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


def test_moved_code_tagged_preexisting():
    """A block moved into a new file by the PR is pre-existing; a truly new line
    is not."""
    block = "\n".join(f"let valueNumber{i} = complexCalc({i}, factor: {i * 3})"
                     for i in range(12)) + "\n"
    with tempfile.TemporaryDirectory() as tmp:
        _git(tmp, "init", "-q", "-b", "main")
        Path(tmp, "Old.swift").write_text(block)
        _git(tmp, "add", ".")
        _git(tmp, "commit", "-qm", "base")
        _git(tmp, "update-ref", "refs/remotes/origin/main", "HEAD")
        _git(tmp, "checkout", "-qb", "pr")
        Path(tmp, "Old.swift").unlink()
        Path(tmp, "New.swift").write_text("let brandNew = 42\n" + block)
        _git(tmp, "add", "-A")
        _git(tmp, "commit", "-qm", "move")
        diff = subprocess.run(["git", "diff", "--no-renames", "origin/main", "HEAD"], cwd=tmp,
                              capture_output=True, text=True).stdout
        lines = Path(tmp, "New.swift").read_text().splitlines()
        fresh = Finding(path="New.swift", line=1, severity=Severity.BLOCKER, message="x")
        moved = Finding(path="New.swift", line=5, severity=Severity.BLOCKER, message="y")
        n = tag_preexisting([fresh, moved], tmp, "main", LOGGER,
                            lambda f: (f.line, lines[f.line - 1]), removed_lines(diff))
    assert n == 1 and moved.preexisting and not fresh.preexisting


def test_context_line_is_preexisting():
    """Renamed file: its unchanged lines are context in the diff."""
    f = Finding(path="a", line=3, severity=Severity.IMPORTANT, message="x")
    assert tag_preexisting([f], ".", "", LOGGER, lambda f: (3, "x", False), set()) == 1
    assert f.preexisting


def test_copied_code_is_not_preexisting():
    """Pasting an existing block (without removing it elsewhere) introduces the code: no tag."""
    block = "\n".join(f"let valueNumber{i} = complexCalc({i}, factor: {i * 3})"
                     for i in range(12)) + "\n"
    with tempfile.TemporaryDirectory() as tmp:
        _git(tmp, "init", "-q", "-b", "main")
        Path(tmp, "Old.swift").write_text(block)
        _git(tmp, "add", ".")
        _git(tmp, "commit", "-qm", "base")
        _git(tmp, "update-ref", "refs/remotes/origin/main", "HEAD")
        _git(tmp, "checkout", "-qb", "pr")
        Path(tmp, "Copy.swift").write_text(block)
        _git(tmp, "add", "-A")
        _git(tmp, "commit", "-qm", "copy")
        diff = subprocess.run(["git", "diff", "origin/main", "HEAD"], cwd=tmp,
                              capture_output=True, text=True).stdout
        lines = block.splitlines()
        copy = Finding(path="Copy.swift", line=5, severity=Severity.BLOCKER, message="y")
        n = tag_preexisting([copy], tmp, "main", LOGGER,
                            lambda f: (f.line, lines[f.line - 1]), removed_lines(diff))
    assert n == 0 and not copy.preexisting


def test_moved_blocker_stays_inline():
    fs = [_f(10, Severity.BLOCKER, preexisting=True), _f(11, Severity.IMPORTANT, preexisting=True)]
    prep = prepare(PR, _config(), _ctx(), ReviewResult("COMMENT", "s", fs), LOGGER, "tech")
    assert [c["line"] for c in prep.batch] == [10]
    assert "code moved by this PR" in prep.batch[0]["body"] and "m11" in prep.summary


def test_minor_already_inline_not_duplicated():
    from reviewme.reconciler import fingerprint_hash
    ctx = _ctx()
    ctx.inline_by_marker[("tech", fingerprint_hash("a.swift", "l11"))] = {"id": 3, "position": 2}
    f = _f(11, Severity.MINOR)
    prep = prepare(PR, _config(), ctx, ReviewResult("COMMENT", "s", [f]), LOGGER, "tech")
    assert f.fate == "deduped" and "m11" not in prep.summary


def test_summary_alone_updated_not_reposted():
    from reviewme.reconciler import post_all

    class GH:
        def __init__(self, existing):
            self.existing, self.posts, self.updated = existing, [], []

        def list_issue_comments(self, n):
            return self.existing

        def post_issue_comment(self, n, body):
            self.posts.append(body)
            return {"id": 1}

        def update_issue_comment(self, cid, body):
            self.updated.append(cid)
            return {"id": cid}

    fs = [_f(11, Severity.MINOR)]
    cfg = _config(dry_run=False)
    prep = prepare(PR, cfg, _ctx(), ReviewResult("COMMENT", "s", fs), LOGGER, "tech")
    gh = GH([])
    post_all(PR, cfg, gh, [prep], LOGGER)
    assert len(gh.posts) == 1
    gh2 = GH([{"id": 42, "body": gh.posts[0]}])
    post_all(PR, cfg, gh2, [prep], LOGGER)
    assert gh2.posts == [] and gh2.updated == [42]


def test_invalid_inline_threshold_rejected(monkeypatch):
    import pytest

    from reviewme.config import load_config
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("REVIEWME_INLINE_MIN_SEVERITY", "WARNING")
    with pytest.raises(RuntimeError):
        load_config(require_repo=False)


def test_without_base_nothing_is_tagged():
    with tempfile.TemporaryDirectory() as tmp:
        f = Finding(path="a", line=1, severity=Severity.BLOCKER, message="x")
        assert tag_preexisting([f], tmp, "main", LOGGER) == 0 and not f.preexisting
