"""Claude Code plugins required by a reviewer.

A plugin brings skills and agents that the review engine can use during its analysis.
A reviewer declares what it needs, and the core installs it before launching the
review:

    [plugins]
    marketplaces = ["owner/marketplace-repo"]   # added first
    install      = ["mon-plugin"]               # or "mon-plugin@marketplace"

Idempotent: what is already installed is not reinstalled (a review should not pay for a
network resolution on every PR).

⚠️ SECURITY: installing a plugin means running **third-party code** in the review
environment, alongside the job's secrets. A plugin must be reviewed like code (same rule
as prechecks). The core still enforces its tool allowlist: a plugin skill cannot grant
itself more rights than the reviewer itself.
"""
from __future__ import annotations

import logging
import subprocess

_TIMEOUT_S = 180


class PluginError(RuntimeError):
    """Installation impossible: the reviewer that requires it cannot run properly."""


def _run(cli: str, args: list[str], logger: logging.Logger) -> tuple[int, str]:
    try:
        proc = subprocess.run([cli, "plugin", *args], capture_output=True, text=True,
                              timeout=_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        return 1, f"timeout after {_TIMEOUT_S}s"
    except OSError as e:
        return 1, str(e)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def installed_plugins(cli: str, logger: logging.Logger) -> set[str]:
    """Names of already-installed plugins, without their `@marketplace` suffix.

    Output of `claude plugin list`:

        Installed plugins:

          ❯ context7@claude-plugins-official
            Version: unknown
            Scope: user

    Detail lines (`Version:`, `Scope:`) are ignored; only those carrying a
    `name@marketplace` identifier count.
    """
    code, out = _run(cli, ["list"], logger)
    if code != 0:
        logger.info("`plugin list` unavailable: installation will be attempted without short-circuit")
        return set()

    names: set[str] = set()
    for raw in out.splitlines():
        line = raw.strip().lstrip("❯•-*").strip()
        if not line or ":" in line or "@" not in line:
            continue
        names.add(line.split("@", 1)[0].strip())
    return names


def ensure_plugins(spec, cli: str, logger: logging.Logger) -> None:
    """Install what the reviewer declares. Raises PluginError if impossible.

    The caller treats the failure as "this reviewer cannot run" — not as a failure of the
    whole review: the other reviewers remain useful.
    """
    if not (spec.plugins_marketplaces or spec.plugins_install):
        return

    for source in spec.plugins_marketplaces:
        code, out = _run(cli, ["marketplace", "add", source], logger)
        # Already added = success: the operation must stay replayable on every review.
        if code != 0 and "already" not in out.lower():
            raise PluginError(f"marketplace '{source}' not added: {out.strip()[:200]}")
        logger.info("[%s] marketplace ready: %s", spec.id, source)

    if not spec.plugins_install:
        return

    already = installed_plugins(cli, logger)
    for plugin in spec.plugins_install:
        short = plugin.split("@")[0]
        if short in already:
            logger.info("[%s] plugin already installed: %s", spec.id, short)
            continue
        code, out = _run(cli, ["install", plugin], logger)
        if code != 0 and "already" not in out.lower():
            raise PluginError(f"plugin '{plugin}' not installed: {out.strip()[:200]}")
        logger.info("[%s] plugin installed: %s", spec.id, plugin)
