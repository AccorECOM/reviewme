"""Réconciliateur : transforme des findings en commentaires inline, avec dédup et
cycle de vie des threads.

Garanties :
- ANTI-422 : chaque finding est validé contre le diff (`diff_utils.valid_positions`)
  AVANT le post. Les findings hors-diff vont dans le summary, jamais en inline. Sur un
  422 résiduel, on retombe sur un commentaire global (rien n'est perdu).
- DÉDUP STABLE : fingerprint = sha1(path + contenu réel de la ligne), PAS le snippet
  narré par le LLM. Même finding entre deux runs -> même marqueur -> pas de doublon.
- IDENTITÉ PAR MARQUEUR : on ne reconnaît NOS commentaires que par le marqueur
  `<!-- reviewme:REVIEWER:HASH -->`, jamais par l'auteur (sinon on toucherait les threads
  humains).
- CLOISONNEMENT PAR REVIEWER (ADR v3 D3) : la clé de dédup est le COUPLE
  (reviewer_id, hash). Sans ça, deux reviewers qui pointent la même ligne se dédupent
  mutuellement et le second est silencieusement avalé.
- RÉTRO-COMPAT : les marqueurs de la v0.2 (`<!-- reviewme:HASH -->`, sans reviewer) sont
  lus comme appartenant à `tech`, le reviewer unique de l'époque. Le HASH lui-même est
  INCHANGÉ (le reviewer_id est un préfixe, il n'entre PAS dans le sha1) : une PR déjà
  commentée par la v0.2 se dédupe donc normalement au premier run v3, sans salve de
  doublons.
- CYCLE DE VIE : si un finding persiste alors que son commentaire est devenu outdated
  (nouveau commit), on RÉPOND dans le thread existant au lieu de dupliquer un top-level.
- SÉCURITÉ : tout texte est scrubbé (secrets/chemins) avant post.

Découpage v3 (ADR v3 D5) : `prepare()` ne fait AUCUNE écriture (calcul pur, donc
parallélisable entre reviewers) ; `post_all()` est le SEUL point d'écriture GitHub, ce
qui permet de partager la dédup, de tenir la limite de 80 writes/min et d'appliquer le
plafond de commentaires par PR (D6) — impossible à calculer reviewer par reviewer.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field

from .config import Config
from .diff_utils import normalize_line, resolve_line, valid_positions
from .github_client import GitHubApiError, GitHubClient
from .models import ReviewResult, Severity

# Deux formes acceptées : `reviewme:<reviewer>:<hash>` (v3) et `reviewme:<hash>` (v0.2).
_MARKER_RE = re.compile(r"<!--\s*reviewme:(?:([a-z0-9][a-z0-9_-]{0,31}):)?([0-9a-f]{6,})\s*-->")

#: Reviewer attribué aux marqueurs v0.2 dépourvus d'identifiant (cf. ADR v3 D3).
LEGACY_REVIEWER_ID = "tech"
_SEV_LABEL = {
    Severity.BLOCKER: "[BLOCKER]",
    Severity.IMPORTANT: "[IMPORTANT]",
    Severity.MINOR: "[MINOR]",
}
#: Ordre de priorité quand le plafond D6 oblige à couper.
_SEV_RANK = {Severity.BLOCKER: 0, Severity.IMPORTANT: 1, Severity.MINOR: 2}
_SIGNATURE = "_ReviewMe · review automatisée_"


def fingerprint_hash(path: str, line_content: str) -> str:
    """Hash d'ancrage d'un finding. NE DOIT PAS CHANGER sans plan de migration.

    Volontairement INDÉPENDANT du numéro de ligne : c'est ce qui permet de ré-ancrer sans
    doublon quand le code se déplace. Limite acceptée : deux lignes au contenu
    normalisé identique dans un même fichier partagent le hash (rare ; le suivi fin de la 2e
    occurrence peut être perdu). Un index par (marqueur, ligne) est une piste v2.

    Le `reviewer_id` n'entre PAS ici : il est ajouté comme préfixe du marqueur (cf.
    `fingerprint`), ce qui rend les marqueurs v0.2 rétro-compatibles sans recalcul.
    """
    return hashlib.sha1(f"{path}\n{normalize_line(line_content)}".encode()).hexdigest()[:10]


def fingerprint(path: str, line_content: str,
                reviewer_id: str = LEGACY_REVIEWER_ID) -> str:
    """Marqueur HTML posté dans le corps du commentaire : `<!-- reviewme:<reviewer>:<hash> -->`."""
    return f"<!-- reviewme:{reviewer_id}:{fingerprint_hash(path, line_content)} -->"


def summary_fingerprint(reviewer_id: str) -> str:
    """Marqueur d'un commentaire GLOBAL de reviewer (mode `global`, ADR v3 D2).

    Ancré sur le seul reviewer : il n'y a qu'un commentaire global par reviewer et par PR,
    qu'on met à jour de commit en commit au lieu d'en empiler un nouveau à chaque fois.
    """
    h = hashlib.sha1(f"__summary__\n{reviewer_id}".encode()).hexdigest()[:10]
    return f"<!-- reviewme:{reviewer_id}:{h} -->"


def marker_key(body: str) -> tuple[str, str] | None:
    """(reviewer_id, hash) d'un commentaire existant, ou None s'il n'est pas de nous.

    Un marqueur v0.2 sans reviewer est attribué à `tech` (LEGACY_REVIEWER_ID) : c'est LA
    règle de migration, sans elle la première review v3 reposterait tout en double.
    """
    m = _MARKER_RE.search(body or "")
    if not m:
        return None
    return (m.group(1) or LEGACY_REVIEWER_ID, m.group(2))


def _scrub(text: str | None) -> str:
    from .scrub import scrub_text
    return scrub_text(text)


def _guard(text: str, logger: logging.Logger, pr_number: int) -> str:
    """Garde-fou final : ne JAMAIS poster un texte contenant encore un secret après scrub."""
    from .scrub import contains_secret
    if contains_secret(text):
        logger.warning("PR #%s : secret détecté après scrub — texte masqué avant post", pr_number)
        return "[contenu masqué — secret potentiel détecté après scrub]"
    return text


def _comment_body(finding, marker: str, prefix: str = "") -> str:
    label = _SEV_LABEL.get(finding.severity, "[MINOR]")
    head = f"{prefix} " if prefix else ""
    lines = [f"**{head}{label}** {_scrub(finding.message)}"]
    if finding.suggestion:
        lines.append(f"\n_Suggestion :_ {_scrub(finding.suggestion)}")
    lines.append(f"\n{marker}")
    return "\n".join(lines)


def _summary_body(result: ReviewResult, out_of_diff: list, head_sha: str,
                  title: str = "Tech Lead Review (automatisée)") -> str:
    parts = [f"## {title}"]
    if result.summary:
        parts.append(_scrub(result.summary))
    if out_of_diff:
        parts.append("\n**Remarques hors-diff** (lignes non modifiées par la PR) :")
        for f in out_of_diff:
            parts.append(f"- `{_scrub(f.path)}:{f.line}` — {_SEV_LABEL.get(f.severity, '[MINOR]')} {_scrub(f.message)}")
    parts.append(f"\n---\n{_SIGNATURE} · commit `{head_sha[:8]}`")
    return "\n".join(parts)


def _degraded_body(summary_body: str, batch: list) -> str:
    """Repli : l'inline a échoué (422 résiduel) -> tout en un commentaire global."""
    parts = [summary_body, "\n**Commentaires (inline indisponible)** :"]
    for c in batch:
        body = _MARKER_RE.sub("", c["body"]).strip()
        parts.append(f"- `{_scrub(c['path'])}:{c['line']}` — {body}")
    return "\n".join(parts)


