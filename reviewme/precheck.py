"""Deterministic prechecks: establish FACTS before calling the LLM.

Principle: what a script can prove should not be paid to a model. "The key
`checkout.title` exists in `fr` and is missing in `de`" is a verifiable fact, free and
with no false positive; the LLM only steps in where judgment is needed (wording, tone,
unsubstituted variable, consistency with existing code).

A reviewer declares its script in `reviewer.toml`:

    precheck = "precheck.py"

The script lives in the reviewer's folder. It receives the repo via `--repo`, **the PR
diff on stdin**, and writes to stdout a markdown block injected as-is into the prompt.

⚠️ SECURITY: a precheck is CODE supplied by the project, executed by the core. On a
config contributed by teams, it must go through the same review as a code change, and the deployment sandbox remains the underlying mitigation.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys

from .config import Config
from .projects import ReviewerSpec

_TIMEOUT_S = 120
_MAX_OUTPUT = 20_000  # beyond this it is no longer an "established fact" but a dump: truncate


#: Environment handed to a precheck: enough to run an interpreter, none of the job's
#: credentials (GITHUB_TOKEN, ANTHROPIC_*, JIRA_*). A precheck never has more rights than
#: the model it feeds.
_ENV_KEEP = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TMP", "TEMP",
             "SYSTEMROOT", "PYTHONIOENCODING")


def _minimal_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k in _ENV_KEEP}


def run_precheck(spec: ReviewerSpec, config: Config, logger: logging.Logger,
                 diff: str = "") -> str:
    """Run the reviewer's precheck. Returns its facts block, or "" if unavailable.

    Never raises: a broken precheck degrades the review (the LLM judges alone), it does
    not interrupt it.
    """
    if not spec.precheck or spec.directory is None:
        return ""

    # The path is CONFINED to the reviewer's folder: a config can come from a third-party repo
    # (config in `.reviewme/`), and `precheck = "../../../something.sh"` would execute an
    # arbitrary file from the repo in a job that holds the CI secrets.
    base = spec.directory.resolve()
    try:
        script = (base / spec.precheck).resolve()
        script.relative_to(base)
    except (ValueError, OSError):
        logger.warning("[%s] precheck refused: '%s' escapes the reviewer folder",
                       spec.id, spec.precheck)
        return ""
    if not script.is_file():
        logger.warning("[%s] precheck '%s' not found (%s)", spec.id, spec.precheck, script)
        return ""

    cmd = ([sys.executable, str(script)] if script.suffix == ".py" else [str(script)])
    cmd += ["--repo", config.repo_path]

    # The diff is passed on stdin: a precheck can thus judge what the PR CHANGES, not just
    # the state of the repo. A script that does not read it is not affected.
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, input=diff or "",
                              timeout=_TIMEOUT_S, cwd=config.repo_path, check=False,
                              env=_minimal_env())
    except subprocess.TimeoutExpired:
        logger.warning("[%s] precheck: timeout after %ds", spec.id, _TIMEOUT_S)
        return ""
    except OSError as e:
        logger.warning("[%s] precheck: cannot launch %s (%s)", spec.id, script.name, e)
        return ""

    if proc.returncode != 0:
        logger.warning("[%s] precheck: exit code %s — %s", spec.id, proc.returncode,
                       (proc.stderr or "").strip()[:300])
        return ""

    out = (proc.stdout or "").strip()
    if not out:
        return ""
    if len(out) > _MAX_OUTPUT:
        logger.info("[%s] precheck: output truncated (%d chars)", spec.id, len(out))
        out = out[:_MAX_OUTPUT] + "\n\n_(output truncated)_"

    logger.info("[%s] precheck: %d chars of established facts injected", spec.id, len(out))
    return f"# Facts established by deterministic analysis (reliable — produced by our tooling)\n\n{out}"
