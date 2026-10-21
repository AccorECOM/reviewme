"""Tests for watch mode (`reviewme watch`).

Covers what is expensive or dangerous when it fails:
  - the CHECKOUT: the agent must read the PR's code, in a worktree, never the clone's
    branch; and a config in the reviewed repo must still be read from the base branch;
  - the FILTER: drafts, forks, opt-out/opt-in labels, commits already reviewed;
  - QUOTAS: a usage limit must neither mark the commit as failed (never retried) nor let
    the loop hammer the API;
  - the STATE: per-repo keys, legacy keys still honoured.
"""
import json
import logging
import os
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reviewme import run, state, watch
from reviewme.config import Config
from reviewme.projects import ReviewerSpec
from reviewme.reviewer import UsageLimitError, invoke_cli, usage_limit_in
from reviewme.watch import (
    RoundResult,
    WatchError,
    WatchOptions,
    check_clone,
    config_for_tree,
    next_delay,
    parse_interval,
    pr_worktree,
    select_prs,
    skip_reason,
)

LOGGER = logging.getLogger("test")
LOGGER.addHandler(logging.NullHandler())
REPO = "acme/shop"


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(state, "STATE_LOCK", tmp_path / "state.lock")
    monkeypatch.setattr(watch, "DATA_DIR", tmp_path)


def _pr(number=7, sha="a" * 40, *, labels=(), draft=False, fork=False):
    return {"number": number, "title": f"PR {number}", "draft": draft,
            "labels": [{"name": n} for n in labels],
            "head": {"sha": sha, "repo": {"full_name": "someone/shop" if fork else REPO}},
            "base": {"ref": "main", "repo": {"full_name": REPO, "default_branch": "main"}}}


def _config(**kw):
    base = dict(github_token="t", github_repo=REPO)
    base.update(kw)
    return Config(**base)


# --------------------------------------------------------------- options

def test_parse_interval_units_and_minimum():
    assert parse_interval("600") == 600
    assert parse_interval("90s") == 90
    assert parse_interval("10m") == 600
    assert parse_interval("1h") == 3600
    for bad in ("30s", "0", "10 minutes", "-5m", ""):
        with pytest.raises(ValueError):
            parse_interval(bad)


def test_default_interval_reads_poll_interval(monkeypatch):
    monkeypatch.delenv("POLL_INTERVAL", raising=False)
    assert watch.default_interval() == 600
    monkeypatch.setenv("POLL_INTERVAL", "120")
    assert watch.default_interval() == 120


# --------------------------------------------------------------- filter

def test_every_open_pr_by_default():
    assert skip_reason(_pr(), WatchOptions()) is None


def test_opt_out_label_is_case_insensitive():
    assert "no-ai-review" in skip_reason(_pr(labels=["No-AI-Review"]), WatchOptions())
    assert skip_reason(_pr(labels=["no-ai-review"]), WatchOptions(skip_label="")) is None


def test_opt_in_label_restricts():
    options = WatchOptions(label="review-me")
    assert "review-me" in skip_reason(_pr(), options)
    assert skip_reason(_pr(labels=["review-me"]), options) is None


def test_opt_out_wins_over_opt_in():
    options = WatchOptions(label="review-me")
    assert skip_reason(_pr(labels=["review-me", "no-ai-review"]), options)


def test_drafts_and_forks_are_opt_in():
    assert skip_reason(_pr(draft=True), WatchOptions()) == "draft"
    assert skip_reason(_pr(draft=True), WatchOptions(include_drafts=True)) is None
    assert skip_reason(_pr(fork=True), WatchOptions()) == "from a fork"
    assert skip_reason(_pr(fork=True), WatchOptions(include_forks=True)) is None


def test_deleted_fork_counts_as_fork():
    pr = _pr()
    pr["head"]["repo"] = None
    assert skip_reason(pr, WatchOptions()) == "from a fork"


def test_select_skips_reviewed_and_failed_commits_only():
    state.mark_reviewed(1, "s1", "t", repo=REPO)
    state.mark_error(2, "s2", "t", "boom", repo=REPO)
    prs = [_pr(1, "s1"), _pr(2, "s2"), _pr(3, "s3"), _pr(1, "s1-new")]
    todo, skipped = select_prs(prs, WatchOptions(), REPO)
    assert [(p["number"], p["head"]["sha"]) for p in todo] == [(3, "s3"), (1, "s1-new")]
    assert skipped[2].startswith("failed")


def test_select_uses_session_memory_in_dry_run():
    seen = {(3, "s3")}
    todo, skipped = select_prs([_pr(3, "s3")], WatchOptions(), REPO, seen)
    assert not todo and "session" in skipped[3]


