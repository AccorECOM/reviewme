"""`run_review`: the function shared by ALL entry points (CLI, poll, webhook).

It is the one-shot building block ("review this PR, once, then return"). Each entry
point only resolves a PR and then calls this function — no review logic is duplicated
elsewhere.

Fan-out:
  1. resolve the project (`PROJECT`) and its reviewers;
  2. SELECTION: a reviewer only runs if the diff concerns it (`when.paths`) and its
     required context is available (e.g. Jira ticket) — without this filter, every PR
     would pay for all the project's reviewers;
  3. PARALLEL execution (each reviewer is an independent `claude -p`, with its own
     `--max-budget-usd`). The PR's total cost is logged; a hard stop at the cap would
     require sequential execution by priority — not adopted for now;
  4. a SINGLE GitHub write via `post_all` (shared dedup, rate limit, comment cap).
"""
from __future__ import annotations

import logging
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor

from .config import Config
from .github_client import GitHubClient
from .history import build_history_context
from .logging_ import save_review
from .projects import (
    ProjectConfigError,
    ReviewerSpec,
    resolve_project,
    select_reviewers,
)
from .reconciler import PreparedReview, load_pr_context, post_all, prepare
from .reviewer import run_review as run_reviewer
from .scrub import scrub_text
from .state import is_reviewed, mark_error, mark_reviewed
from .stats import enregistrer as enregistrer_stats


def _gather_context(pr: dict, config: Config, logger: logging.Logger) -> tuple[dict, set[str]]:
    """External contexts available for this PR.

    Each provider is OPTIONAL: unavailable -> the reviewer that requires it is skipped,
    never an error. The core depends on no external source.
    """
    context: dict[str, str] = {}
    try:
        from .context.jira import fetch_ticket_context
        ticket = fetch_ticket_context(pr, config, logger)
        if ticket:
            context["jira_ticket"] = ticket
    except Exception as e:
        logger.info("Jira context unavailable (%s) — reviewers that require it will be skipped", e)
    return context, set(context)


def _run_one(spec: ReviewerSpec, pr_number: int, title: str, diff: str,
             config: Config, context: dict, logger: logging.Logger,
             historique: str = "", changed_paths: list[str] | None = None,
             reader=None):
    """Runs one reviewer (deterministic precheck, then LLM). Returns (spec, result)."""
    # Plugins declared by the reviewer: skills and agents it needs (installed once,
    # then reused). A failure here only affects THIS reviewer.
    if spec.plugins_marketplaces or spec.plugins_install:
        from .plugins import ensure_plugins
        from .reviewer import _find_claude
        ensure_plugins(spec, _find_claude(config), logger)

    blocks = [context[k] for k in spec.requires if k in context]
    # What the reviewer actually received, for the ledger (`runs.jsonl`).
    sources: list[dict] = [{"path": k, "bytes": len(context[k]), "truncated": False,
                            "source": "context"} for k in spec.requires if k in context]

    # What has already been said on this PR, and what was answered.
    if historique:
        blocks.append(historique)
        sources.append({"path": "historique", "bytes": len(historique), "truncated": False,
                        "source": "historique"})

    # Repo conventions: declared in reviewer.toml, read at the source.
    from .repo_context import build_repo_context
    conventions = build_repo_context(spec, config.repo_path, logger, sources, reader)
    if conventions:
        blocks.append(conventions)

    # Repo instructions read without declaring anything (CLAUDE.md, AGENTS.md, .claude/rules),
    # filtered on the changed files. `[context] auto = false` to opt out.
    if spec.context_auto and changed_paths and not config.baseline:
        from .repo_context import build_auto_context
        auto = build_auto_context(changed_paths, config.repo_path, logger, sources,
                                  skip=spec.context_read, reader=reader)
        if auto:
            blocks.append(auto)

    extra = "\n\n".join(blocks)

    if spec.precheck:
        # Deterministic first — what a script can prove must not be paid
        # to the LLM (e.g. "key present in fr, missing in de").
        from .precheck import run_precheck
        facts = run_precheck(spec, config, logger, diff)
        if facts:
            extra = f"{extra}\n\n{facts}" if extra else facts
            sources.append({"path": spec.precheck, "bytes": len(facts),
                            "truncated": "_(output truncated)_" in facts, "source": "precheck"})

    result = run_reviewer(pr_number, title, diff, config, spec=spec, extra_context=extra)
    result.metadata["context_files"] = sources
    if not spec.findings and result.findings:
        # Reviewer without findings (e.g. `conseil`): whatever it produced despite the
        # instruction is neither verified nor posted.
        logger.info("[%s] %d finding(s) ignored: `findings = false`", spec.id,
                    len(result.findings))
        result.findings = []

    # Verification pass (see `verify.py`): only findings above the threshold, the others
    # are already discarded. Its cost adds up to the reviewer's.
    if config.verify and spec.verify and result.parsed_ok:
        from .verify import verify_findings
        candidats = [f for f in result.findings if f.confidence >= config.confidence_threshold]
        bilan = verify_findings(pr_number, title, diff, candidats, config, spec,
                                extra_context=extra,
                                spent_usd=result.metadata.get("cost_usd", 0) or 0)
        result.metadata["verification"] = bilan
        result.metadata["review_cost_usd"] = result.metadata.get("cost_usd", 0) or 0
        result.metadata["cost_usd"] = result.metadata["review_cost_usd"] + bilan.get("cost_usd", 0)
    logger.info("PR #%s [%s]: %d findings, parsed=%s, model=%s, cost $%.4f",
                pr_number, spec.id, len(result.findings), result.parsed_ok,
                result.metadata.get("model", "?"), result.metadata.get("cost_usd", 0))
    return spec, result


