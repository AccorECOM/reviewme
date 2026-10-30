"""Dashboard texts. Same pattern as `export._R`: one dict per language, same keys.

Shared words (columns, empty states) live here; each section keeps its own titles next to
its code (`_T` in the section module) and merges them with `texts`. A missing language
falls back to English rather than failing the page.
"""
from __future__ import annotations

DEFAULT = "en"

COMMON: dict[str, dict[str, str]] = {
    "en": {
        "no_data": "No data in this window yet.",
        "per_week": "per week", "per_sprint": "per sprint",
        "marker": "settings changed (config_hash)",
        "marker_note": "Dashed line: the settings changed (new config_hash) in that period.",
        "reviewer": "Reviewer", "severity": "Severity", "rule": "Rule", "dev": "Dev",
        "model": "Model", "period": "Period", "pr": "PR", "runs": "Runs", "prs": "PRs",
        "posted": "Posted", "followed": "Acted upon", "approved": "Approved",
        "contested": "Contested", "cost": "Cost", "count": "Count", "share": "Share",
        "findings": "Findings", "rate": "Rate",
        # `reviewme triage` causes (`triage.CAUSES`), shared by quality and tuning.
        "cause_false_positive": "false positive", "cause_missing_context": "missing context",
        "cause_preexisting": "pre-existing", "cause_duplicate": "duplicate",
        "cause_unimportant": "not important", "cause_fixed": "fixed",
        "cause_question": "question", "cause_other": "other",
    },
    "fr": {
        "no_data": "Pas encore de données sur cette fenêtre.",
        "per_week": "par semaine", "per_sprint": "par sprint",
        "marker": "réglages modifiés (config_hash)",
        "marker_note": "Ligne pointillée : les réglages ont changé (nouveau config_hash) "
                       "sur cette période.",
        "reviewer": "Relecteur", "severity": "Sévérité", "rule": "Règle", "dev": "Dev",
        "model": "Modèle", "period": "Période", "pr": "PR", "runs": "Passages",
        "prs": "PR", "posted": "Postées", "followed": "Suivies d'effet",
        "approved": "Approuvées", "contested": "Contestées", "cost": "Coût",
        "count": "Nombre", "share": "Part", "findings": "Remarques", "rate": "Taux",
        "cause_false_positive": "faux positif", "cause_missing_context": "contexte manquant",
        "cause_preexisting": "préexistant", "cause_duplicate": "doublon",
        "cause_unimportant": "pas important", "cause_fixed": "corrigé",
        "cause_question": "question", "cause_other": "autre",
    },
}


def texts(local: dict[str, dict[str, str]], language: str) -> dict[str, str]:
    lang = language if language in COMMON else DEFAULT
    return {**COMMON[lang], **local.get(lang, local[DEFAULT])}


def title(titles: dict[str, str], language: str) -> str:
    return titles.get(language) or titles[DEFAULT]


def plural(T: dict[str, str], key: str, n: int, **values) -> str:
    """`T[key + "_one"]` for exactly one, else `T[key]`: "1 review", "2 reviews"."""
    text = T.get(f"{key}_one") if n == 1 else None
    return (text or T[key]).format(n=n, **values)