def _api_comment(item: dict) -> dict:
    """Ne laisse passer que les champs acceptés par l'API (les clés `_*` sont internes)."""
    return {k: v for k, v in item.items() if not k.startswith("_")}


# --------------------------------------------------------------------------- contexte PR

@dataclass
class PrContext:
    """État GitHub lu UNE SEULE FOIS et partagé par tous les reviewers d'une PR."""
    valid: dict
    inline_by_marker: dict[tuple[str, str], dict] = field(default_factory=dict)
    global_by_marker: dict[tuple[str, str], dict] = field(default_factory=dict)


def load_pr_context(gh: GitHubClient, pr_number: int, *, with_globals: bool = False) -> PrContext:
    ctx = PrContext(valid=valid_positions(gh.get_pr_files(pr_number)))

    for c in gh.list_review_comments(pr_number):
        mk = marker_key(c.get("body", ""))
        if not mk or c.get("in_reply_to_id") is not None:
            continue
        prev = ctx.inline_by_marker.get(mk)
        # sur collision, préférer le commentaire NON-outdated (déterministe)
        if prev is None or (prev.get("position") is None and c.get("position") is not None):
            ctx.inline_by_marker[mk] = c

    if with_globals:
        for c in gh.list_issue_comments(pr_number):
            mk = marker_key(c.get("body", ""))
            if mk:
                ctx.global_by_marker[mk] = c

    return ctx


