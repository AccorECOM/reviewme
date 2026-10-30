"""Aggregated statistics, without review content.

`data/reviews/` keeps the details of a review — including the PR title and the summary
written by the model. That is useful locally, but not portable: on an ephemeral runner
it has to go through a cache shared between the app's builds, and there is no reason to
put PR content there.

This file only contains **counters**: how many reviews, at what cost, how many findings
posted, deduplicated, dropped. Enough to feed a dashboard and measure what the bot
brings, without carrying a single line of code or a title.

Append-only and idempotent per (PR, commit): replaying a build does not skew the totals.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from .config import DATA_DIR

STATS_FILE = DATA_DIR / "stats.json"
MAX_ENTREES = 5000          # ~1.5 MB: beyond that, the oldest entries are truncated


def _charger(chemin: Path) -> dict:
    if not chemin.exists():
        return {"version": 1, "runs": []}
    try:
        data = json.loads(chemin.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and "runs" in data else {"version": 1, "runs": []}
    except (OSError, ValueError):
        return {"version": 1, "runs": []}


def enregistrer(pr_number: int, head_sha: str, auteur: str, projet: str,
                reviewers: list[str], counts: dict, cout: float, duree_ms: int,
                diff_kb: float, logger: logging.Logger | None = None,
                chemin: Path | None = None) -> None:
    """Add a run. No title, no message, no code excerpt."""
    chemin = chemin or STATS_FILE
    data = _charger(chemin)

    cle = f"{pr_number}:{head_sha[:12]}"
    data["runs"] = [r for r in data["runs"] if r.get("cle") != cle]   # idempotent
    data["runs"].append({
        "cle": cle,
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "pr": pr_number,
        "auteur": auteur,              # GitHub login: it is a public identifier
        "projet": projet,
        "reviewers": reviewers,
        "diff_kb": round(diff_kb, 1),
        "cout_usd": round(cout, 4),
        "duree_s": round(duree_ms / 1000),
        "postes": counts.get("posted", 0),
        "postes_inline": counts.get("posted_inline", 0),
        "postes_global": counts.get("posted_global", 0),
        "dedupliques": counts.get("deduped", 0),
        "sous_seuil": counts.get("dropped_low", 0),
        "non_verifies": counts.get("dropped_unverified", 0),
        "hors_diff": counts.get("out_of_diff", 0),
        "plafonnes": counts.get("capped", 0),
        "reponses": counts.get("replied", 0),
        # Engagement: how many of the still-open findings received a human reply.
        "remarques_ouvertes": counts.get("remarques_ouvertes", 0),
        "remarques_avec_reponse": counts.get("remarques_avec_reponse", 0),
    })
    if len(data["runs"]) > MAX_ENTREES:
        data["runs"] = data["runs"][-MAX_ENTREES:]

    try:
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as e:
        if logger:
            logger.info("Statistics not written (%s) — no effect on the review", e)


def resume(chemin: Path | None = None) -> dict:
    """Aggregates ready for a dashboard."""
    runs = _charger(chemin or STATS_FILE)["runs"]
    if not runs:
        return {"runs": 0}

    prs = {r["pr"] for r in runs}
    postes = sum(r.get("postes", 0) for r in runs)
    dedup = sum(r.get("dedupliques", 0) for r in runs)
    cout = sum(r.get("cout_usd", 0) for r in runs)

    par_auteur: dict[str, dict] = {}
    for r in runs:
        a = par_auteur.setdefault(r.get("auteur") or "?", {"runs": 0, "postes": 0, "cout": 0.0})
        a["runs"] += 1
        a["postes"] += r.get("postes", 0)
        a["cout"] += r.get("cout_usd", 0)

    # Engagement counters are a SNAPSHOT of one PR's open findings at run time, not an
    # increment: the PR's last run holds its current state, and PRs add up. (A `max` over
    # every run kept the single busiest PR, whatever the number of PRs.)
    dernier_par_pr: dict = {}
    for r in runs:
        dernier_par_pr[r["pr"]] = r

    return {
        "runs": len(runs),
        "prs": len(prs),
        "remarques_postees": postes,
        "remarques_dedupliquees": dedup,          # what we avoided reposting
        "remarques_sous_seuil": sum(r.get("sous_seuil", 0) for r in runs),
        "reponses_dans_threads": sum(r.get("reponses", 0) for r in runs),
        "remarques_avec_reponse": sum(r.get("remarques_avec_reponse", 0)
                                      for r in dernier_par_pr.values()),
        "remarques_ouvertes": sum(r.get("remarques_ouvertes", 0) for r in dernier_par_pr.values()),
        "cout_total_usd": round(cout, 2),
        "cout_moyen_par_pr": round(cout / len(prs), 3) if prs else 0,
        "remarques_par_pr": round(postes / len(prs), 1) if prs else 0,
        "par_auteur": par_auteur,
    }