def _tag_preexisting(outcomes, ctx, base_ref: str | None, config: Config,
                     logger: logging.Logger, pr_number: int, diff: str) -> None:
    """Tags postable findings that are about moved or copied code (see
    `preexisting.py`). They will go into the summary, marked as such."""
    from .diff_utils import resolve_line
    from .preexisting import removed_lines, tag_preexisting

    def anchor_of(f):
        fmap = ctx.valid.get(f.path, {})
        resolved = resolve_line(fmap, f.line, f.snippet) if fmap else None
        if resolved is None:
            return None
        pos = fmap.get(resolved[0])
        return (*resolved, pos.added if pos is not None else True)

    candidats = [f for _, r in outcomes for f in r.findings
                 if f.confidence >= config.confidence_threshold
                 and (f.verification or {}).get("status") not in ("refuted", "missing",
                                                                  "uncited", "invalid")]
    n = tag_preexisting(candidats, config.repo_path, base_ref or "", logger, anchor_of,
                        removed_lines(diff))
    if n:
        logger.info("PR #%s: %d finding(s) on pre-existing code (moved or copied)",
                    pr_number, n)


def trusted_base(base_ref: str | None, default_branch: str | None) -> bool:
    """Is the base branch a trusted config source?

    `base_ref` comes from the PR: anyone who pushes a branch can open a PR TOWARDS it
    and have prepared there the config (and the precheck) that will review it. Only the
    default branch and the patterns of `REVIEWME_TRUSTED_BASES` (e.g. `develop,release/*`) count.
    """
    from fnmatch import fnmatchcase
    if not base_ref:
        return False
    motifs = [m.strip() for m in os.environ.get("REVIEWME_TRUSTED_BASES", "").split(",")
              if m.strip()]
    if default_branch:
        motifs.append(default_branch)
    return any(fnmatchcase(base_ref, m) for m in motifs)


