"""Watch mode: poll a repository's open PRs and review the new commits, from any machine.

`reviewme watch --repo OWNER/REPO --every 10m` runs anywhere the Claude Code CLI runs
(a developer machine, a small server). How that CLI reaches the model is the machine's
own Claude Code setup, not ReviewMe's business. It is the same `run_review` as the CLI,
the webhook and CI: this module only decides WHICH PRs to review and gives each one the
right checkout.

What it adds around `run_review`:
- an ISOLATED CHECKOUT per PR: `git worktree add --detach` at the PR head, next to the
  clone, removed afterwards. The agent reads the PR's code, never the branch the clone
  happens to be on, and the developer's working tree is never touched;
- the FILTER: every open PR by default, minus drafts, PRs from forks and PRs carrying the
  opt-out label (`no-ai-review`); `--label` restricts to PRs carrying an opt-in label;
- QUOTA handling: a usage limit does not mark the commit as failed; the loop waits for
  the reset (or backs off) and resumes;
- a per-repo LOCK, so two loops on the same repo do not review the same PR twice.

Security is unchanged: the config is still read from the base branch (`run.config_from_base`
runs inside the worktree, whose `origin/*` refs are the clone's), the tool allowlist is
the engine's, and the PR's code is read, never executed.
"""
from __future__ import annotations

import dataclasses
import logging
import os
import re
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path

from .config import DATA_DIR, Config
from .github_client import GitHubClient
from .run import run_review
from .state import failed_at, is_reviewed

DEFAULT_INTERVAL_S = 600
MIN_INTERVAL_S = 60
MAX_BACKOFF_S = 3600
DEFAULT_SKIP_LABEL = "no-ai-review"
_GIT_TIMEOUT_S = 120

try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:  # pragma: no cover (Windows)
    _HAS_FCNTL = False


class WatchError(RuntimeError):
    """A setup problem that stops the loop before it starts (wrong clone, lock held…)."""


@dataclass(frozen=True)
class WatchOptions:
    interval_s: int = DEFAULT_INTERVAL_S
    label: str = ""                       # opt-in: only PRs with this label. Empty = all
    skip_label: str = DEFAULT_SKIP_LABEL  # opt-out: PRs with this label are left alone
    include_drafts: bool = False
    include_forks: bool = False
    parallel: int = 1                     # PRs reviewed at once (model quotas)
    once: bool = False
    dashboard: bool = False


@dataclass
class RoundResult:
    reviewed: int = 0
    failed: int = 0
    usage_limit: bool = False
    retry_at: float | None = None
    skipped: dict[int, str] = field(default_factory=dict)


# ------------------------------------------------------------------ options

def parse_interval(value: str | int) -> int:
    """`90`, `90s`, `10m`, `1h` -> seconds. Below one minute is refused: every round lists
    the PRs through the GitHub API, and nothing in a review needs a faster reaction."""
    text = str(value).strip().lower()
    m = re.fullmatch(r"(\d+)\s*([smh]?)", text)
    if not m:
        raise ValueError(f"invalid interval {value!r} (e.g. 600, 90s, 10m, 1h)")
    seconds = int(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600}[m.group(2)]
    if seconds < MIN_INTERVAL_S:
        raise ValueError(f"interval {value!r} too short (minimum {MIN_INTERVAL_S}s)")
    return seconds


def default_interval() -> int:
    """`POLL_INTERVAL` when set (same variable as the legacy poll loop), else 10 min."""
    env = os.environ.get("POLL_INTERVAL", "").strip()
    return parse_interval(env) if env else DEFAULT_INTERVAL_S


# ------------------------------------------------------------------ selection

def _labels(pr: dict) -> set[str]:
    return {(lb.get("name") or "").lower() for lb in pr.get("labels") or []}


