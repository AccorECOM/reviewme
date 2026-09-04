"""Extraction des retours de développeurs, pour en faire des règles attribuées.

Le bot ne mémorise rien d'une PR à l'autre : un point rejeté hier revient demain. Ce
module ne l'apprend pas tout seul — il **prépare** le travail et laisse la décision à un
humain.

Le point clé est l'attribution. Une règle absorbée en silence rend le bot progressivement
aveugle sans que personne ne sache pourquoi. Une règle qui porte son auteur, sa PR et sa
date reste discutable : un autre développeur peut la contester, et le désaccord devient
visible au lieu de disparaître dans un prompt.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from .reconciler import _MARKER_RE, marker_key

MAX_CAR = 500


def _nettoyer(texte: str) -> str:
    return " ".join(_MARKER_RE.sub("", texte or "").split())[:MAX_CAR]


def collecter(gh, pr_number: int, logger: logging.Logger) -> list[dict]:
    """Les remarques du bot auxquelles un humain a répondu, sur cette PR."""
    try:
        commentaires = gh.list_review_comments(pr_number)
    except Exception as e:
        logger.warning("PR #%s : commentaires illisibles (%s)", pr_number, type(e).__name__)
        return []

    racines = {c["id"]: c for c in commentaires
               if c.get("in_reply_to_id") is None and marker_key(c.get("body", ""))}
    echanges: list[dict] = []
    for c in commentaires:
        parent = racines.get(c.get("in_reply_to_id"))
        if not parent:
            continue
        echanges.append({
            "reviewer": (marker_key(parent.get("body", "")) or ("?",))[0],
            "fichier": parent.get("path", "?"),
            "ligne": parent.get("line") or parent.get("original_line") or "?",
            "remarque": _nettoyer(parent.get("body", "")),
            "auteur": (c.get("user") or {}).get("login", "?"),
            "reponse": _nettoyer(c.get("body", "")),
        })
    return echanges


def en_regles(echanges: list[dict], pr_number: int) -> str:
    """Bloc markdown prêt à coller dans `common/regles-terrain.md`.

    Chaque règle porte sa source. Rien n'est décidé ici : c'est une proposition qu'un
    humain garde, reformule ou jette.
    """
    if not echanges:
        return ""
    date = datetime.now(UTC).strftime("%Y-%m-%d")
    lignes = [f"<!-- Proposé depuis la PR #{pr_number}, le {date}. "
              f"À relire avant de garder : une réponse ponctuelle n'est pas une règle. -->"]
    for e in echanges:
        lignes.append("")
        lignes.append(f"- **{e['fichier']}** — le reviewer `{e['reviewer']}` disait : "
                      f"« {e['remarque'][:180]} »")
        lignes.append(f"  **{e['auteur']}** a répondu (PR #{pr_number}, {date}) : "
                      f"« {e['reponse'][:180]} »")
    return "\n".join(lignes)