def config_from_base(config: Config, base_ref: str | None, workdir: str,
                     logger: logging.Logger, *, default_branch: str | None = None) -> Config:
    """The project config as it is on the BASE BRANCH, not on the PR.

    When the config lives in the reviewed repo (`REVIEWME_CONFIG_HOME` under `REPO_PATH`,
    e.g. `.reviewme/`), the checkout is the PR's: a third party could rewrite the
    `system.md` of the reviewer that reviews it, or its precheck — code run with the
    CI secrets. So this folder is extracted from `origin/<base>` into `workdir`.
    "A PR cannot change how it is reviewed": a config change only applies once
    merged.

    Config outside the repo (external config repo, the engine's `config/projects/`):
    nothing to do. `REVIEWME_CONFIG_FROM_BASE=0` disables it (not recommended; never read
    from an instance's `.env`). Raises ProjectConfigError if the config cannot be found on
    the base: we do NOT fall back to the PR's.
    """
    import dataclasses
    import io
    import tarfile
    from pathlib import Path

    if not config.config_from_base:
        return config
    home = (config.config_home or os.environ.get("REVIEWME_CONFIG_HOME", "")).strip()
    if not home:
        return config
    # Paths compared WITHOUT resolving links: a `.reviewme` replaced by a link to the
    # outside must not pass for an external config repo (read from the PR).
    repo = os.path.abspath(os.path.expanduser(config.repo_path))
    cible = os.path.abspath(os.path.expanduser(home))
    if os.path.commonpath([repo, cible]) != repo:
        return config                       # external config repo: already outside the PR
    rel = Path(os.path.relpath(cible, repo)).as_posix()
    if not base_ref:
        raise ProjectConfigError("unknown base branch: cannot read the config "
                                 "outside the PR")
    if not trusted_base(base_ref, default_branch):
        raise ProjectConfigError(
            f"base branch '{base_ref}' is not a trusted config source (default branch "
            f"or REVIEWME_TRUSTED_BASES): the PR is not reviewed")
    try:
        out = subprocess.run(["git", "archive", "--format=tar", f"origin/{base_ref}", "--", rel],
                             cwd=repo, capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ProjectConfigError(f"cannot read the config from origin/{base_ref} "
                                 f"({type(e).__name__})") from e
    if out.returncode != 0:
        raise ProjectConfigError(
            f"`{rel}` not found on origin/{base_ref}: a PR's config only applies once "
            f"merged ({out.stderr.decode(errors='replace').strip()[:200]})")
    try:
        with tarfile.open(fileobj=io.BytesIO(out.stdout)) as tar:
            tar.extractall(workdir, filter="data")
    except (tarfile.TarError, OSError) as e:
        raise ProjectConfigError(f"config archive from origin/{base_ref} rejected "
                                 f"({type(e).__name__})") from e
    if not (Path(workdir) / rel).is_dir():
        raise ProjectConfigError(f"`{rel}` empty in the archive of origin/{base_ref} "
                                 f"(export-ignore?)")
    logger.info("Config read from origin/%s (`%s`), not from the PR", base_ref, rel)
    return dataclasses.replace(config, config_home=str(Path(workdir) / rel))


def run_review(pr: dict, config: Config, gh: GitHubClient, logger: logging.Logger,
               *, force: bool = False) -> dict:
    """Reviews a PR (full GitHub API dict). Returns a dict of counters."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="reviewme-config-") as workdir:
        return _run_review(pr, config, gh, logger, force=force, workdir=workdir)


def _run_review(pr: dict, config: Config, gh: GitHubClient, logger: logging.Logger,
                *, force: bool, workdir: str) -> dict:
    pr_number = pr["number"]
    head_sha = pr["head"]["sha"]
    title = pr.get("title", "")
    author = pr.get("user", {}).get("login", "")

    if not force and is_reviewed(pr_number, head_sha):
        logger.info("PR #%s already reviewed at %s, skip", pr_number, head_sha[:8])
        return {"skipped": True}

    logger.info("Review PR #%s: %s", pr_number, title,
                extra={"pr_number": pr_number, "pr_title": title, "status": "started"})

    try:
        # Context: best-effort fetch of the base branch, so the agent can compare with
        # `git log/diff`. TRULY best-effort: on a shallow CI clone or a large repo, this
        # fetch can drag on or fail — it must never bring down the review, which does not
        # depend on it. `FETCH_BASE_TIMEOUT=0` disables it.
        base_ref = pr.get("base", {}).get("ref")
        fetch_timeout = int(os.environ.get("FETCH_BASE_TIMEOUT", "30"))
        if base_ref and fetch_timeout > 0:
            try:
                # Explicit refspec: on a single-branch CI clone, `git fetch origin <base>`
                # only updates FETCH_HEAD, and `origin/<base>` would remain missing.
                subprocess.run(["git", "fetch", "origin",
                                f"+refs/heads/{base_ref}:refs/remotes/origin/{base_ref}",
                                "--quiet", "--depth", "50"],
                               cwd=config.repo_path, capture_output=True,
                               timeout=fetch_timeout, check=False)
            except (subprocess.TimeoutExpired, OSError) as e:
                logger.info("PR #%s: fetch of '%s' abandoned (%s) — the review continues "
                            "without the base branch history",
                            pr_number, base_ref, type(e).__name__)

        diff = gh.get_pr_diff(pr_number)
        if not diff.strip():
            logger.warning("PR #%s: empty diff, skip", pr_number)
            return {"skipped": True, "reason": "empty_diff"}

        diff_size_kb = len(diff.encode("utf-8")) / 1024

        try:
            config = config_from_base(
                config, base_ref, workdir, logger,
                default_branch=((pr.get("base") or {}).get("repo") or {}).get("default_branch"))
            project = resolve_project(config)
        except ProjectConfigError as e:
            logger.error("Invalid project config: %s", e)
            return {"error": f"project config: {e}"}

        # --- Reviewer selection ---
        files = gh.get_pr_files(pr_number)
        changed_paths = [f.get("filename", "") for f in files]
        context, available = _gather_context(pr, config, logger)
        selected, skipped = select_reviewers(project, changed_paths, available)
        for spec, reason in skipped:
            logger.info("PR #%s: reviewer '%s' not triggered (%s)", pr_number, spec.id, reason)
        if not selected:
            logger.warning("PR #%s: no reviewer triggered, nothing to do", pr_number)
            return {"skipped": True, "reason": "no_reviewer_triggered"}

        logger.info("PR #%s: diff %.1f KB, project=%s, reviewers=%s",
                    pr_number, diff_size_kb, project.name, [s.id for s in selected])

        # Repo conventions and instructions: read from the base branch, not from the PR
        # (see `repo_context.RepoReader`), unless reading from the base is disabled.
        from .repo_context import RepoReader
        reader = RepoReader(config.repo_path, base_ref if config.config_from_base else None)

        # --- Parallel execution: each reviewer is an independent subprocess ---
        engagement: dict = {}
        historique = build_history_context(gh, pr_number, logger, engagement)

        outcomes: list[tuple[ReviewerSpec, object]] = []
        if len(selected) == 1:
            outcomes.append(_run_one(selected[0], pr_number, title, diff, config, context,
                                     logger, historique, changed_paths, reader))
        else:
            with ThreadPoolExecutor(max_workers=min(len(selected), 4)) as pool:
                futures = [pool.submit(_run_one, s, pr_number, title, diff, config, context,
                                       logger, historique, changed_paths, reader)
                           for s in selected]
                for fut in futures:
                    try:
                        outcomes.append(fut.result())
                    except Exception as e:
                        logger.error("PR #%s: reviewer failed: %s", pr_number, e)

        if not outcomes:
            return {"error": "all reviewers failed"}

        # --- PR cost: budget applied PER reviewer (--max-budget-usd); here we total
        # it and warn when the PR cap is exceeded (no stop: the reviewers have already run
        # in parallel).
        total_cost = sum(r.metadata.get("cost_usd", 0) for _, r in outcomes)
        pr_budget = config.max_budget_usd * max(len(outcomes), 1)
        if total_cost > pr_budget:
            logger.warning("PR #%s: total cost $%.2f above the expected cap $%.2f",
                           pr_number, total_cost, pr_budget)

        # --- Preparation (no writes), then a SINGLE write point ---
        needs_globals = any(s.output_mode == "global" for s, _ in outcomes)
        ctx = load_pr_context(gh, pr_number, with_globals=needs_globals)
        if not config.baseline:
            _tag_preexisting(outcomes, ctx, base_ref, config, logger, pr_number, diff)
        multi = len(outcomes) > 1
        prepared: list[PreparedReview] = [
            prepare(pr, config, ctx, result, logger, spec.id,
                    output_mode=spec.output_mode,
                    prefix=f"[{spec.id.upper()}]" if multi else "",
                    silent_when_clean=spec.silent_when_clean,
                    # Only an advice reviewer (`findings = false`) shows advice:
                    # elsewhere, it would duplicate it.
                    advice=not spec.findings and not config.baseline,
                    language=spec.language)
            for spec, result in outcomes
        ]
        # Ledger: each finding with its fate, posted or not (see `ledger.py`). After
        # `post_all`, which sets the `capped` fates and the ids of posted comments; and
        # even if `post_all` fails, so the run (and its cost) stays measurable.
        #
        # What happens to comments afterwards (👍/👎, replies) is NOT collected here: that
        # would be hundreds of API calls on the critical path of every push. That is the
        # nightly job's work (`reviewme enrich`).
        from .ledger import record as enregistrer_registre
        try:
            counts = post_all(pr, config, gh, prepared, logger)
        except Exception:
            enregistrer_registre(pr, config, project, prepared, outcomes, diff, logger,
                                 status="post_failed")
            raise
        counts["cost_usd"] = round(total_cost, 4)
        counts["run_id"] = enregistrer_registre(pr, config, project, prepared, outcomes,
                                                diff, logger)
        counts.update(engagement)

        findings_total = sum(len(r.findings) for _, r in outcomes)
        save_review(pr_number, {
            "pr_number": pr_number, "pr_title": title, "pr_author": author,
            "head_sha": head_sha, "diff_size_kb": round(diff_size_kb, 1),
            "project": project.name,
            "reviewers": [s.id for s, _ in outcomes],
            "parsed_ok": all(r.parsed_ok for _, r in outcomes),
            "findings_count": findings_total,
            "counts": counts,
            "summary": scrub_text("\n\n".join(r.summary for _, r in outcomes if r.summary)),
            "cost_usd": total_cost,
            "duration_ms": max((r.metadata.get("duration_ms", 0) for _, r in outcomes), default=0),
            "input_tokens": sum(r.metadata.get("input_tokens", 0) for _, r in outcomes),
            "output_tokens": sum(r.metadata.get("output_tokens", 0) for _, r in outcomes),
        })

        # Aggregated counters, without content: transportable through a CI cache.
        #
        # NEVER in fake mode: findings are fabricated and the cost is zero. Recording them
        # would inflate "posted comments" and the run count while leaving the cost
        # unchanged — hence a skewed ROI, in the flattering direction, from data that
        # measures nothing. A DRY-RUN, however, is still counted: its findings are real.
        if config.fake:
            logger.info("PR #%s: fake mode, statistics NOT recorded "
                        "(fabricated findings, zero cost)", pr_number)
        elif config.baseline:
            # The baseline has its own ledger (variant `baseline`); it must not double the
            # operational counters of the real pipeline.
            logger.info("PR #%s: baseline pipeline, operational statistics NOT recorded",
                        pr_number)
        else:
            enregistrer_stats(pr_number, head_sha, author, project.name,
                              [s.id for s, _ in outcomes], counts, total_cost,
                              counts.get("duration_ms", 0) or 0, diff_size_kb, logger)

        if not config.dry_run:
            mark_reviewed(pr_number, head_sha, title, status="success")
        logger.info("PR #%s: done %s", pr_number, counts,
                    extra={"pr_number": pr_number, "pr_title": title, "status": "success"})
        return counts

    except Exception as e:
        mark_error(pr_number, head_sha, title, str(e))
        logger.error("PR #%s: review failed: %s", pr_number, e,
                     extra={"pr_number": pr_number, "pr_title": title, "status": "error"})
        return {"error": str(e)}


def run_review_by_number(pr_number: int, config: Config, logger: logging.Logger,
                         gh: GitHubClient | None = None, *, force: bool = False) -> dict:
    """Resolves a PR by its number then reviews it (used by the CLI and the webhook)."""
    own = gh is None
    gh = gh or GitHubClient(config)
    try:
        pr = gh.get_pr(pr_number)
        return run_review(pr, config, gh, logger, force=force)
    finally:
        if own:
            gh.close()