def skip_reason(pr: dict, options: WatchOptions) -> str | None:
    """Why this PR is left alone, or None if it is a candidate (before the state check)."""
    labels = _labels(pr)
    if options.skip_label and options.skip_label.lower() in labels:
        return f"label '{options.skip_label}'"
    if options.label and options.label.lower() not in labels:
        return f"no label '{options.label}'"
    if pr.get("draft") and not options.include_drafts:
        return "draft"
    head_repo = ((pr.get("head") or {}).get("repo") or {}).get("full_name")
    base_repo = ((pr.get("base") or {}).get("repo") or {}).get("full_name")
    # A fork PR is written by someone outside the repo: on a personal machine, the agent
    # would read hostile content with the developer's files around it. Opt-in only.
    # A deleted fork leaves `head.repo` empty: still a fork.
    if not options.include_forks and base_repo and head_repo != base_repo:
        return "from a fork"
    return None


def select_prs(prs: list[dict], options: WatchOptions, repo: str,
               seen: set[tuple[int, str]] | None = None) -> tuple[list[dict], dict[int, str]]:
    """PRs to review this round, and the reason each other PR was left alone.

    `seen` is the in-memory record of this process: in dry-run nothing is written to the
    state, and without it the same commit would be reviewed again every round.
    """
    selected: list[dict] = []
    skipped: dict[int, str] = {}
    for pr in prs:
        number, sha = pr["number"], pr["head"]["sha"]
        reason = skip_reason(pr, options)
        if reason is None:
            if seen is not None and (number, sha) in seen:
                reason = "already reviewed in this session"
            elif is_reviewed(number, sha, repo):
                reason = "already reviewed"
            elif failed_at(number, sha, repo):
                reason = "failed on this commit (a new push will be retried)"
        if reason:
            skipped[number] = reason
        else:
            selected.append(pr)
    return selected, skipped


# ------------------------------------------------------------------ git

def _git(args: list[str], cwd: str | Path, timeout: int = _GIT_TIMEOUT_S) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                         timeout=timeout, check=False)
    if out.returncode != 0:
        raise WatchError(f"git {' '.join(args[:2])} failed: {out.stderr.strip()[:300]}")
    return out.stdout.strip()


def _repo_slug(url: str) -> str:
    """`owner/repo` from an https or ssh remote, including ssh host aliases
    (`git@github.com-work:owner/repo.git`)."""
    path = re.sub(r"\.git$", "", url.strip().rstrip("/"))
    path = re.split(r"[:/]", path)
    return "/".join(path[-2:]).lower() if len(path) >= 2 else ""


def check_clone(repo_path: str, github_repo: str) -> str:
    """`repo_path` must be a git clone of `github_repo`: otherwise every PR would be
    fetched from the wrong remote, or the agent would read another project. Returns the
    clone's absolute top-level path."""
    path = Path(repo_path).expanduser()
    if not path.is_dir():
        raise WatchError(f"REPO_PATH {path} does not exist: clone {github_repo} first")
    try:
        top = _git(["rev-parse", "--show-toplevel"], path)
        # The configured URL, not `remote get-url`: that one applies `insteadOf` rewrites.
        url = _git(["config", "--get", "remote.origin.url"], path)
    except (WatchError, OSError, subprocess.TimeoutExpired) as e:
        raise WatchError(f"REPO_PATH {path} is not a git clone with an `origin` remote "
                         f"({e})") from e
    if _repo_slug(url) != github_repo.lower():
        raise WatchError(f"REPO_PATH {top} is a clone of {_repo_slug(url) or url!r}, "
                         f"not {github_repo}")
    return top


