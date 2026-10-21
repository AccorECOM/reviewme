"""LLM gateway diagnostics: why does the CLI get a 401, without ever printing a credential.

The engine does not call the model itself: the Claude Code CLI does, with the
`ANTHROPIC_*` variables it inherits. When a gateway answers 401, the CLI only shows the
gateway's message. This module tells what the CLI could have sent, and checks each
credential separately against the gateway, the way `curl -v` would, minus the secrets:

- which variables are set (`ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY`,
  `ANTHROPIC_CUSTOM_HEADERS`, model aliases), each credential reduced to its length, its
  first 3 and last 4 characters, and its SHA-256. LiteLLM-style gateways print the SHA-256
  of the key they received ("Key Hash"): comparing it with the hash below says WHICH
  variable the CLI actually used.
- a probe per credential, with the `ANTHROPIC_CUSTOM_HEADERS` the CLI adds (names logged, never values): one minimal `POST /v1/messages` (`max_tokens: 1`) with
  `Authorization: Bearer <ANTHROPIC_AUTH_TOKEN>` only, then with `x-api-key:
  <ANTHROPIC_API_KEY>` only. The status code of each says which one the gateway accepts.

Used by `reviewme doctor`, and automatically by the reviewer when the CLI fails on an
authentication error.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from urllib.parse import urlparse

from .scrub import scrub_text

_CREDENTIALS = ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")
_MODEL_VARS = ("CLAUDE_MODEL", "ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
               "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL",
               "ANTHROPIC_SMALL_FAST_MODEL")
_PROBE_TIMEOUT_S = 15.0
_DEFAULT_PROBE_MODEL = "claude-sonnet-5-5"


def fingerprint(secret: str) -> str:
    """`len=NN prefix=sk-… suffix=…ab12 sha256=<hex>`: enough to compare, never to use."""
    if not secret:
        return "absent"
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    head = secret[:3] if len(secret) > 12 else "?"
    tail = secret[-4:] if len(secret) > 12 else "?"
    return f"len={len(secret)} prefix={head}… suffix=…{tail} sha256={digest}"


def gateway_host(env: Mapping[str, str]) -> str:
    base = (env.get("ANTHROPIC_BASE_URL") or "").strip()
    if not base:
        return "<not set: api.anthropic.com>"
    return urlparse(base).netloc or base


def describe_env(env: Mapping[str, str] | None = None) -> list[str]:
    """One line per fact. Nothing in them can be replayed against the gateway."""
    env = os.environ if env is None else env
    lines = [f"gateway host: {gateway_host(env)}"]
    for name in _CREDENTIALS:
        lines.append(f"{name}: {fingerprint(env.get(name) or '')}")
    both = all(env.get(n) for n in _CREDENTIALS)
    if both:
        lines.append("both credentials are set: the CLI sends both headers, and the gateway "
                     "picks one (LiteLLM prefers x-api-key on Anthropic routes)")
    lines.append("ANTHROPIC_CUSTOM_HEADERS: "
                 + ("set" if env.get("ANTHROPIC_CUSTOM_HEADERS") else "none"))
    for name in _MODEL_VARS:
        value = env.get(name) or ""
        lines.append(f"{name}: {value or 'unset'}")
    return lines


def custom_headers(env: Mapping[str, str]) -> dict[str, str]:
    """`ANTHROPIC_CUSTOM_HEADERS` as the CLI reads it: one `Name: value` per line."""
    out: dict[str, str] = {}
    for line in (env.get("ANTHROPIC_CUSTOM_HEADERS") or "").splitlines():
        name, sep, value = line.partition(":")
        if sep and name.strip():
            out[name.strip()] = value.strip()
    return out


def _probe_once(base_url: str, headers: dict[str, str], model: str) -> tuple[str, str]:
    """(status, scrubbed excerpt of the body). Status is "ERR <type>" when no HTTP answer."""
    import httpx
    url = base_url.rstrip("/") + "/v1/messages"
    payload = {"model": model, "max_tokens": 1,
               "messages": [{"role": "user", "content": "ping"}]}
    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=_PROBE_TIMEOUT_S)
    except httpx.HTTPError as e:
        return f"ERR {type(e).__name__}", scrub_text(str(e))[:200]
    body = resp.text.strip()
    try:
        data = json.loads(body)
        if isinstance(data, dict) and "error" in data:
            body = json.dumps(data["error"])
    except json.JSONDecodeError:
        pass
    return str(resp.status_code), scrub_text(body)[:300]


def probe(env: Mapping[str, str] | None = None, model: str = "") -> list[str]:
    """One minimal call per credential, each alone, like `curl` would with one header."""
    env = os.environ if env is None else env
    base = (env.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com").strip()
    model = model or env.get("CLAUDE_MODEL") or env.get("ANTHROPIC_MODEL") or _DEFAULT_PROBE_MODEL
    common = {"anthropic-version": "2023-06-01", "content-type": "application/json"}
    custom = custom_headers(env)
    common.update(custom)
    lines = [f"probe: POST {base.rstrip('/')}/v1/messages model={model} max_tokens=1"
             + (f" custom headers: {', '.join(custom)}" if custom else "")]
    token = env.get("ANTHROPIC_AUTH_TOKEN") or ""
    key = env.get("ANTHROPIC_API_KEY") or ""
    if token:
        status, body = _probe_once(base, {**common, "Authorization": f"Bearer {token}"}, model)
        lines.append(f"  Authorization: Bearer <ANTHROPIC_AUTH_TOKEN> -> {status} {body}")
    if key:
        status, body = _probe_once(base, {**common, "x-api-key": key}, model)
        lines.append(f"  x-api-key: <ANTHROPIC_API_KEY> -> {status} {body}")
    if token and key:
        status, body = _probe_once(
            base, {**common, "Authorization": f"Bearer {token}", "x-api-key": key}, model)
        lines.append(f"  both headers (what the CLI sends) -> {status} {body}")
    if not (token or key):
        lines.append("  no credential set: nothing to probe")
    return lines


def looks_like_auth_error(text: str) -> bool:
    low = (text or "").lower()
    return "401" in low or "authenticat" in low or "invalid proxy server token" in low


_KEY_HASH_RE = re.compile(r"key hash[^=:]*[=:]\s*([0-9a-f]{64})", re.IGNORECASE)

BANNER_START = "=" * 72
BANNER_TITLE = "LLM GATEWAY AUTHENTICATION FAILED: running diagnostics (no secret printed)"


def verdict(error_text: str, env: Mapping[str, str] | None = None) -> list[str]:
    """Matches the key hash a LiteLLM-style gateway prints against each credential.

    The gateway says which key it received only as a SHA-256: comparing it with ours tells
    whether the CLI sent the expected variable (then the gateway does not know that token)
    or another one (then the environment is wrong).
    """
    env = os.environ if env is None else env
    m = _KEY_HASH_RE.search(error_text or "")
    if not m:
        return ["verdict: the gateway did not print a key hash; see the probe results above"]
    received = m.group(1).lower()
    for name in _CREDENTIALS:
        value = env.get(name) or ""
        if value and hashlib.sha256(value.encode("utf-8")).hexdigest() == received:
            return [f"verdict: the gateway received {name} (hash match) and rejected it.",
                    f"         The token itself is unknown to the gateway: expired, revoked, or "
                    f"issued for another environment than {gateway_host(env)}."]
    return ["verdict: the key the gateway received matches NONE of ANTHROPIC_AUTH_TOKEN / "
            "ANTHROPIC_API_KEY.",
            "         Something else injects a credential (CLI settings, apiKeyHelper, "
            "ANTHROPIC_CUSTOM_HEADERS)."]


def report(error_text: str = "", model: str = "", env: Mapping[str, str] | None = None,
           *, with_probe: bool = True) -> list[str]:
    """The full block, framed so it stands out in a CI log."""
    lines = [BANNER_START, BANNER_TITLE, BANNER_START]
    lines += describe_env(env)
    if with_probe:
        lines += probe(env, model=model)
    if error_text:
        lines += verdict(error_text, env)
    lines.append(BANNER_START)
    return lines
