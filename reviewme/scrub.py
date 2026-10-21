"""Scrub de sortie avant post GitHub (défense exfiltration — ADR sécurité).

Le moteur lit du contenu de PR NON FIABLE et peut être poussé (prompt injection) à
recopier un secret ou du contenu interne dans un commentaire posté sur une PR publique.
On caviarde donc les secrets et chemins absolus de tout texte avant publication.

Ce n'est PAS une protection complète (la vraie mitigation est le sandbox sans réseau
sortant + Read scopé, cf. doc §Sécurité) — c'est la dernière barrière avant le post.
"""
from __future__ import annotations

import re

_SECRET_PATTERNS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),           # tokens GitHub (ghp_, gho_, ghu_, ghs_, ghr_)
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),           # PAT fine-grained
    re.compile(r"glpat-[A-Za-z0-9_\-]{20,}"),              # tokens GitLab
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),             # clés Anthropic
    re.compile(r"sk-proj-[A-Za-z0-9_\-]{20,}"),            # clés OpenAI projet
    re.compile(r"sk-[A-Za-z0-9]{32,}"),                     # clés type OpenAI (legacy)
    re.compile(r"(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}"),  # clés Stripe
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),                 # clés Google API
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),           # tokens Slack
    re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/]+"),  # webhooks Slack
    re.compile(r"AKIA[0-9A-Z]{16}"),                         # AWS access key id
    re.compile(r"aws_secret_access_key\s*[=:]\s*['\"]?[A-Za-z0-9/+]{40}"),  # AWS secret
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),    # clés privées PEM
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),  # JWT
    re.compile(r"(?i)\b(?:password|passwd|api[_-]?key|secret|token|bearer)\b\s*[=:]\s*['\"]?[^\s'\"]{8,}"),  # clé=valeur générique
]

_ABS_PATH_PATTERNS = [
    re.compile(r"/Users/[^\s\"'`)]+"),
    re.compile(r"/home/[^\s\"'`)]+"),
    re.compile(r"[A-Za-z]:\\\\[^\s\"'`)]+"),
]

_SECRET_TAG = "[REDACTED-SECRET]"
_PATH_TAG = "[chemin-local-masqué]"


def scrub_text(text: str | None) -> str:
    if not text:
        return ""
    out = text
    for pat in _SECRET_PATTERNS:
        out = pat.sub(_SECRET_TAG, out)
    for pat in _ABS_PATH_PATTERNS:
        out = pat.sub(_PATH_TAG, out)
    return out


def contains_secret(text: str | None) -> bool:
    if not text:
        return False
    return any(pat.search(text) for pat in _SECRET_PATTERNS)