# --------------------------------------------------------------------------- préparation

@dataclass
class PreparedReview:
    """Résultat d'un reviewer, prêt à poster. Aucune écriture n'a encore eu lieu."""
    reviewer_id: str
    output_mode: str = "inline"
    summary: str = ""
    batch: list[dict] = field(default_factory=list)
    replies: list[tuple[int, str]] = field(default_factory=list)
    global_body: str | None = None          # mode `global`, ou fallback JSON illisible
    global_update_id: int | None = None     # commentaire global existant à mettre à jour
    threads_resolus: set = field(default_factory=set)   # évite de répondre deux fois
    counts: dict = field(default_factory=dict)


def prepare(pr: dict, config: Config, ctx: PrContext, result: ReviewResult,
            logger: logging.Logger, reviewer_id: str, *,
            output_mode: str = "inline", prefix: str = "") -> PreparedReview:
    """Calcule ce qu'il faudrait poster pour UN reviewer. Sans effet de bord réseau."""
    pr_number = pr["number"]
    head_sha = pr["head"]["sha"]
    counts = {"posted": 0, "replied": 0, "deduped": 0, "out_of_diff": 0,
              "invalid": 0, "dropped_low": 0, "capped": 0, "resolus": 0, "fallback": False}
    prep = PreparedReview(reviewer_id=reviewer_id, output_mode=output_mode, counts=counts)

    # --- Repli : JSON inexploitable -> commentaire global ---
    if not result.parsed_ok:
        logger.warning("PR #%s [%s] : sortie agent non-JSON, fallback commentaire global",
                       pr_number, reviewer_id)
        counts["fallback"] = True
        prep.global_body = _guard(
            f"## Review automatisée ({reviewer_id})\n\n{_scrub(result.raw)}\n\n"
            f"---\n{_SIGNATURE} · commit `{head_sha[:8]}`", logger, pr_number)
        return prep

    # --- Validation contre le diff (anti-422) ---
    inline: list = []
    out_of_diff: list = []
    for f in result.findings:
        if f.confidence < config.confidence_threshold:
            counts["dropped_low"] += 1
            continue
        fmap = ctx.valid.get(f.path, {})
        resolved = resolve_line(fmap, f.line, f.snippet) if fmap else None
        if resolved is None:
            f.valid = False
            out_of_diff.append(f)
            counts["out_of_diff"] += 1
        else:
            # re-ancrage sur la ligne réelle (corrige l'imprécision de numéro de ligne du LLM)
            f.line, f.line_content = resolved
            f.valid = True
            inline.append(f)

    title = f"Review automatisée — {reviewer_id}" if prefix else "Tech Lead Review (automatisée)"
    prep.summary = _guard(_summary_body(result, out_of_diff, head_sha, title), logger, pr_number)

    # --- Mode `global` : un unique commentaire, mis à jour de commit en commit ---
    if output_mode == "global":
        marker = summary_fingerprint(reviewer_id)
        existing = ctx.global_by_marker.get(marker_key(marker))
        prep.global_body = f"{prep.summary}\n\n{marker}"
        prep.global_update_id = existing.get("id") if existing else None
        return prep

    # --- Réconciliation inline ---
    for f in inline:
        marker = fingerprint(f.path, f.line_content, reviewer_id)
        # La clé de dédup est le COUPLE (reviewer, hash) : deux reviewers peuvent commenter la
        # même ligne sans s'annuler l'un l'autre (ADR v3 D3).
        existing = ctx.inline_by_marker.get((reviewer_id, fingerprint_hash(f.path, f.line_content)))
        if existing is None:
            prep.batch.append({
                "path": f.path, "line": f.line, "side": "RIGHT",
                "body": _guard(_comment_body(f, marker, prefix), logger, pr_number),
                "_severity": f.severity, "_confidence": f.confidence, "_reviewer": reviewer_id,
            })
        elif existing.get("position") is None:
            # finding toujours là mais commentaire devenu outdated -> répondre dans le thread
            prep.replies.append((existing["id"],
                                 f"Toujours d'actualité sur `{head_sha[:8]}` "
                                 f"(le code de cette ligne a évolué). {_SIGNATURE}"))
        else:
            counts["deduped"] += 1

    # --- Points corrigés : le commentaire existe encore, le finding a disparu ---
    # Sans ça, une remarque traitée reste ouverte indéfiniment et personne ne sait qu'elle
    # l'est. On ne le déduit QUE si la ligne commentée a réellement changé
    # (`position is None` = le commentaire est devenu obsolète côté GitHub) : sinon, un
    # modèle qui varie d'un run à l'autre suffirait à annoncer une correction imaginaire.
    reproduits = {fingerprint_hash(f.path, f.line_content) for f in inline}
    for (rid, empreinte), commentaire in ctx.inline_by_marker.items():
        if rid != reviewer_id or empreinte in reproduits:
            continue
        if commentaire.get("position") is not None:
            continue                       # la ligne n'a pas bougé : on ne conclut rien
        if commentaire.get("id") in prep.threads_resolus:
            continue
        prep.threads_resolus.add(commentaire["id"])
        prep.replies.append((commentaire["id"],
                             f"Ce point n'apparaît plus sur `{head_sha[:8]}` — corrigé "
                             f"ou disparu du diff. {_SIGNATURE}"))
        counts["resolus"] = counts.get("resolus", 0) + 1

    if counts["dropped_low"]:
        logger.info("PR #%s [%s] : %d finding(s) sous le seuil de confiance (%d) non postés",
                    pr_number, reviewer_id, counts["dropped_low"], config.confidence_threshold)
    return prep


