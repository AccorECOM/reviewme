"""Extraction of developer replies, to turn them into attributed rules.

The bot remembers nothing from one PR to the next: a point rejected yesterday comes back
tomorrow. This module does not learn it on its own — it **prepares** the work and leaves
the decision to a human.

The key point is attribution. A rule absorbed silently makes the bot progressively blind
without anyone knowing why. A rule that carries its author, its PR and its date stays
open to debate: another developer can challenge it, and the disagreement becomes visible
instead of vanishing into a prompt.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from .reconciler import _MARKER_RE, marker_key

MAX_CAR = 500


def _nettoyer(texte: str) -> str:
    return " ".join(_MARKER_RE.sub("", texte or "").split())[:MAX_CAR]


def collecter(gh, pr_number: int, logger: logging.Logger) -> list[dict]:
    """The bot's findings that a human replied to, on this PR."""
    try:
        commentaires = gh.list_review_comments(pr_number)
    except Exception as e:
        logger.warning("PR #%s: comments unreadable (%s)", pr_number, type(e).__name__)
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
    """Markdown block ready to paste into `common/team-rules.md`.

    Each rule carries its source. Nothing is decided here: it is a proposal that a
    human keeps, rewords or discards.
    """
    if not echanges:
        return ""
    date = datetime.now(UTC).strftime("%Y-%m-%d")
    lignes = [f"<!-- Proposed from PR #{pr_number}, on {date}. "
              f"Review before keeping: a one-off reply is not a rule. -->"]
    for e in echanges:
        lignes.append("")
        lignes.append(f"- **{e['fichier']}** — reviewer `{e['reviewer']}` said: "
                      f"\"{e['remarque'][:180]}\"")
        lignes.append(f"  **{e['auteur']}** replied (PR #{pr_number}, {date}): "
                      f"\"{e['reponse'][:180]}\"")
    return "\n".join(lignes)
