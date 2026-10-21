"""The precheck subprocess never receives the job's credentials.

Run: `pytest tests/`.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reviewme.precheck import _minimal_env


def test_credentials_are_not_passed_to_prechecks(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.setenv("JIRA_API_TOKEN", "j")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = _minimal_env()
    assert "GITHUB_TOKEN" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert "JIRA_API_TOKEN" not in env
    assert env["PATH"] == "/usr/bin"
