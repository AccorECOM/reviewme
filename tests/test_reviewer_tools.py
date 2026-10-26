"""Tools allowed for a reviewer: its MCP servers and its plugins' skills, nothing more.

What matters:
  - `mcp__<server>` for each server the reviewer declares itself, `Skill` when it
    installs plugins — otherwise `claude -p` refuses these tools;
  - the verification pass keeps the base list;
  - never `Write` nor `Task`.

Run: `pytest tests/`.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reviewme import reviewer
from reviewme.config import Config
from reviewme.projects import ReviewerSpec

CONTEXT7 = {"mcpServers": {"context7": {"type": "http", "url": "https://mcp.context7.com/mcp"}}}


def _config():
    return Config(github_token="t", github_repo="o/r", dry_run=True)


def _spec(tmp: Path, mcp: dict | str | None = None, **kw):
    if mcp is not None:
        (tmp / "mcp.json").write_text(mcp if isinstance(mcp, str) else json.dumps(mcp))
        kw["mcp_config"] = "mcp.json"
    return ReviewerSpec(id="tech", project="p", system_prompt="persona", directory=tmp, **kw)


def _invoke(monkeypatch, spec, **kw):
    seen = {}

    class _R:
        stdout = json.dumps({"result": "{}", "num_turns": 1, "subtype": "success"})
        stderr, returncode = "", 0

    def run(cmd, **_kw):
        seen["cmd"] = cmd
        return _R()
    monkeypatch.setattr(reviewer.subprocess, "run", run)
    monkeypatch.setattr(reviewer, "_find_claude", lambda config=None: "claude")
    reviewer.invoke_cli("p", _config(), spec, max_turns=5, **kw)
    cmd = seen["cmd"]
    tools = cmd[cmd.index("--allowedTools") + 1].split(",")
    assert "Write" not in tools and "Task" not in tools
    assert "--dangerously-skip-permissions" not in cmd and "--agents" not in cmd
    assert cmd[cmd.index("--setting-sources") + 1] == "user" and "--strict-mcp-config" in cmd
    return cmd, tools


def test_declared_mcp_server_is_allowed(monkeypatch, tmp_path):
    cmd, tools = _invoke(monkeypatch, _spec(tmp_path, CONTEXT7))
    assert "mcp__context7" in tools and "Skill" not in tools
    assert cmd[cmd.index("--mcp-config") + 1] == str((tmp_path / "mcp.json").resolve())


def test_plugins_allow_skill(monkeypatch, tmp_path):
    _, tools = _invoke(monkeypatch, _spec(tmp_path, plugins_install=("mon-plugin",)))
    assert "Skill" in tools
    assert not [t for t in tools if t.startswith("mcp__")]


def test_no_mcp_no_plugins_keeps_base_list(monkeypatch, tmp_path):
    cmd, _ = _invoke(monkeypatch, _spec(tmp_path))
    assert cmd[cmd.index("--allowedTools") + 1] == reviewer._ALLOWED_TOOLS
    assert "--mcp-config" not in cmd


def test_verification_keeps_base_list(monkeypatch, tmp_path):
    spec = _spec(tmp_path, CONTEXT7, plugins_install=("mon-plugin",))
    cmd, tools = _invoke(monkeypatch, spec, reviewer_tools=False)
    assert cmd[cmd.index("--allowedTools") + 1] == reviewer._ALLOWED_TOOLS
    assert "--mcp-config" not in cmd and "Skill" not in tools


def test_unreadable_mcp_file_adds_nothing(monkeypatch, tmp_path):
    for content in ("{not json", json.dumps({"servers": {"x": {}}}), "[]",
                    json.dumps({"mcpServers": ["x"]})):
        cmd, _ = _invoke(monkeypatch, _spec(tmp_path, content))
        assert cmd[cmd.index("--allowedTools") + 1] == reviewer._ALLOWED_TOOLS


def test_server_name_cannot_inject_rules(monkeypatch, tmp_path):
    mcp = {"mcpServers": {"a,Bash(rm:*)": {}, "my server": {}}}
    _, tools = _invoke(monkeypatch, _spec(tmp_path, mcp))
    assert "mcp__a_Bash_rm___" in tools and "mcp__my_server" in tools
    assert not [t for t in tools if t.startswith("Bash(rm")]