# --------------------------------------------------------------- state

def test_state_is_per_repo():
    state.mark_reviewed(12, "sha", "t", repo="acme/a")
    assert state.is_reviewed(12, "sha", "acme/a")
    assert not state.is_reviewed(12, "other", "acme/b")
    state.mark_reviewed(12, "sha-b", "t", repo="acme/b")
    assert state.is_reviewed(12, "sha", "acme/a")       # not overwritten by repo b


def test_legacy_key_still_read_then_replaced():
    state.STATE_FILE.write_text(json.dumps(
        {"reviewed": {"5": {"head_sha": "old"}}, "errors": {}}), encoding="utf-8")
    assert state.is_reviewed(5, "old", REPO)
    state.mark_reviewed(5, "new", "t", repo=REPO)
    data = json.loads(state.STATE_FILE.read_text(encoding="utf-8"))
    assert "5" not in data["reviewed"] and data["reviewed"][f"{REPO}#5"]["head_sha"] == "new"


def test_success_clears_the_error_of_the_pr():
    state.mark_error(5, "s", "t", "boom", repo=REPO)
    state.mark_reviewed(5, "s2", "t", repo=REPO)
    assert not state.failed_at(5, "s", REPO)


# --------------------------------------------------------------- quotas

@pytest.mark.parametrize("text", [
    "Claude AI usage limit reached|1761091200",
    "5-hour limit reached ∙ resets 3pm",
    "You've hit your limit · resets 11pm (Europe/Paris)",
    'API Error: 429 {"type":"error","error":{"type":"rate_limit_error"}}',
    "API Error: 529 overloaded_error",
])
def test_usage_limit_messages_are_recognised(text):
    assert isinstance(usage_limit_in(text), UsageLimitError)


def test_usage_limit_reset_time_is_parsed():
    assert usage_limit_in("Claude AI usage limit reached|1761091200").retry_at == 1761091200


def test_other_errors_are_not_usage_limits():
    for text in ("Invalid API key", "Error: ENOENT", "timeout", ""):
        assert usage_limit_in(text) is None


