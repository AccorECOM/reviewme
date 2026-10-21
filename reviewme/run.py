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
             historique: str = "", changed_paths: list[str] | None = None,
             reader=None):
    """Exécute un reviewer (précheck déterministe puis LLM). Renvoie (spec, result)."""
    # Plugins déclarés par le reviewer : skills et agents dont il a besoin (installés une
    # fois, puis réutilisés). Un échec ici ne concerne QUE ce reviewer.
    if spec.plugins_marketplaces or spec.plugins_install:
        from .plugins import ensure_plugins
        from .reviewer import _find_claude
        ensure_plugins(spec, _find_claude(config), logger)

    blocks = [context[k] for k in spec.requires if k in context]
    # Ce que le reviewer a réellement reçu, pour le registre (`runs.jsonl`).
    sources: list[dict] = [{"path": k, "bytes": len(context[k]), "truncated": False,
                            "source": "context"} for k in spec.requires if k in context]

    # Ce qui a déjà été dit sur cette PR, et ce qu'on y a répondu.
    if historique:
        blocks.append(historique)
        sources.append({"path": "historique", "bytes": len(historique), "truncated": False,
                        "source": "historique"})

    # Conventions du dépôt : déclarées dans reviewer.toml, lues à la source (D13).
    from .repo_context import build_repo_context
    conventions = build_repo_context(spec, config.repo_path, logger, sources, reader)
    if conventions:
        blocks.append(conventions)

    # Instructions du dépôt lues sans rien déclarer (CLAUDE.md, AGENTS.md, .claude/rules),
    # filtrées sur les fichiers modifiés. `[context] auto = false` pour s'en passer.
    if spec.context_auto and changed_paths and not config.baseline:
        from .repo_context import build_auto_context
        auto = build_auto_context(changed_paths, config.repo_path, logger, sources,
                                  skip=spec.context_read, reader=reader)
        if auto:
            blocks.append(auto)

    extra = "\n\n".join(blocks)

    if spec.precheck:
        # ADR v3 D7 : le déterministe d'abord — ce qu'un script sait prouver ne doit pas être
        # payé au LLM (ex. « clé présente en fr, absente en de »).
        from .precheck import run_precheck
        facts = run_precheck(spec, config, logger, diff)
        if facts:
            extra = f"{extra}\n\n{facts}" if extra else facts
            sources.append({"path": spec.precheck, "bytes": len(facts),
                            "truncated": "_(sortie tronquée)_" in facts, "source": "precheck"})

    result = run_reviewer(pr_number, title, diff, config, spec=spec, extra_context=extra)
    result.metadata["context_files"] = sources

    # Passe de vérification (cf. `verify.py`) : seuls les findings au-dessus du seuil, les
    # autres sont déjà écartés. Son coût s'ajoute à celui du reviewer.
    if config.verify and spec.verify and result.parsed_ok:
        from .verify import verify_findings
        candidats = [f for f in result.findings if f.confidence >= config.confidence_threshold]
        bilan = verify_findings(pr_number, title, diff, candidats, config, spec,
                                extra_context=extra,
                                spent_usd=result.metadata.get("cost_usd", 0) or 0)
        result.metadata["verification"] = bilan
        result.metadata["review_cost_usd"] = result.metadata.get("cost_usd", 0) or 0
        result.metadata["cost_usd"] = result.metadata["review_cost_usd"] + bilan.get("cost_usd", 0)
    logger.info("PR #%s [%s] : %d findings, parsed=%s, modèle=%s, coût $%.4f",
                pr_number, spec.id, len(result.findings), result.parsed_ok,
                result.metadata.get("model", "?"), result.metadata.get("cost_usd", 0))
    return spec, result


def _tag_preexisting(outcomes, ctx, base_ref: str | None, config: Config,
                     logger: logging.Logger, pr_number: int, diff: str) -> None:
    """Tague les findings postables qui portent sur du code déplacé ou copié (cf.
    `preexisting.py`). Ils iront dans le résumé, marqués comme tels."""
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
        logger.info("PR #%s : %d finding(s) sur du code préexistant (déplacé ou copié)",
                    pr_number, n)


