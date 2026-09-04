"""Statistiques agrégées, sans contenu de review.

`data/reviews/` garde le détail d'une review — dont le titre de la PR et le résumé
rédigé par le modèle. C'est utile en local, mais ce n'est pas transportable : sur un
runner éphémère il faut le faire transiter par un cache partagé entre les builds de
l'app, et y mettre du contenu de PR n'a pas de raison d'être.

Ce fichier-ci ne contient que des **compteurs** : combien de reviews, pour quel coût,
combien de remarques postées, dédupliquées, écartées. De quoi alimenter un tableau de
bord et mesurer ce que le bot apporte, sans embarquer une ligne de code ni un titre.

Append-only et idempotent par (PR, commit) : rejouer un build ne fausse pas les totaux.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from .config import DATA_DIR

STATS_FILE = DATA_DIR / "stats.json"
MAX_ENTREES = 5000          # ~1,5 Mo : au-delà, on tronque les plus anciennes


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
    """Ajoute un run. Aucun titre, aucun message, aucun extrait de code."""
    chemin = chemin or STATS_FILE
    data = _charger(chemin)

    cle = f"{pr_number}:{head_sha[:12]}"
    data["runs"] = [r for r in data["runs"] if r.get("cle") != cle]   # idempotent
    data["runs"].append({
        "cle": cle,
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "pr": pr_number,
        "auteur": auteur,              # login GitHub : c'est un identifiant public
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
        "hors_diff": counts.get("out_of_diff", 0),
        "plafonnes": counts.get("capped", 0),
        "reponses": counts.get("replied", 0),
        # Engagement : combien des remarques encore ouvertes ont reçu une réponse humaine.
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
            logger.info("Statistiques non écrites (%s) — sans effet sur la review", e)


def resume(chemin: Path | None = None) -> dict:
    """Agrégats prêts pour un tableau de bord."""
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

    return {
        "runs": len(runs),
        "prs": len(prs),
        "remarques_postees": postes,
        "remarques_dedupliquees": dedup,          # ce qu'on a évité de reposter
        "remarques_sous_seuil": sum(r.get("sous_seuil", 0) for r in runs),
        "reponses_dans_threads": sum(r.get("reponses", 0) for r in runs),
        "remarques_avec_reponse": max((r.get("remarques_avec_reponse", 0) for r in runs), default=0),
        "remarques_ouvertes": max((r.get("remarques_ouvertes", 0) for r in runs), default=0),
        "cout_total_usd": round(cout, 2),
        "cout_moyen_par_pr": round(cout / len(prs), 3) if prs else 0,
        "remarques_par_pr": round(postes / len(prs), 1) if prs else 0,
        "par_auteur": par_auteur,
    }