def _fake_cli(tmp_path, stdout: str, code: int = 1) -> str:
    script = tmp_path / "claude"
    script.write_text(f"#!/bin/sh\ncat <<'EOF'\n{stdout}\nEOF\nexit {code}\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script)


def test_cli_usage_limit_raises_instead_of_returning_text(tmp_path):
    envelope = json.dumps({"is_error": True, "subtype": "success",
                           "result": "Claude AI usage limit reached|1761091200"})
    config = _config(claude_bin=_fake_cli(tmp_path, envelope), repo_path=str(tmp_path))
    spec = ReviewerSpec(id="tech", project="p", system_prompt="x")
    with pytest.raises(UsageLimitError) as err:
        invoke_cli("prompt", config, spec, max_turns=1, model="", budget=0)
    assert err.value.retry_at == 1761091200


def test_usage_limit_does_not_mark_the_commit_failed(monkeypatch):
    monkeypatch.setenv("FETCH_BASE_TIMEOUT", "0")

    class Gh:
        def get_pr_diff(self, number):
            raise UsageLimitError("usage limit: hit your limit", retry_at=None)

    out = run._run_review(_pr(9, "s9"), _config(), Gh(), LOGGER, force=False, workdir="/tmp")
    assert out["usage_limit"] and not state.failed_at(9, "s9", REPO)


def test_next_delay():
    assert next_delay(RoundResult(), 600, 600) == (600, 600)
    hit = RoundResult(usage_limit=True)
    assert next_delay(hit, 600, 600) == (1200, 1200)
    assert next_delay(hit, 600, 3000) == (3600, 3600)            # capped at one hour
    reset = RoundResult(usage_limit=True, retry_at=10_000)
    assert next_delay(reset, 600, 600, now=9_000) == (1060, 600)


def test_round_stops_at_the_first_usage_limit(monkeypatch):
    calls = []

    def fake_review(pr, config, clone, gh, logger):
        calls.append(pr["number"])
        return {"error": "usage limit", "usage_limit": True, "retry_at": None}

    monkeypatch.setattr(watch, "_review_one", fake_review)

    class Gh:
        def list_open_prs(self):
            return [_pr(1, "a"), _pr(2, "b")]

    result = watch.watch_once(Gh(), _config(), "/clone", WatchOptions(), LOGGER, set(),
                              threading.Event())
    assert calls == [1] and result.usage_limit and result.reviewed == 0


def test_round_remembers_reviewed_commits(monkeypatch):
    monkeypatch.setattr(watch, "_review_one", lambda *a: {"posted": 1})

    class Gh:
        def list_open_prs(self):
            return [_pr(1, "a"), _pr(2, "b", draft=True)]

    seen: set = set()
    result = watch.watch_once(Gh(), _config(), "/clone", WatchOptions(), LOGGER, seen)
    assert result.reviewed == 1 and seen == {(1, "a")} and result.skipped == {2: "draft"}


# --------------------------------------------------------------- checkout

def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


@pytest.fixture
def clone(tmp_path):
    """A clone of `acme/shop` whose origin has `main` and a PR ref `refs/pull/7/head`."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git("init", "-q", "-b", "main", cwd=origin)
    _git("config", "user.email", "t@t", cwd=origin)
    _git("config", "user.name", "t", cwd=origin)
    (origin / ".reviewme").mkdir()
    (origin / ".reviewme" / "rules.md").write_text("base rules\n")
    (origin / "app.py").write_text("print('main')\n")
    _git("add", ".", cwd=origin)
    _git("commit", "-qm", "main", cwd=origin)
    _git("checkout", "-qb", "feature", cwd=origin)
    (origin / "app.py").write_text("print('pr')\n")
    (origin / ".reviewme" / "rules.md").write_text("rules rewritten by the PR\n")
    _git("commit", "-qam", "pr", cwd=origin)
    head = _git("rev-parse", "HEAD", cwd=origin)
    _git("update-ref", "refs/pull/7/head", head, cwd=origin)
    _git("checkout", "-q", "main", cwd=origin)

    work = tmp_path / "shop"
    _git("clone", "-q", str(origin), str(work), cwd=tmp_path)
    _git("remote", "set-url", "origin", "git@github.com-work:Acme/shop.git", cwd=work)
    _git("config", "url." + str(origin) + ".insteadOf", "git@github.com-work:Acme/shop.git",
         cwd=work)
    return work, head


def test_check_clone_accepts_ssh_alias_and_refuses_other_repo(clone):
    work, _ = clone
    assert check_clone(str(work), "acme/shop") == str(work.resolve())
    with pytest.raises(WatchError, match="not acme/other"):
        check_clone(str(work), "acme/other")


def test_check_clone_refuses_a_plain_folder(tmp_path):
    with pytest.raises(WatchError):
        check_clone(str(tmp_path), REPO)


def test_worktree_has_the_pr_code_and_leaves_the_clone_alone(clone):
    work, head = clone
    with pr_worktree(str(work), 7, head, LOGGER) as tree:
        assert (tree / "app.py").read_text() == "print('pr')\n"
        assert _git("rev-parse", "HEAD", cwd=tree) == head
    assert not tree.exists()
    assert (work / "app.py").read_text() == "print('main')\n"
    assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=work) == "main"
    assert _git("worktree", "list", cwd=work).count("\n") == 0     # only the clone


def test_worktree_removed_even_when_the_review_fails(clone):
    work, head = clone
    with pytest.raises(RuntimeError), pr_worktree(str(work), 7, head, LOGGER) as tree:
        raise RuntimeError("review crashed")
    assert not tree.exists()


def test_moved_pr_is_left_for_the_next_round(clone):
    work, _ = clone
    with pr_worktree(str(work), 7, "b" * 40, LOGGER) as tree:
        assert tree is None


def test_config_in_the_repo_is_still_read_from_the_base(clone, tmp_path):
    """The security invariant survives the worktree: the PR's `.reviewme/` is ignored."""
    work, head = clone
    config = _config(repo_path=str(work), config_home=str(work / ".reviewme"))
    with pr_worktree(str(work), 7, head, LOGGER) as tree:
        moved = config_for_tree(config, str(work), tree)
        assert moved.repo_path == str(tree)
        assert moved.config_home == str(tree / ".reviewme")
        _git("fetch", "-q", "origin", "+refs/heads/main:refs/remotes/origin/main", cwd=tree)
        workdir = tmp_path / "cfg"
        workdir.mkdir()
        from_base = run.config_from_base(moved, "main", str(workdir), LOGGER,
                                         default_branch="main")
        rules = Path(from_base.config_home) / "rules.md"
        assert rules.read_text() == "base rules\n"


def test_external_config_is_left_untouched(clone, tmp_path):
    work, head = clone
    config = _config(repo_path=str(work), config_home=str(tmp_path / "config-repo"))
    with pr_worktree(str(work), 7, head, LOGGER) as tree:
        assert config_for_tree(config, str(work), tree).config_home == config.config_home


def test_second_watch_on_the_same_repo_is_refused():
    with watch.repo_lock(REPO), pytest.raises(WatchError, match="already running"), \
            watch.repo_lock(REPO):
        pass
    with watch.repo_lock("acme/other"), watch.repo_lock(REPO):
        pass
