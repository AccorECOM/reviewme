"""Output scrub before posting to GitHub (exfiltration defense — security ADR).

The engine reads UNTRUSTED PR content and can be pushed (prompt injection) into
copying a secret or internal content into a comment posted on a public PR.
So secrets and absolute paths are redacted from any text before publishing.

This is NOT complete protection (the real mitigation is the sandbox with no outbound
network + scoped Read, see doc §Security) — it is the last barrier before posting.
"""
from __future__ import annotations

import re

_SECRET_PATTERNS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),           # GitHub tokens (ghp_, gho_, ghu_, ghs_, ghr_)
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),           # fine-grained PAT
    re.compile(r"glpat-[A-Za-z0-9_\-]{20,}"),              # GitLab tokens
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),             # Anthropic keys
    re.compile(r"sk-proj-[A-Za-z0-9_\-]{20,}"),            # OpenAI project keys
    re.compile(r"sk-[A-Za-z0-9]{32,}"),                     # OpenAI-style keys (legacy)
    re.compile(r"(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}"),  # Stripe keys
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),                 # Google API keys
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),           # Slack tokens
    re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/]+"),  # Slack webhooks
    re.compile(r"AKIA[0-9A-Z]{16}"),                         # AWS access key id
    re.compile(r"aws_secret_access_key\s*[=:]\s*['\"]?[A-Za-z0-9/+]{40}"),  # AWS secret
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),    # PEM private keys
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),  # JWT
    re.compile(r"(?i)\b(?:password|passwd|api[_-]?key|secret|token|bearer)\b\s*[=:]\s*['\"]?[^\s'\"]{8,}"),  # generic key=value
]

_ABS_PATH_PATTERNS = [
    re.compile(r"/Users/[^\s\"'`)]+"),
    re.compile(r"/home/[^\s\"'`)]+"),
    re.compile(r"[A-Za-z]:\\\\[^\s\"'`)]+"),
]

_SECRET_TAG = "[REDACTED-SECRET]"
_PATH_TAG = "[local-path-redacted]"


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