# --------------------------------------------------------------------------- écriture

def _apply_cap(prepared: list[PreparedReview], cap: int, logger: logging.Logger,
               pr_number: int) -> list[dict]:
    """Plafond de commentaires PAR PR, tous reviewers confondus (ADR v3 D6).

    Quatre reviewers zélés = 15+ commentaires = le dev décroche et désactive le bot. On
    garde les plus graves, puis les plus sûrs ; le reste est journalisé, pas posté.
    """
    items = [c for p in prepared for c in p.batch]
    if cap <= 0 or len(items) <= cap:
        return items

    items.sort(key=lambda c: (_SEV_RANK.get(c["_severity"], 3), -c["_confidence"]))
    kept, dropped = items[:cap], items[cap:]
    for p in prepared:
        p.counts["capped"] = sum(1 for c in dropped if c["_reviewer"] == p.reviewer_id)
    logger.info("PR #%s : plafond de %d commentaires atteint, %d finding(s) non postés : %s",
                pr_number, cap, len(dropped),
                ", ".join(f"{c['_reviewer']}:{c['path']}:{c['line']}" for c in dropped))
    return kept


def post_all(pr: dict, config: Config, gh: GitHubClient, prepared: list[PreparedReview],
             logger: logging.Logger) -> dict:
    """SEUL point d'écriture GitHub (ADR v3 D5). Applique le plafond D6 puis poste."""
    pr_number = pr["number"]
    head_sha = pr["head"]["sha"]
    # `posted` reste le total (compatibilité), mais on distingue la nature : sans ça, un
    # unique commentaire global se lisait « 1 inline » alors qu'aucun finding n'était passé.
    totals = {"posted": 0, "posted_inline": 0, "posted_global": 0, "replied": 0,
              "deduped": 0, "out_of_diff": 0, "invalid": 0, "dropped_low": 0,
              "capped": 0, "resolus": 0, "fallback": False, "reviewers": len(prepared)}
    for p in prepared:
        for k in ("deduped", "out_of_diff", "invalid", "dropped_low", "capped", "resolus"):
            totals[k] += p.counts.get(k, 0)
        totals["fallback"] = totals["fallback"] or p.counts.get("fallback", False)

    batch = _apply_cap(prepared, config.max_comments_per_pr, logger, pr_number)
    replies = [r for p in prepared for r in p.replies]
    globals_ = [p for p in prepared if p.global_body]
    inline_summaries = [p.summary for p in prepared if p.output_mode != "global" and p.summary]
    summary_body = "\n\n---\n\n".join(inline_summaries) if inline_summaries else ""
    out_of_diff_only = any(p.counts.get("out_of_diff") for p in prepared)

    if config.dry_run:
        # En dry-run, AFFICHER ce qui serait posté : c'est tout l'intérêt du mode, sans quoi
        # on ne peut ni juger la pertinence ni calibrer le seuil de confiance.
        logger.info("PR #%s [DRY-RUN] : %d inline, %d global, %d réponses",
                    pr_number, len(batch), len(globals_), len(replies))
        for c in batch:
            body = _MARKER_RE.sub("", c["body"]).replace("\n", " ").strip()
            logger.info("  [%s] %s:%s (confiance %s) — %s",
                        c["_reviewer"], c["path"], c["line"], c["_confidence"], body[:400])
        for p in prepared:
            if p.global_body:
                extrait = _MARKER_RE.sub("", p.global_body).strip()
                logger.info("  [%s] commentaire global :\n%s", p.reviewer_id, extrait[:1500])
            elif p.summary and p.output_mode != "global":
                logger.info("  [%s] résumé :\n%s", p.reviewer_id, p.summary[:1000])
        totals["posted"] = totals["posted_inline"] = len(batch)
        totals["posted_global"] = len(globals_)
        return totals

    # --- Post inline : UN seul write pour tous les reviewers (limite 80 créations/min) ---
    if batch:
        api_batch = [_api_comment(c) for c in batch]
        try:
            gh.create_review(pr_number, head_sha, summary_body, api_batch, event="COMMENT")
            totals["posted_inline"] = len(api_batch)
            totals["posted"] = len(api_batch)
        except GitHubApiError as e:
            if e.status_code == 422:
                logger.warning("PR #%s : 422 sur le batch review, repli commentaire global", pr_number)
                gh.post_issue_comment(pr_number, _guard(_degraded_body(summary_body, api_batch),
                                                        logger, pr_number))
                totals["fallback"] = True
            else:
                raise
    elif summary_body and out_of_diff_only:
        # pas d'inline, mais des remarques hors-diff à délivrer (un commentaire global par commit).
        # NB : une PR totalement propre ne reçoit PAS de commentaire (évite le spam "RAS" à chaque
        # commit) ; le "LGTM" dédupliqué est une amélioration v2.
        gh.post_issue_comment(pr_number, summary_body)

    # --- Commentaires globaux (reviewers en mode `global`, fallbacks) ---
    for p in globals_:
        body = _guard(p.global_body, logger, pr_number)
        try:
            if p.global_update_id:
                gh.update_issue_comment(p.global_update_id, body)
            else:
                gh.post_issue_comment(pr_number, body)
            totals["posted_global"] += 1
            totals["posted"] += 1
        except GitHubApiError:
            logger.warning("PR #%s [%s] : échec du commentaire global", pr_number, p.reviewer_id)

    # --- Réponses dans les threads (ré-ancrage / dialogue) ---
    for cid, msg in replies:
        try:
            gh.reply_to_comment(pr_number, cid, msg)
            totals["replied"] += 1
        except GitHubApiError:
            logger.warning("PR #%s : échec réponse thread %s", pr_number, cid)

    logger.info("PR #%s : %d inline, %d global, %d réponses, %d dédup, %d hors-diff, "
                "%d sous-seuil, %d plafonnés, %d résolus",
                pr_number, totals["posted_inline"], totals["posted_global"], totals["replied"],
                totals["deduped"], totals["out_of_diff"], totals["dropped_low"],
                totals["capped"], totals["resolus"])
    return totals


def reconcile(pr: dict, config: Config, gh: GitHubClient, result: ReviewResult,
              logger: logging.Logger, reviewer_id: str | None = None) -> dict:
    """Façade mono-reviewer : prépare puis poste en une passe."""
    reviewer_id = reviewer_id or getattr(config, "reviewer_id", None) or LEGACY_REVIEWER_ID
    ctx = load_pr_context(gh, pr["number"])
    prep = prepare(pr, config, ctx, result, logger, reviewer_id)
    return post_all(pr, config, gh, [prep], logger)
