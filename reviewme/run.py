"""`run_review` : la fonction commune à TOUTES les entrées (CLI, poll, webhook).

C'est la brique one-shot (« review cette PR, une fois, puis rends la main »). Chaque
point d'entrée ne fait que résoudre une PR puis appeler cette fonction — aucune logique
de review dupliquée ailleurs.

Fan-out v3 (ADR v3 D4/D5/D8) :
  1. résolution du projet (`PROJECT`) et de ses reviewers ;
  2. SÉLECTION : un reviewer ne tourne que si le diff le concerne (`when.paths`) et si son
     contexte requis est disponible (ex. ticket Jira) — sans ce filtre, chaque PR paierait
     tous les reviewers du projet ;
  3. exécution EN PARALLÈLE (chaque reviewer est un `claude -p` indépendant, avec son propre
     `--max-budget-usd`). Le coût total de la PR est journalisé ; un arrêt net au plafond
     supposerait une exécution séquentielle par priorité — non retenu pour l'instant ;
  4. écriture GitHub UNIQUE via `post_all` (dédup partagée, rate limit, plafond D6).
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
    """Contextes externes disponibles pour cette PR (ADR v3 D9).

    Chaque fournisseur est OPTIONNEL : indisponible -> le reviewer qui l'exige est skippé,
    jamais une erreur. Le core ne dépend d'aucune source externe.
    """
    context: dict[str, str] = {}
    try:
        from .context.jira import fetch_ticket_context
        ticket = fetch_ticket_context(pr, config, logger)
        if ticket:
            context["jira_ticket"] = ticket
    except Exception as e:
        logger.info("Contexte Jira indisponible (%s) — les reviewers qui l'exigent seront skippés", e)
    return context, set(context)


def _run_one(spec: ReviewerSpec, pr_number: int, title: str, diff: str,
             config: Config, context: dict, logger: logging.Logger,
             historique: str = ""):
    """Exécute un reviewer (précheck déterministe puis LLM). Renvoie (spec, result)."""
    # Plugins déclarés par le reviewer : skills et agents dont il a besoin (installés une
    # fois, puis réutilisés). Un échec ici ne concerne QUE ce reviewer.
    if spec.plugins_marketplaces or spec.plugins_install:
        from .plugins import ensure_plugins
        from .reviewer import _find_claude
        ensure_plugins(spec, _find_claude(config), logger)

    blocks = [context[k] for k in spec.requires if k in context]

    # Ce qui a déjà été dit sur cette PR, et ce qu'on y a répondu.
    if historique:
        blocks.append(historique)

    # Conventions du dépôt : déclarées dans reviewer.toml, lues à la source (D13).
    from .repo_context import build_repo_context
    conventions = build_repo_context(spec, config.repo_path, logger)
    if conventions:
        blocks.append(conventions)

    extra = "\n\n".join(blocks)

    if spec.precheck:
        # ADR v3 D7 : le déterministe d'abord — ce qu'un script sait prouver ne doit pas être
        # payé au LLM (ex. « clé présente en fr, absente en de »).
        from .precheck import run_precheck
        facts = run_precheck(spec, config, logger, diff)
        if facts:
            extra = f"{extra}\n\n{facts}" if extra else facts

    result = run_reviewer(pr_number, title, diff, config, spec=spec, extra_context=extra)
    logger.info("PR #%s [%s] : %d findings, parsed=%s, modèle=%s, coût $%.4f",
                pr_number, spec.id, len(result.findings), result.parsed_ok,
                result.metadata.get("model", "?"), result.metadata.get("cost_usd", 0))
    return spec, result


def run_review(pr: dict, config: Config, gh: GitHubClient, logger: logging.Logger,
               *, force: bool = False) -> dict:
    """Review une PR (dict complet de l'API GitHub). Renvoie un dict de compteurs."""
    pr_number = pr["number"]
    head_sha = pr["head"]["sha"]
    title = pr.get("title", "")
    author = pr.get("user", {}).get("login", "")

    if not force and is_reviewed(pr_number, head_sha):
        logger.info("PR #%s déjà reviewée à %s, skip", pr_number, head_sha[:8])
        return {"skipped": True}

    logger.info("Review PR #%s : %s", pr_number, title,
                extra={"pr_number": pr_number, "pr_title": title, "status": "started"})

    try:
        # Contexte : fetch best-effort de la branche de base, pour que l'agent puisse
        # comparer avec `git log/diff`. VRAIMENT best-effort : sur un clone CI peu profond
        # ou un gros dépôt, ce fetch peut traîner ou échouer — il ne doit jamais faire
        # tomber la review, qui n'en dépend pas. `FETCH_BASE_TIMEOUT=0` le désactive.
        base_ref = pr.get("base", {}).get("ref")
        fetch_timeout = int(os.environ.get("FETCH_BASE_TIMEOUT", "30"))
        if base_ref and fetch_timeout > 0:
            try:
                subprocess.run(["git", "fetch", "origin", base_ref, "--quiet", "--depth", "50"],
                               cwd=config.repo_path, capture_output=True,
                               timeout=fetch_timeout, check=False)
            except (subprocess.TimeoutExpired, OSError) as e:
                logger.info("PR #%s : fetch de '%s' abandonné (%s) — la review continue "
                            "sans l'historique de la branche de base",
                            pr_number, base_ref, type(e).__name__)

        diff = gh.get_pr_diff(pr_number)
        if not diff.strip():
            logger.warning("PR #%s : diff vide, skip", pr_number)
            return {"skipped": True, "reason": "empty_diff"}

        diff_size_kb = len(diff.encode("utf-8")) / 1024

        try:
            project = resolve_project(config)
        except ProjectConfigError as e:
            logger.error("Config de projet invalide : %s", e)
            return {"error": f"config projet : {e}"}

        # --- Sélection des reviewers (D4) ---
        files = gh.get_pr_files(pr_number)
        changed_paths = [f.get("filename", "") for f in files]
        context, available = _gather_context(pr, config, logger)
        selected, skipped = select_reviewers(project, changed_paths, available)
        for spec, reason in skipped:
            logger.info("PR #%s : reviewer '%s' non déclenché (%s)", pr_number, spec.id, reason)
        if not selected:
            logger.warning("PR #%s : aucun reviewer déclenché, rien à faire", pr_number)
            return {"skipped": True, "reason": "no_reviewer_triggered"}

        logger.info("PR #%s : diff %.1f KB, projet=%s, reviewers=%s",
                    pr_number, diff_size_kb, project.name, [s.id for s in selected])

        # --- Exécution parallèle (D5) : chaque reviewer est un subprocess indépendant ---
        engagement: dict = {}
        historique = build_history_context(gh, pr_number, logger, engagement)

        outcomes: list[tuple[ReviewerSpec, object]] = []
        if len(selected) == 1:
            outcomes.append(_run_one(selected[0], pr_number, title, diff, config, context,
                                     logger, historique))
        else:
            with ThreadPoolExecutor(max_workers=min(len(selected), 4)) as pool:
                futures = [pool.submit(_run_one, s, pr_number, title, diff, config, context,
                                       logger, historique)
                           for s in selected]
                for fut in futures:
                    try:
                        outcomes.append(fut.result())
                    except Exception as e:
                        logger.error("PR #%s : reviewer en échec : %s", pr_number, e)

        if not outcomes:
            return {"error": "tous les reviewers ont échoué"}

        # --- Coût de la PR (D8) : budget appliqué PAR reviewer (--max-budget-usd) ; ici on
        # totalise et on alerte au dépassement du plafond PR (pas d'arrêt : les reviewers ont
        # déjà tourné en parallèle).
        total_cost = sum(r.metadata.get("cost_usd", 0) for _, r in outcomes)
        pr_budget = config.max_budget_usd * max(len(outcomes), 1)
        if total_cost > pr_budget:
            logger.warning("PR #%s : coût total $%.2f au-dessus du plafond attendu $%.2f",
                           pr_number, total_cost, pr_budget)

        # --- Préparation (sans écriture), puis UN SEUL point d'écriture (D5) ---
        needs_globals = any(s.output_mode == "global" for s, _ in outcomes)
        ctx = load_pr_context(gh, pr_number, with_globals=needs_globals)
        multi = len(outcomes) > 1
        prepared: list[PreparedReview] = [
            prepare(pr, config, ctx, result, logger, spec.id,
                    output_mode=spec.output_mode,
                    prefix=f"[{spec.id.upper()}]" if multi else "")
            for spec, result in outcomes
        ]
        counts = post_all(pr, config, gh, prepared, logger)
        counts["cost_usd"] = round(total_cost, 4)
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

        # Compteurs agrégés, sans contenu : transportables par un cache de CI.
        enregistrer_stats(pr_number, head_sha, author, project.name,
                          [s.id for s, _ in outcomes], counts, total_cost,
                          counts.get("duration_ms", 0) or 0, diff_size_kb, logger)

        if not config.dry_run:
            mark_reviewed(pr_number, head_sha, title, status="success")
        logger.info("PR #%s : terminé %s", pr_number, counts,
                    extra={"pr_number": pr_number, "pr_title": title, "status": "success"})
        return counts

    except Exception as e:
        mark_error(pr_number, head_sha, title, str(e))
        logger.error("PR #%s : échec review : %s", pr_number, e,
                     extra={"pr_number": pr_number, "pr_title": title, "status": "error"})
        return {"error": str(e)}


def run_review_by_number(pr_number: int, config: Config, logger: logging.Logger,
                         gh: GitHubClient | None = None, *, force: bool = False) -> dict:
    """Résout une PR par son numéro puis la review (utilisé par le CLI et le webhook)."""
    own = gh is None
    gh = gh or GitHubClient(config)
    try:
        pr = gh.get_pr(pr_number)
        return run_review(pr, config, gh, logger, force=force)
    finally:
        if own:
            gh.close()
