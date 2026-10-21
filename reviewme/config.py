"""ReviewMe configuration (driven by .env — the format proven over 3 months).

Configuration goes through the environment (`.env`); review behaviour goes
multi-team is deferred to v2). Secrets come ONLY from the environment.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
REVIEWS_DIR = DATA_DIR / "reviews"
STATE_FILE = DATA_DIR / "state.json"
STATE_LOCK = DATA_DIR / "state.lock"
LOG_FILE = DATA_DIR / "reviews.log"
CONFIG_DIR = ROOT_DIR / "config"


@dataclass(frozen=True)
class Config:
    github_token: str
    github_repo: str
    review_label: str = "review-me"
    poll_interval: int = 300
    repo_path: str = "."
    max_budget_usd: float = 1.00
    claude_agent: str = ""  # empty = reviewer persona; otherwise the name of a Claude Code agent
    claude_bin: str = ""    # review CLI binary. Empty = `claude` from PATH. Allows pointing
                            # to an in-house wrapper (internal gateway, quotas, logs).
    # --- v2 additions (inline + safeguards) ---
    confidence_threshold: int = 80        # findings < threshold not posted (official rubric)
    bot_login: str = ""                   # bot login if dedicated identity (otherwise empty)
    # --- Multiple reviewers ---
    # GitHub App auth (dedicated identity): if set, it takes precedence over the PAT.
    github_app_id: str = ""
    github_app_private_key_path: str = ""
    github_app_installation_id: str = ""   # empty = resolved from the target repo
    config_home: str = ""                 # EXTERNAL config repo (REVIEWME_CONFIG_HOME):
                                          # its projects take precedence over the core's config/projects/
    project: str = ""                     # name of the <projects>/<name> folder. Empty = v0.2
                                          # backward-compatible mode (single `tech` reviewer).
    max_comments_per_pr: int = 10         # cap: all reviewers combined, per PR
    reviewer_id: str = "tech"             # reviewer identity in the dedup marker.
                                          # STABLE over time for a project: renaming it
                                          # would repost all its comments as duplicates.
    review_all_prs: bool = False          # False = filter by label; True = every open PR
    dry_run: bool = False                 # True = posts nothing, logs only
    fake: bool = False                    # True = no LLM call (CI tuning)
    debug: bool = False                   # True = logs the invocation and the CLI envelope
    verify: bool = True                   # verification pass (REVIEWME_VERIFY=0 turns it off
                                          # everywhere, e.g. for a baseline pipeline)
    config_from_base: bool = True         # config under REPO_PATH read from origin/<base>
    config_source: str = "base"           # base | pr (see `config_source_env`)
    baseline: bool = False                # BASELINE pipeline (REVIEWME_BASELINE): the behaviour
                                          # from before measurement, in dry-run, to compare
    inline_min_severity: str = "IMPORTANT"  # minimum severity posted INLINE; below it,
                                            # the comment goes into the summary. MINOR = all inline

    @property
    def repo_owner(self) -> str:
        return self.github_repo.split("/")[0]

    @property
    def repo_name(self) -> str:
        return self.github_repo.split("/")[1]

    @property
    def uses_github_app(self) -> bool:
        return bool(self.github_app_id and self.github_app_private_key_path)

    @property
    def api_headers(self) -> dict[str, str]:
        """Base headers. `Authorization` is rewritten on each request by the client
        (a GitHub App token expires after one hour)."""
        return {
            "Authorization": f"Bearer {self.github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }


def _severity_env(name: str, default: str) -> str:
    """Severity read strictly: a typo must not send everything inline."""
    val = (os.environ.get(name) or default).strip().upper()
    if val not in SEVERITIES:
        raise RuntimeError(f"{name}={val!r} invalid (expected: {', '.join(SEVERITIES)})")
    return val


def _env_bool(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


#: Variables NEVER read from an instance's `.env`: they decide what gets executed or
#: where authentication happens. A config can come from a third-party repo.
INSTANCE_ENV_DENYLIST = frozenset({
    "CLAUDE_BIN",                   # binary launched as the review engine -> RCE
    "CLAUDE_AGENT",
    "REPO_PATH",                    # repo explored by the agent
    "REVIEWME_CONFIG_HOME",         # redirection of the config itself
    "REVIEWME_CONFIG_FROM_BASE",    # a PR does not choose to be reviewed with ITS config
    "REVIEWME_CONFIG_SOURCE",
    "REVIEWME_TRUSTED_BASES",       # nor the branch its config is read from
    "FETCH_BASE_TIMEOUT",           # 0 = no more origin/<base>, hence no more review
    "ANTHROPIC_BASE_URL",           # redirection of model traffic
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "GITHUB_TOKEN",
    "GITHUB_APP_PRIVATE_KEY_PATH",
    "PATH",
})


def _load_instance_env(path: Path) -> None:
    """Loads an instance's `.env`, discarding sensitive variables.

    This file is NOT trusted: it can arrive through a Pull Request.
    """
    if not path.is_file():
        return
    values = dotenv_values(path)
    for key, value in values.items():
        if value is None or key in INSTANCE_ENV_DENYLIST:
            continue
        os.environ.setdefault(key, value)   # never overrides the job's environment


def _config_in_reviewed_repo(home: str) -> bool:
    """Does the config live under `REPO_PATH` (hence in the PR's checkout)?

    Comparison WITHOUT resolving links: a `.reviewme` turned into a symlink to the
    outside stays "in the repo"; it must not pass for an external config.
    """
    if config_source_env() == "pr":
        return False
    repo = os.path.abspath(os.path.expanduser(os.environ.get("REPO_PATH", ".")))
    cible = os.path.abspath(os.path.expanduser(home))
    return os.path.commonpath([repo, cible]) == repo


SEVERITIES = ("BLOCKER", "IMPORTANT", "MINOR")
CONFIG_SOURCES = ("base", "pr")


def config_source_env() -> str:
    """Where to read the config of a project that lives in the reviewed repo (`.reviewme/`).

    - `base` (default): from the base branch. A PR does not change its own review.
    - `pr`: from the PR itself. To test a new reviewer before merging it, by running a
      build manually with this variable; avoid as a permanent setting.

    JOB setting only (never read from an instance's `.env`). `REVIEWME_CONFIG_FROM_BASE=0`
    is still accepted as a synonym of `pr`.
    """
    val = os.environ.get("REVIEWME_CONFIG_SOURCE", "").strip().lower()
    if not val:
        return "base" if _env_bool("REVIEWME_CONFIG_FROM_BASE", True) else "pr"
    if val not in CONFIG_SOURCES:
        raise RuntimeError(f"REVIEWME_CONFIG_SOURCE={val!r} invalid "
                           f"(expected: {', '.join(CONFIG_SOURCES)})")
    return val


def load_config(*, require_repo: bool = True) -> Config:
    # The INSTANCE's .env takes precedence over the engine's: an instance carries its
    # target repo, its token and its settings. `load_dotenv` never overwrites an already
    # set variable, so the order defines the priority.
    #
    # SECURITY: this file can come from a repo we are currently REVIEWING (config in
    # `.reviewme/`), hence from a PR written by a third party. Variables that decide
    # WHAT TO EXECUTE are excluded: without this filter, a PR adding `.reviewme/.env` with
    # `CLAUDE_BIN=/bin/sh` gets arbitrary code execution in a job that holds the CI
    # secrets. These variables can only be set in the job's environment.
    #
    # And when the config lives IN the reviewed repo (read from the base branch, see
    # `run.config_from_base`), this `.env` is the PR's: even filtered, it could turn off
    # its own review (`DRY_RUN`, `CONFIDENCE_THRESHOLD=101`…). It is then not read at
    # all; settings go through the job's environment.
    home = os.environ.get("REVIEWME_CONFIG_HOME", "").strip()
    if home and not _config_in_reviewed_repo(home):
        _load_instance_env(Path(home).expanduser() / ".env")
    load_dotenv(ROOT_DIR / ".env")

    token = os.environ.get("GITHUB_TOKEN", "")
    app_id = os.environ.get("GITHUB_APP_ID", "")
    app_key = os.environ.get("GITHUB_APP_PRIVATE_KEY_PATH", "")
    if not token and not (app_id and app_key):
        raise RuntimeError(
            "No GitHub authentication: set GITHUB_TOKEN (PAT), or "
            "GITHUB_APP_ID + GITHUB_APP_PRIVATE_KEY_PATH (GitHub App).")

    repo = os.environ.get("GITHUB_REPO", "")
    if require_repo and not repo:
        raise RuntimeError("GITHUB_REPO not set (env or .env)")

    # Baseline pipeline: replays the previous behaviour (one pass, threshold only, all
    # inline, no core method, no auto reading, no "pre-existing" tag) on the same PRs,
    # without posting anything. The threshold comes from CONFIDENCE_THRESHOLD as usual
    # (70 at the time).
    baseline = _env_bool("REVIEWME_BASELINE", False)

    return Config(
        baseline=baseline,
        github_token=token,
        github_repo=repo,
        review_label=os.environ.get("REVIEW_LABEL", "review-me"),
        poll_interval=int(os.environ.get("POLL_INTERVAL", "300")),
        repo_path=os.environ.get("REPO_PATH", "."),
        max_budget_usd=float(os.environ.get("MAX_BUDGET_USD", "1.00")),
        claude_agent=os.environ.get("CLAUDE_AGENT", ""),
        claude_bin=os.environ.get("CLAUDE_BIN", ""),
        confidence_threshold=int(os.environ.get("CONFIDENCE_THRESHOLD", "80")),
        bot_login=os.environ.get("BOT_LOGIN", ""),
        github_app_id=app_id,
        github_app_private_key_path=app_key,
        github_app_installation_id=os.environ.get("GITHUB_APP_INSTALLATION_ID", ""),
        config_home=os.environ.get("REVIEWME_CONFIG_HOME", ""),
        project=os.environ.get("PROJECT", ""),
        max_comments_per_pr=int(os.environ.get("MAX_COMMENTS_PER_PR", "10")),
        reviewer_id=os.environ.get("REVIEWER_ID", "tech"),
        review_all_prs=_env_bool("REVIEW_ALL_PRS", False),
        dry_run=_env_bool("DRY_RUN", False) or baseline,
        fake=_env_bool("REVIEWME_FAKE", False),
        debug=_env_bool("REVIEWME_DEBUG", False),
        verify=_env_bool("REVIEWME_VERIFY", True) and not baseline,
        config_from_base=config_source_env() != "pr",
        config_source=config_source_env(),
        inline_min_severity="MINOR" if baseline
        else _severity_env("REVIEWME_INLINE_MIN_SEVERITY", "IMPORTANT"),
    )
