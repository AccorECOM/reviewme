"""LLM diagnostics never print a credential, and probe each credential separately.

Run: `pytest tests/`.
"""
import hashlib
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reviewme import llm_diag

TOKEN = "sk-litellm-0123456789abcdefab12"
KEY = "sk-ant-api03-zzzzzzzzzzzzzzzzzzzz9999"


def test_describe_env_fingerprints_without_leaking():
    env = {"ANTHROPIC_BASE_URL": "https://gateway.example/anthropic",
           "ANTHROPIC_AUTH_TOKEN": TOKEN, "ANTHROPIC_API_KEY": KEY, "CLAUDE_MODEL": "m"}
    text = "\n".join(llm_diag.describe_env(env))
    assert TOKEN not in text and KEY not in text
    assert "gateway host: gateway.example" in text
    assert hashlib.sha256(TOKEN.encode()).hexdigest() in text
    assert "suffix=…ab12" in text
    assert "both credentials are set" in text


def test_probe_sends_each_credential_alone(monkeypatch):
    seen = []

    def fake_post(url, json, headers, timeout):
        seen.append((url, "Authorization" in headers, "x-api-key" in headers))
        status = 200 if headers.get("Authorization") == f"Bearer {TOKEN}" else 401
        body = {"error": {"message": f"Invalid proxy server token {KEY}"}} if status == 401 else {}
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    env = {"ANTHROPIC_BASE_URL": "https://gw.example/", "ANTHROPIC_AUTH_TOKEN": TOKEN,
           "ANTHROPIC_API_KEY": KEY}
    lines = llm_diag.probe(env, model="claude-sonnet-5-5")
    assert seen == [("https://gw.example/v1/messages", True, False),
                    ("https://gw.example/v1/messages", False, True),
                    ("https://gw.example/v1/messages", True, True)]
    text = "\n".join(lines)
    assert "Bearer <ANTHROPIC_AUTH_TOKEN> -> 200" in text
    assert "x-api-key: <ANTHROPIC_API_KEY> -> 401" in text
    assert KEY not in text and TOKEN not in text   # the gateway's echo is scrubbed


def test_auth_error_detection():
    assert llm_diag.looks_like_auth_error("Failed to authenticate. API Error: 401 Authentication Error")
    assert not llm_diag.looks_like_auth_error("max turns reached")
