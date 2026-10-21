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


def test_probe_adds_custom_headers_by_name_only(monkeypatch):
    seen = {}

    def fake_post(url, json, headers, timeout):
        seen.update(headers)
        return httpx.Response(200, json={}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    env = {"ANTHROPIC_BASE_URL": "https://gw.example", "ANTHROPIC_AUTH_TOKEN": TOKEN,
           "ANTHROPIC_CUSTOM_HEADERS": "X-Gateway-Key: front-door-9876\nX-Tenant: acme"}
    text = "\n".join(llm_diag.probe(env, model="m"))
    assert seen["X-Gateway-Key"] == "front-door-9876" and seen["X-Tenant"] == "acme"
    assert "custom headers: X-Gateway-Key, X-Tenant" in text
    assert "front-door-9876" not in text


def test_auth_error_detection():
    assert llm_diag.looks_like_auth_error("Failed to authenticate. API Error: 401 Authentication Error")
    assert not llm_diag.looks_like_auth_error("max turns reached")


def test_verdict_names_the_variable_the_gateway_received():
    h = hashlib.sha256(TOKEN.encode()).hexdigest()
    err = f"401 Authentication Error, Invalid proxy server token passed. Key Hash (Token) ={h}."
    env = {"ANTHROPIC_BASE_URL": "https://gw.example", "ANTHROPIC_AUTH_TOKEN": TOKEN}
    text = "\n".join(llm_diag.verdict(err, env))
    assert "received ANTHROPIC_AUTH_TOKEN (hash match)" in text and "gw.example" in text


def test_verdict_when_no_credential_matches():
    err = "Key Hash (Token) =" + "0" * 64
    text = "\n".join(llm_diag.verdict(err, {"ANTHROPIC_AUTH_TOKEN": TOKEN}))
    assert "matches NONE" in text


def test_report_is_framed_and_leaks_nothing():
    env = {"ANTHROPIC_AUTH_TOKEN": TOKEN}
    lines = llm_diag.report("401", env=env, with_probe=False)
    assert lines[0] == lines[2] == lines[-1] == llm_diag.BANNER_START
    assert lines[1] == llm_diag.BANNER_TITLE
    assert TOKEN not in "\n".join(lines)


def test_request_path_shows_doubled_v1():
    assert llm_diag.request_path({"ANTHROPIC_BASE_URL": "https://gw.example/llm"}) == "/llm/v1/messages"
    assert llm_diag.request_path({"ANTHROPIC_BASE_URL": "https://gw.example/llm/v1/"}) == "/llm/v1/v1/messages"
