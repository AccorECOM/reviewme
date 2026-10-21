"""Output scrub before posting to GitHub (exfiltration defense).

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
    # PEM private keys: the whole block, or the header plus the base64 body when END is missing
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----(?:\s*[A-Za-z0-9+/=]{16,})*"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),  # JWT
    re.compile(r"(?i)\bbearer\s+(?=[A-Za-z0-9_\-.]*\d)[A-Za-z0-9_\-.]{16,}"),  # "Bearer <token>" headers
    # X-Api-Key: … headers (before the generic pattern, so the whole header name goes)
    re.compile(r"(?i)(?<![A-Za-z0-9_-])x-[a-z0-9-]*(?:key|token|secret)\s*:\s*['\"]?(?=[^\s'\"]*\d)[A-Za-z0-9_\-./+]{8,}"),
    # generic key=value (word-bounded name)
    re.compile(r"(?i)\b(?:password|passwd|api[_-]?key|secret|token|bearer)\b\s*[=:]\s*['\"]?[^\s'\"]{8,}"),
    # UPPER_CASE env-style names (ANTHROPIC_AUTH_TOKEN=, CLIENT_SECRET:) and X-…-Key headers;
    # the value must look like a credential: no call, no attribute access, at least one digit
    re.compile(r"(?<![A-Za-z0-9_])[A-Z0-9]+(?:_[A-Z0-9]+)*_(?:TOKEN|SECRET|API_KEY|APIKEY|PASSWORD|PASSWD)"
               r"\s*[=:]\s*['\"]?(?=[^\s'\"]*\d)[A-Za-z0-9_\-./+]{8,}"),
]

# A path only starts at a token boundary: `src/private/x.swift` or `https://…/opt/` are not
# local paths.
_ABS_PATH_PATTERNS = [
    re.compile(r"(?<![\w.~/\-])/Users/[^\s\"'`)]+"),
    re.compile(r"(?<![\w.~/\-])/home/[^\s\"'`)]+"),
    re.compile(r"(?<![\w.~/\-])/(?:tmp|var|root|private|opt|srv)/[^\s\"'`)]+"),
    re.compile(r"(?<!\w)[A-Za-z]:\\(?=[A-Za-z])[^\s\"'`)]*\\[^\s\"'`)]+"),  # C:\Users\x\y (two separators)
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
