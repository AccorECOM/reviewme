"""Tests for the verification pass (`verify.py`) and its effects on posting.

What matters:
  - an unconfirmed finding never reaches the PR, but stays in the ledger;
  - a verifier failure does not make the bot mute;
  - the verifier output is data: unreadable = no verdict, not a crash.

Run: `pytest tests/`.
"""
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from reviewme import verify
from reviewme.config import Config
from reviewme.diff_utils import LinePos
from reviewme.ledger import finding_rows
from reviewme.models import Finding, ReviewResult, Severity
from reviewme.projects import ProjectConfigError, ReviewerSpec, load_project
from reviewme.reconciler import PrContext, prepare
from reviewme.reviewer import _build_prompt

LOGGER = logging.getLogger("test-verify")
LOGGER.addHandler(logging.NullHandler())
PR = {"number": 7, "head": {"sha": "abcdef1234567890"}, "title": "Test PR"}


def _config(**kw):
    base = dict(github_token="t", github_repo="o/r", confidence_threshold=50, dry_run=True)
    base.update(kw)
    return Config(**base)


def _spec(**kw):
    return ReviewerSpec(id="tech", project="p", system_prompt="persona", **kw)


def _findings():
    return [Finding(path="a.swift", line=10 + i, severity=Severity.IMPORTANT, message=f"m{i}",
                    confidence=90) for i in range(4)]


def test_verdicts_and_statuses():
    fs = _findings() + _findings()[:1]
    text = json.dumps({"verdicts": [
        {"id": 0, "verdict": "confirmed", "confidence": 90, "citation": "a.swift:10",
         "reason": "seen"},
        {"id": 1, "verdict": "refuted", "confidence": 80, "citation": "", "reason": "handled"},
        {"id": 2, "verdict": "confirmed", "confidence": 70, "citation": ""},
        {"id": "x", "verdict": "confirmed"},
        {"id": 3, "verdict": "unsure", "citation": "a.swift:10"},
        {"id": 0, "verdict": "refuted"},                   # duplicate: the first one counts
    ]})
    assert verify.apply_verdicts(fs, f"Here:\n```json\n{text}\n```") == 4
    assert [f.verification["status"] for f in fs] == ["confirmed", "refuted", "uncited",
                                                      "invalid", "missing"]


def test_citation_checked_in_python():
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "src").mkdir()
        (Path(tmp) / "src" / "A.swift").write_text("a\nb\nc\n")
        assert verify.valid_citation("src/A.swift:3", tmp, set()) == "src/A.swift:3"
        assert verify.valid_citation("`./src/A.swift:2`", tmp, set()) == "src/A.swift:2"
        assert verify.valid_citation("src/A.swift:4", tmp, set()) == ""      # outside the file
        assert verify.valid_citation("src/Nope.swift:1", tmp, set()) == ""
        assert verify.valid_citation("src/Nope.swift:1", tmp, {"src/Nope.swift"})
        assert verify.valid_citation("../../etc/passwd:1", tmp, set()) == ""
        assert verify.valid_citation("handled by the middleware, verified", tmp, set()) == ""
        assert verify.valid_citation("src/A.swift:2 let x = secret", tmp, set()) == ""


def test_turn_cap_is_not_refutation():
    """A finding the verifier had no time to judge goes through, unverified."""
    fs = _findings()[:2]
    text = json.dumps({"verdicts": [{"id": 0, "verdict": "confirmed",
                                      "citation": "a.swift:10"}]})
    verify.apply_verdicts(fs, text, complete=False)
    assert fs[1].verification["status"] == "error"


def test_budget_exhausted_by_review(monkeypatch):
    def never(*a, **kw):
        raise AssertionError("the verifier must not be called")
    monkeypatch.setattr("reviewme.reviewer.invoke_cli", never)
    fs = _findings()
    outcome = verify.verify_findings(7, "t", "+x", fs, _config(max_budget_usd=1.0), _spec(),
                                   spent_usd=1.2)
    assert outcome["status"] == "no_budget" and outcome["unverified"] == 4