def trusted_base(base_ref: str | None, default_branch: str | None) -> bool:
    """La branche de base est-elle une source de config de confiance ?

    `base_ref` vient de la PR : quiconque pousse une branche peut ouvrir une PR VERS elle
    et y avoir préparé la config (et le precheck) qui la relira. Seules la branche par
    défaut et les motifs de `REVIEWME_TRUSTED_BASES` (ex. `develop,release/*`) comptent.
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
    """La config du projet telle qu'elle est sur la BRANCHE DE BASE, pas sur la PR.

    Quand la config vit dans le dépôt relu (`REVIEWME_CONFIG_HOME` sous `REPO_PATH`, ex.
    `.reviewme/`), le checkout est celui de la PR : un tiers pourrait réécrire le
    `system.md` du relecteur qui le relit, ou son precheck — du code exécuté avec les
    secrets de la CI. On extrait donc ce dossier depuis `origin/<base>` dans `workdir`.
    « Une PR ne peut pas changer la façon dont elle est relue » : une modification de la
    config ne s'applique qu'une fois mergée.

    Config hors du dépôt (dépôt de config externe, `config/projects/` du moteur) : rien à
    faire. `REVIEWME_CONFIG_FROM_BASE=0` désactive (déconseillé ; jamais lu du `.env` d'une
    instance). Lève ProjectConfigError si la config est introuvable sur la base : on ne
    retombe PAS sur celle de la PR.
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
    # Chemins comparés SANS résoudre les liens : un `.reviewme` remplacé par un lien vers
    # l'extérieur ne doit pas passer pour un dépôt de config externe (lu depuis la PR).
    repo = os.path.abspath(os.path.expanduser(config.repo_path))
    cible = os.path.abspath(os.path.expanduser(home))
    if os.path.commonpath([repo, cible]) != repo:
        return config                       # dépôt de config externe : déjà hors de la PR
    rel = Path(os.path.relpath(cible, repo)).as_posix()
    if not base_ref:
        raise ProjectConfigError("branche de base inconnue : impossible de lire la config "
                                 "hors de la PR")
    if not trusted_base(base_ref, default_branch):
        raise ProjectConfigError(
            f"branche de base '{base_ref}' non reconnue comme source de config (branche par "
            f"défaut ou REVIEWME_TRUSTED_BASES) : la PR n'est pas relue")
    try:
        out = subprocess.run(["git", "archive", "--format=tar", f"origin/{base_ref}", "--", rel],
                             cwd=repo, capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ProjectConfigError(f"lecture de la config sur origin/{base_ref} impossible "
                                 f"({type(e).__name__})") from e
    if out.returncode != 0:
        raise ProjectConfigError(
            f"`{rel}` introuvable sur origin/{base_ref} : la config d'une PR ne s'applique "
            f"qu'une fois mergée ({out.stderr.decode(errors='replace').strip()[:200]})")
    try:
        with tarfile.open(fileobj=io.BytesIO(out.stdout)) as tar:
            tar.extractall(workdir, filter="data")
    except (tarfile.TarError, OSError) as e:
        raise ProjectConfigError(f"archive de la config sur origin/{base_ref} refusée "
                                 f"({type(e).__name__})") from e
    if not (Path(workdir) / rel).is_dir():
        raise ProjectConfigError(f"`{rel}` vide dans l'archive de origin/{base_ref} "
                                 f"(export-ignore ?)")
    logger.info("Config lue sur origin/%s (`%s`), pas sur la PR", base_ref, rel)
    return dataclasses.replace(config, config_home=str(Path(workdir) / rel))


def run_review(pr: dict, config: Config, gh: GitHubClient, logger: logging.Logger,
               *, force: bool = False) -> dict:
    """Review une PR (dict complet de l'API GitHub). Renvoie un dict de compteurs."""
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
                # Refspec explicite : sur un clone CI à branche unique, `git fetch origin
                # <base>` ne met à jour que FETCH_HEAD, et `origin/<base>` resterait absent.
                subprocess.run(["git", "fetch", "origin",
                                f"+refs/heads/{base_ref}:refs/remotes/origin/{base_ref}",
                                "--quiet", "--depth", "50"],
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
            config = config_from_base(
                config, base_ref, workdir, logger,
                default_branch=((pr.get("base") or {}).get("repo") or {}).get("default_branch"))
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

        # Conventions et instructions du dépôt : lues sur la branche de base, pas sur la PR
        # (cf. `repo_context.RepoReader`), sauf si la lecture sur la base est désactivée.
        from .repo_context import RepoReader
        reader = RepoReader(config.repo_path, base_ref if config.config_from_base else None)

        # --- Exécution parallèle (D5) : chaque reviewer est un subprocess indépendant ---
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
        if not config.baseline:
            _tag_preexisting(outcomes, ctx, base_ref, config, logger, pr_number, diff)
        multi = len(outcomes) > 1
        prepared: list[PreparedReview] = [
            prepare(pr, config, ctx, result, logger, spec.id,
                    output_mode=spec.output_mode,
                    prefix=f"[{spec.id.upper()}]" if multi else "",
                    silent_when_clean=spec.silent_when_clean)
            for spec, result in outcomes
        ]
        # Registre : chaque finding avec son sort, posté ou non (cf. `ledger.py`). Après
        # `post_all`, qui fixe les sorts `capped` et les ids des commentaires postés ; et
        # même si `post_all` échoue, pour que le run (et son coût) reste mesurable.
        #
        # Le devenir des remarques (👍/👎, réponses) n'est PAS relevé ici : ce serait des
        # centaines d'appels API sur le chemin critique de chaque push. C'est le travail du
        # job de nuit (`reviewme enrich`).
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

        # Compteurs agrégés, sans contenu : transportables par un cache de CI.
        #
        # JAMAIS en mode simulé : les findings sont fabriqués et le coût est nul. Les
        # enregistrer gonflerait « remarques postées » et le nombre de runs tout en laissant
        # le coût inchangé — donc un ROI faussé, dans le sens flatteur, par des données qui
        # ne mesurent rien. Un DRY-RUN, lui, reste comptabilisé : ses findings sont réels.
        if config.fake:
            logger.info("PR #%s : mode simulé, statistiques NON enregistrées "
                        "(findings fabriqués, coût nul)", pr_number)
        elif config.baseline:
            # Le témoin a son registre (variant `baseline`) ; il ne doit pas doubler les
            # compteurs d'exploitation de la chaîne réelle.
            logger.info("PR #%s : chaîne témoin, statistiques d'exploitation NON enregistrées",
                        pr_number)
        else:
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
