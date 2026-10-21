"""GitHub client tests: every request carries the token of the active provider.

Regression: `get_pr_diff` and `get_file_at` used to re-inject `config.api_headers`
per request, which overwrote the Authorization header with the (possibly empty)
PAT even in GitHub App mode.

Run: `pytest tests/`.
"""
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reviewme.config import Config
from reviewme.github_auth import TokenProvider
from reviewme.github_client import GitHubClient


def _client(monkeypatch, config, token):
    seen: list[tuple[str, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.headers.get("authorization", ""),
                     request.headers.get("accept", "")))
        return httpx.Response(200, text="diff --git a/x b/x")

    monkeypatch.setattr("reviewme.github_auth.build_provider", lambda cfg: TokenProvider(token))
    gh = GitHubClient(config)
    gh._client = httpx.Client(transport=httpx.MockTransport(handler), headers=config.api_headers)
    return gh, seen


def test_app_mode_sends_installation_token_on_every_request(monkeypatch):
    config = Config(github_token="", github_repo="o/r", confidence_threshold=50, dry_run=True)
    gh, seen = _client(monkeypatch, config, "APP_TOKEN")
    gh.get_pr_diff(1)
    gh.get_file_at("README.md", "abc")
    assert [h for _, h, _ in seen] == ["Bearer APP_TOKEN", "Bearer APP_TOKEN"]
    assert seen[0][2] == "application/vnd.github.diff"
    assert seen[1][2] == "application/vnd.github.raw"


def test_pat_mode_sends_pat(monkeypatch):
    config = Config(github_token="PAT", github_repo="o/r", confidence_threshold=50, dry_run=True)
    gh, seen = _client(monkeypatch, config, "PAT")
    gh.get_pr_diff(1)
    assert seen[0][1] == "Bearer PAT"