@contextmanager
def pr_worktree(clone: str, pr_number: int, head_sha: str,
                logger: logging.Logger) -> Iterator[Path | None]:
    """Detached worktree at the PR head, removed on exit (even on error).

    Yields None when the PR moved since it was listed (a new push): the next round sees
    the new commit; reviewing the old one now would comment on code that is gone.
    """
    ref = f"refs/reviewme/pr/{pr_number}"
    _git(["fetch", "--quiet", "--no-tags", "origin", f"+refs/pull/{pr_number}/head:{ref}"],
         clone)
    fetched = _git(["rev-parse", ref], clone)
    if fetched != head_sha:
        logger.info("PR #%s moved to %s while listing, left for the next round",
                    pr_number, fetched[:8])
        yield None
        return
    parent = Path(tempfile.mkdtemp(prefix=f"reviewme-pr-{pr_number}-"))
    tree = parent / "tree"
    _git(["worktree", "add", "--detach", "--quiet", str(tree), head_sha], clone)
    try:
        yield tree
    finally:
        try:
            _git(["worktree", "remove", "--force", str(tree)], clone)
        except (WatchError, OSError, subprocess.TimeoutExpired) as e:
            logger.warning("PR #%s: worktree %s not removed (%s)", pr_number, tree, e)
        subprocess.run(["git", "worktree", "prune"], cwd=clone, capture_output=True,
                       check=False)
        with suppress(OSError):
            parent.rmdir()


def config_for_tree(config: Config, clone: str, tree: Path) -> Config:
    """The config, pointed at the worktree instead of the clone.

    A config living in the reviewed repo (`REVIEWME_CONFIG_HOME=<clone>/.reviewme`) is
    moved under the worktree too: `config_from_base` only reads a config from the base
    branch when it sits under `repo_path`. Left pointing at the clone, it would look
    external and be read from whatever branch the clone has checked out.
    """
    changes: dict = {"repo_path": str(tree)}
    home = (config.config_home or os.environ.get("REVIEWME_CONFIG_HOME", "")).strip()
    if home:
        target = os.path.abspath(os.path.expanduser(home))
        if os.path.commonpath([clone, target]) == clone:
            changes["config_home"] = str(tree / os.path.relpath(target, clone))
    return dataclasses.replace(config, **changes)


# ------------------------------------------------------------------ loop

