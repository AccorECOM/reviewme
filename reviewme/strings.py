"""Texts the bot posts on pull requests, in the project's language.

Prompts sent to the model are in English; what humans read on GitHub follows the
project's `[review] language` (`project.toml`), English by default. Adding a language
means adding one column here: every key must exist in every language.
"""
from __future__ import annotations

LANGUAGES = {"en": "English", "fr": "French"}
DEFAULT_LANGUAGE = "en"

_TEXTS: dict[str, dict[str, str]] = {
    "signature": {
        "en": "_ReviewMe · automated review_",
        "fr": "_ReviewMe · review automatisée_",
    },
    "review_title": {
        "en": "Tech Lead Review (automated)",
        "fr": "Tech Lead Review (automatisée)",
    },
    "review_title_reviewer": {
        "en": "Automated review — {reviewer}",
        "fr": "Review automatisée — {reviewer}",
    },
    "fallback_title": {
        "en": "Automated review ({reviewer})",
        "fr": "Review automatisée ({reviewer})",
    },
    "moved_code_note": {
        "en": "_(code moved by this PR: it existed before it)_",
        "fr": "_(code déplacé par la PR : il existait déjà avant elle)_",
    },
    "unverified_note": {
        "en": "_(not verified: the verification pass did not complete)_",
        "fr": "_(non vérifiée : la passe de vérification n'a pas abouti)_",
    },
    "suggestion": {"en": "_Suggestion:_", "fr": "_Suggestion :_"},
    "dropped_unverified": {
        "en": "_{n} finding(s) dropped by verification; only confirmed points are listed "
              "below._",
        "fr": "_{n} remarque(s) écartée(s) par la vérification ; seuls les points confirmés "
              "sont repris ci-dessous._",
    },
    "advice_header": {
        "en": "**💡 Review tip** (for a human to check):",
        "fr": "**💡 Conseil pour la relecture** (à valider par un humain) :",
    },
    "minor_header": {"en": "**Minor findings:**", "fr": "**Remarques mineures** :"},
    "moved_header": {
        "en": "**Pre-existing code** (moved or copied by the PR, not introduced by it):",
        "fr": "**Code préexistant** (déplacé ou copié par la PR, pas introduit par elle) :",
    },
    "out_of_diff_header": {
        "en": "**Out-of-diff findings** (lines not changed by the PR):",
        "fr": "**Remarques hors-diff** (lignes non modifiées par la PR) :",
    },
    "degraded_header": {
        "en": "**Comments (inline unavailable):**",
        "fr": "**Commentaires (inline indisponible)** :",
    },
    "still_relevant": {
        "en": "Still relevant on `{sha}` (the code on this line changed). {signature}",
        "fr": "Toujours d'actualité sur `{sha}` (le code de cette ligne a évolué). {signature}",
    },
    "gone": {
        "en": "This point no longer appears on `{sha}` — fixed or gone from the diff. "
              "{signature}",
        "fr": "Ce point n'apparaît plus sur `{sha}` — corrigé ou disparu du diff. {signature}",
    },
    "commit": {"en": "commit", "fr": "commit"},
    # `[review] show_model`: the name the config asked for, then the one the API answered
    # with. They differ behind a gateway alias, hence both.
    "model_line": {
        "en": "model: {configured} (configured) → {reported} (answered)",
        "fr": "modèle : {configured} (configuré) → {reported} (a répondu)",
    },
    "model_same": {
        "en": "model: {model} (configured = answered)",
        "fr": "modèle : {model} (configuré = a répondu)",
    },
    "model_default": {"en": "CLI default", "fr": "défaut du CLI"},
    "model_unknown": {"en": "unknown", "fr": "inconnu"},
    # `[review] show_score`: the reviewer's own confidence, then the verifier's.
    "score": {"en": "score {score}/100", "fr": "score {score}/100"},
    "score_verified": {"en": "verification {score}/100", "fr": "vérification {score}/100"},
}

#: Signatures of every language: replies of the bot are recognised by them, whatever the
#: language it posted in (a project may switch languages).
ALL_SIGNATURES = tuple(v for v in _TEXTS["signature"].values())


def normalize(language: str | None) -> str:
    """Language code, or ValueError for an unknown one (a typo must not go unnoticed)."""
    code = (language or DEFAULT_LANGUAGE).strip().lower()
    if code not in LANGUAGES:
        raise ValueError(f"unknown language {language!r} (available: "
                         f"{', '.join(sorted(LANGUAGES))})")
    return code


def t(key: str, language: str = DEFAULT_LANGUAGE, **values) -> str:
    """Text `key` in `language` (English when the language is unknown)."""
    entry = _TEXTS[key]
    text = entry.get(language) or entry[DEFAULT_LANGUAGE]
    return text.format(**values) if values else text