def test_verifier_gets_context_without_persona(monkeypatch):
    seen = {}

    def capture(prompt, config, spec, **kw):
        seen["prompt"], seen["kw"] = prompt, kw
        return json.dumps({"verdicts": []}), {"subtype": "success"}
    monkeypatch.setattr("reviewme.reviewer.invoke_cli", capture)
    verify.verify_findings(7, "t", "+x", _findings(), _config(), _spec(),
                           extra_context="# Ticket PROJ-1\nAC3: share link")
    assert "AC3: share link" in seen["prompt"] and "persona" not in seen["prompt"]
    assert seen["kw"]["reviewer_tools"] is False and seen["kw"]["timeout"] == verify.TIMEOUT_S
    assert seen["kw"]["max_turns"] >= 4 * 4


def test_unreadable_output_sets_no_verdict():
    fs = _findings()
    assert verify.apply_verdicts(fs, "I did not understand") == -1
    assert all(f.verification is None for f in fs)


def test_verifier_failure_lets_through(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("CLI down")
    monkeypatch.setattr("reviewme.reviewer.invoke_cli", boom)
    fs = _findings()
    outcome = verify.verify_findings(7, "t", "+x", fs, _config(), _spec())
    assert outcome["status"] == "error" and outcome["dropped"] == 0
    assert {f.verification["status"] for f in fs} == {"error"}


def test_prepare_drops_unconfirmed():
    ctx = PrContext(valid={"a.swift": {n: LinePos(n, f"l{n}", added=True)
                                       for n in range(10, 14)}})
    fs = _findings()
    statuses = ["confirmed", "refuted", "missing", "error"]
    for f, s in zip(fs, statuses, strict=True):
        f.verification = {"status": s, "confidence": 80}
    prep = prepare(PR, _config(), ctx, ReviewResult("COMMENT", "s", fs), LOGGER, "tech")
    assert [f.fate for f in fs] == ["posted", "dropped_unverified", "dropped_unverified",
                                    "posted"]
    assert prep.counts["dropped_unverified"] == 2 and len(prep.batch) == 2


def test_simulated_mode_refutes_one_in_two():
    fs = _findings()
    outcome = verify.verify_findings(7, "t", "+x", fs, _config(fake=True), _spec())
    assert outcome["candidates"] == 4 and outcome["refuted"] == 2


def test_verifier_reason_only_with_text():
    f = _findings()[0]
    f.fate, f.verification = "posted", {"status": "confirmed", "confidence": 9,
                                        "citation": "a:1", "reason": "text on the PR"}
    result = ReviewResult("COMMENT", "s", [f])
    (without,) = finding_rows({}, "tech", result, [f], with_text=False)
    (with_,) = finding_rows({}, "tech", result, [f], with_text=True)
    assert "reason" not in without["verification"] and with_["verification"]["reason"]


def test_method_injected_before_contract():
    prompt = _build_prompt(7, "t", "/tmp/x.diff", _config(), _spec())
    assert prompt.index("Step 3: verify") < prompt.index("Output contract")
    assert "evidence" in prompt


def test_global_skips_synthesis_when_findings_dropped():
    ctx = PrContext(valid={"a.swift": {n: LinePos(n, f"l{n}", added=True)
                                       for n in range(10, 14)}})
    fs = _findings()[:2]
    fs[0].verification = {"status": "confirmed"}
    fs[1].verification = {"status": "refuted"}
    result = ReviewResult("COMMENT", "m0 and m1 are two problems", fs)
    prep = prepare(PR, _config(), ctx, result, LOGGER, "us", output_mode="global")
    assert "m1" not in prep.global_body and "m0" in prep.global_body


def test_refuted_finding_not_announced_fixed():
    """Comment posted in run 1, finding refuted in run 2: no false "fixed"."""
    from reviewme.reconciler import fingerprint_hash
    ctx = PrContext(valid={"a.swift": {10: LinePos(10, "l10", added=True)}})
    ctx.inline_by_marker[("tech", fingerprint_hash("a.swift", "l10"))] = {
        "id": 5, "position": None}
    f = _findings()[0]
    f.verification = {"status": "refuted"}
    prep = prepare(PR, _config(), ctx, ReviewResult("COMMENT", "s", [f]), LOGGER, "tech")
    assert not any("no longer appears" in msg for _, msg in prep.replies)


def test_comment_flags_missing_verification():
    ctx = PrContext(valid={"a.swift": {10: LinePos(10, "l10", added=True)}})
    f = _findings()[0]
    f.verification = {"status": "error"}
    prep = prepare(PR, _config(), ctx, ReviewResult("COMMENT", "s", [f]), LOGGER, "tech")
    assert "not verified" in prep.batch[0]["body"]


def test_cli_invocation(monkeypatch):
    from reviewme import reviewer
    seen = {}

    class _R:
        stdout = json.dumps({"result": "{}", "num_turns": 3, "subtype": "success"})
        stderr, returncode = "", 0

    def run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return _R()
    monkeypatch.setattr(reviewer.subprocess, "run", run)
    monkeypatch.setattr(reviewer, "_find_claude", lambda config=None: "claude")
    text, meta = reviewer.invoke_cli("p", _config(claude_agent="custom"), _spec(),
                                      max_turns=12, budget=0.5, timeout=60,
                                      reviewer_tools=False)
    cmd = seen["cmd"]
    assert cmd[cmd.index("--max-turns") + 1] == "12"
    assert cmd[cmd.index("--max-budget-usd") + 1] == "0.5"
    assert "--agent" not in cmd and seen["kw"]["timeout"] == 60
    assert cmd[cmd.index("--allowedTools") + 1] == reviewer._ALLOWED_TOOLS
    # Neither hooks nor MCP from the reviewed repo (the PR's one).
    assert cmd[cmd.index("--setting-sources") + 1] == "user" and "--strict-mcp-config" in cmd
    assert meta["max_turns"] == 12 and text == "{}"


def test_silent_outside_global_mode_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        rdir = Path(tmp) / "p" / "reviewers" / "tech"
        rdir.mkdir(parents=True)
        (rdir / "system.md").write_text("x")
        (rdir / "reviewer.toml").write_text("silent_when_clean = true\n")
        with pytest.raises(ProjectConfigError):
            load_project("p", Path(tmp))


def test_silent_when_nothing_in_global_mode():
    ctx = PrContext(valid={})
    # Silence is the reviewer's call: an empty `summary`.
    prep = prepare(PR, _config(), ctx, ReviewResult("COMMENT", "", []), LOGGER, "us",
                   output_mode="global", silent_when_clean=True)
    assert prep.global_body is None
    # A summary-only reviewer (e.g. `us` with an uncovered criterion) still posts.
    prep = prepare(PR, _config(), ctx, ReviewResult("COMMENT", "1/2 criteria covered", []),
                   LOGGER, "us", output_mode="global", silent_when_clean=True)
    assert prep.global_body and "1/2 criteria covered" in prep.global_body
    # Without the option, the historical behavior is kept.
    prep = prepare(PR, _config(), ctx, ReviewResult("COMMENT", "Nothing to report", []), LOGGER, "us",
                   output_mode="global")
    assert prep.global_body


def test_reviewer_toml_settings():
    with tempfile.TemporaryDirectory() as tmp:
        rdir = Path(tmp) / "p" / "reviewers" / "tech"
        rdir.mkdir(parents=True)
        (rdir / "system.md").write_text("x")
        (rdir / "reviewer.toml").write_text(
            'max_turns = 60\noutput_mode = "global"\nsilent_when_clean = true\n'
            '[verification]\nenabled = false\nmodel = "m"\nmax_turns = 8\n')
        spec = load_project("p", Path(tmp)).reviewers[0]
        assert (spec.max_turns, spec.verify, spec.verify_model, spec.verify_max_turns,
                spec.silent_when_clean) == (60, False, "m", 8, True)
        (rdir / "reviewer.toml").write_text("max_turns = 0\n")
        with pytest.raises(ProjectConfigError):
            load_project("p", Path(tmp))