@contextmanager
def repo_lock(github_repo: str) -> Iterator[None]:
    """One watch loop per repo on this install. Non-blocking: the second one stops."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"watch-{github_repo.replace('/', '-').lower()}.lock"
    fh = open(path, "w")
    try:
        if _HAS_FCNTL:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as e:
                raise WatchError(f"another `reviewme watch` is already running on "
                                 f"{github_repo} (lock {path})") from e
        yield
    finally:
        fh.close()


def _review_one(pr: dict, config: Config, clone: str, gh: GitHubClient,
                logger: logging.Logger) -> dict:
    number, sha = pr["number"], pr["head"]["sha"]
    try:
        with pr_worktree(clone, number, sha, logger) as tree:
            if tree is None:
                return {"skipped": True, "reason": "moved"}
            return run_review(pr, config_for_tree(config, clone, tree), gh, logger)
    except (WatchError, OSError, subprocess.TimeoutExpired) as e:
        # Checkout problem (network, PR ref gone): the commit is not to blame, so it is
        # not marked as failed; the next round tries again.
        logger.error("PR #%s: checkout impossible (%s)", number, e)
        return {"error": str(e), "checkout": True}


def watch_once(gh: GitHubClient, config: Config, clone: str, options: WatchOptions,
               logger: logging.Logger, seen: set[tuple[int, str]] | None = None,
               stop: threading.Event | None = None) -> RoundResult:
    """One round: list, filter, review. Never raises on a GitHub or review error."""
    result = RoundResult()
    try:
        prs = gh.list_open_prs()
    except Exception as e:
        logger.error("Failed to list the PRs of %s: %s", config.github_repo, e)
        return result
    todo, result.skipped = select_prs(prs, options, config.github_repo, seen)
    # Filtered PRs are named (a label typo would otherwise look like a silent bot);
    # those already reviewed are not, they are most PRs on most rounds.
    left_out = [f"#{n} {r}" for n, r in sorted(result.skipped.items())
                if not r.startswith("already reviewed")]
    if left_out:
        logger.info("Left alone: %s", ", ".join(left_out))
    if not todo:
        logger.info("%d open PR(s), nothing new to review", len(prs))
        return result
    logger.info("%d open PR(s), %d to review: %s", len(prs), len(todo),
                ", ".join(f"#{p['number']}" for p in todo))

    def handle(pr: dict, outcome: dict) -> None:
        if outcome.get("usage_limit"):
            result.usage_limit = True
            result.retry_at = outcome.get("retry_at") or result.retry_at
        elif outcome.get("error"):
            result.failed += 1
        elif outcome.get("reason") != "moved":   # moved: the next round has the new commit
            result.reviewed += 1
            if seen is not None:
                seen.add((pr["number"], pr["head"]["sha"]))

    if options.parallel <= 1:
        for pr in todo:
            if (stop and stop.is_set()) or result.usage_limit:
                break                     # quota hit: the rest waits for the reset
            handle(pr, _review_one(pr, config, clone, gh, logger))
    else:
        with ThreadPoolExecutor(max_workers=min(options.parallel, len(todo))) as pool:
            futures = [(pr, pool.submit(_review_one, pr, config, clone, gh, logger))
                       for pr in todo]
            for pr, fut in futures:
                handle(pr, fut.result())
    return result


def next_delay(result: RoundResult, interval_s: int, backoff_s: int,
               now: float | None = None) -> tuple[int, int]:
    """(seconds to wait, next back-off). After a quota error: until the reset the CLI
    announced (+1 min), else double the wait, capped at one hour. Otherwise the interval."""
    if not result.usage_limit:
        return interval_s, interval_s
    now = time.time() if now is None else now
    if result.retry_at and result.retry_at > now:
        return int(result.retry_at - now) + 60, interval_s
    backoff = min(max(backoff_s, interval_s) * 2, MAX_BACKOFF_S)
    return backoff, backoff


def _start_dashboard(logger: logging.Logger) -> None:
    from http.server import HTTPServer

    from .web.server import DashboardHandler
    try:
        server = HTTPServer(("127.0.0.1", 8420), DashboardHandler)
        server.daemon_threads = True
        logger.info("Dashboard: http://127.0.0.1:8420")
        server.serve_forever()
    except OSError:
        logger.warning("Dashboard port 8420 already in use, skipped")


def watch(config: Config, options: WatchOptions, logger: logging.Logger) -> int:
    """The loop. Returns the process exit code."""
    try:
        clone = check_clone(config.repo_path, config.github_repo)
    except WatchError as e:
        logger.error("%s", e)
        return 2

    scope = f"label '{options.label}'" if options.label else "every open PR"
    logger.info("ReviewMe watch: %s, %s, every %ss, clone %s%s", config.github_repo, scope,
                options.interval_s, clone, " [DRY-RUN]" if config.dry_run else "")

    stop = threading.Event()

    def _on_signal(sig, frame) -> None:
        logger.info("Stopping after the current review…")
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _on_signal)

    if options.dashboard:
        threading.Thread(target=_start_dashboard, args=(logger,), daemon=True).start()

    seen: set[tuple[int, str]] = set()
    gh = GitHubClient(config)
    try:
        with repo_lock(config.github_repo):
            backoff = options.interval_s
            while not stop.is_set():
                result = watch_once(gh, config, clone, options, logger, seen, stop)
                if options.once:
                    return 1 if result.failed or result.usage_limit else 0
                delay, backoff = next_delay(result, options.interval_s, backoff)
                if result.usage_limit:
                    logger.warning("Usage limit reached: next round in %d min", delay // 60)
                stop.wait(delay)
    except WatchError as e:
        logger.error("%s", e)
        return 2
    finally:
        gh.close()
        logger.info("ReviewMe watch stopped")
    return 0
