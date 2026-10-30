"""`[review] show_model` and `[review] show_score`: what the PR shows of the model and score.

What matters: both model names appear (a gateway alias says nothing on its own), the
score is the ledger's `confidence`, and nothing changes when the switches are off.

Run: `pytest tests/`.
"""
import logging
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reviewme.config import Config
from reviewme.diff_utils import LinePos
from reviewme.ledger import finding_rows, run_row
from reviewme.models import Finding, ReviewResult, Severity
from reviewme.projects import ProjectConfigError, ReviewerSpec, load_project, resolve_project
from reviewme.reconciler import PrContext, model_line, prepare
from reviewme.reviewer import configured_model, main_model

LOGGER = logging.getLogger("test-model-score")
LOGGER.addHandler(logging.NullHandler())
PR = {"number": 7, "head": {"sha": "abcdef1234567890"}, "title": "Test PR"}
META = {"model": "claude-haiku-4-5, claude-opus-5-5", "model_reported": "claude-opus-5-5",
        "model_configured": "SuperLLM"}


def _config(**kw):
    base = dict(github_token="t", github_repo="o/r", confidence_threshold=50, dry_run=True)
    base.update(kw)
    return Config(**base)


def _ctx():
    return PrContext(valid={"a.swift": {n: LinePos(n, f"l{n}", added=True)
                                       for n in range(10, 14)}})


def _f(line, sev, confidence=85, **kw):
    return Finding(path="a.swift", line=line, severity=sev, message=f"m{line}",
                   confidence=confidence, **kw)


def _prepare(findings, meta=None, **kw):
    result = ReviewResult("COMMENT", "", findings, metadata=dict(meta or META))
    return prepare(PR, _config(), _ctx(), result, LOGGER, "tech", **kw)


# --------------------------------------------------------------------------- model

def test_main_model_is_the_costliest():
    usage = {"claude-haiku-4-5": {"costUSD": 0.001, "outputTokens": 40},
             "claude-opus-5-5": {"costUSD": 0.42, "outputTokens": 3000}}
    assert main_model(usage) == "claude-opus-5-5"
    assert main_model({}) == "" and main_model(None) == ""


def test_configured_model_order(monkeypatch):
    spec = ReviewerSpec(id="tech", project="p", system_prompt="")
    monkeypatch.delenv("CLAUDE_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    assert configured_model(spec) == ""
    monkeypatch.setenv("ANTHROPIC_MODEL", "SuperLLM")
    assert configured_model(spec) == "SuperLLM"
    monkeypatch.setenv("CLAUDE_MODEL", "claude-sonnet-5-5")
    assert configured_model(spec) == "claude-sonnet-5-5"
    spec = ReviewerSpec(id="tech", project="p", system_prompt="", model="claude-opus-5-5")
    assert configured_model(spec) == "claude-opus-5-5"


def test_model_line_shows_alias_and_answer():
    assert model_line(META) == ("model: `SuperLLM` (configured) → `claude-opus-5-5` "
                                "(answered)")
    assert model_line(META, "fr") == ("modèle : `SuperLLM` (configuré) → "
                                      "`claude-opus-5-5` (a répondu)")
    same = {**META, "model_configured": "claude-opus-5-5"}
    assert model_line(same) == "model: `claude-opus-5-5` (configured = answered)"
    assert model_line({"model": "?"}) == "model: CLI default (configured) → unknown (answered)"
    # Simulated run: no `model_reported`, `model` is used.
    assert "`simulated` (answered)" in model_line({"model": "simulated"})
    # A name never breaks the Markdown code span.
    assert "`Super`LLM`" not in model_line({**META, "model_configured": "Super`LLM"})


def test_inline_comment_carries_model_and_score():
    prep = _prepare([_f(10, Severity.BLOCKER)], show_model=True, show_score=True)
    body = prep.batch[0]["body"]
    assert ("<sub>score 85/100 · model: `SuperLLM` (configured) → `claude-opus-5-5` "
            "(answered)</sub>") in body
    # The marker stays the last line: dedup reads it.
    assert body.rstrip().endswith("-->")


def test_off_by_default():
    prep = _prepare([_f(10, Severity.BLOCKER), _f(11, Severity.MINOR)])
    assert "<sub>" not in prep.batch[0]["body"]
    assert "<sub>" not in prep.summary and "score" not in prep.summary


def test_model_under_the_section_title():
    prep = _prepare([_f(11, Severity.MINOR)], show_model=True, prefix="[TECH]")
    lines = prep.summary.split("\n")
    assert lines[0].startswith("## ")
    assert lines[1].startswith("<sub>model: `SuperLLM`")


def test_empty_section_stays_empty_with_model():
    # Inline findings only, no summary: no section, the model is on each comment.
    prep = _prepare([_f(10, Severity.BLOCKER)], show_model=True)
    assert prep.summary == ""


def test_global_mode_and_fallback_name_the_model():
    prep = _prepare([_f(10, Severity.BLOCKER)], show_model=True, show_score=True,
                    output_mode="global")
    assert "`claude-opus-5-5` (answered)" in prep.global_body
    assert "m10 <sub>score 85/100</sub>" in prep.global_body

    broken = ReviewResult("COMMENT", "", [], raw="not json", parsed_ok=False,
                          metadata=dict(META))
    prep = prepare(PR, _config(), _ctx(), broken, LOGGER, "tech", show_model=True)
    assert prep.global_body.split("\n")[1].startswith("<sub>model: `SuperLLM`")


# --------------------------------------------------------------------------- score

def test_score_adds_the_verifier_when_it_confirmed():
    f = _f(10, Severity.BLOCKER, confidence=72,
           verification={"status": "confirmed", "confidence": 90, "citation": "a.swift:10"})
    body = _prepare([f], show_score=True).batch[0]["body"]
    assert "<sub>score 72/100 · verification 90/100</sub>" in body

    f = _f(10, Severity.BLOCKER, verification={"status": "error", "confidence": None})
    body = _prepare([f], show_score=True, language="fr").batch[0]["body"]
    assert "<sub>score 85/100</sub>" in body and "vérification" not in body.split("<sub>")[1]


def test_score_on_summary_lines():
    prep = _prepare([_f(11, Severity.MINOR, confidence=60)], show_score=True)
    assert "m11 <sub>score 60/100</sub>" in prep.summary


def test_score_is_the_ledger_confidence():
    f = _f(10, Severity.BLOCKER, confidence=77)
    result = ReviewResult("COMMENT", "", [f], metadata=dict(META))
    row = finding_rows({}, "tech", result, [f], with_text=False)[0]
    assert row["confidence"] == 77 and row["model_configured"] == "SuperLLM"
    run = run_row({}, "tech", result, threshold=50, diff_files=1, diff_lines=1, fates={})
    assert run["model_configured"] == "SuperLLM"
    assert run["model_reported"] == "claude-opus-5-5"


# --------------------------------------------------------------------------- config

def _project(toml: str):
    tmp = tempfile.mkdtemp()
    pdir = Path(tmp) / "shop"
    pdir.mkdir()
    (pdir / "project.toml").write_text(f'[reviewers]\ncore = ["tech"]\n{toml}')
    return load_project("shop", Path(tmp))


def test_project_toml_switches(monkeypatch):
    monkeypatch.delenv("REVIEWME_SHOW_MODEL", raising=False)
    monkeypatch.delenv("REVIEWME_SHOW_SCORE", raising=False)
    p = _project("")
    assert (p.show_model, p.show_score) == (False, False)
    p = _project("[review]\nshow_model = true\nshow_score = true\n")
    assert (p.show_model, p.show_score) == (True, True)
    with pytest.raises(ProjectConfigError, match="show_score"):
        _project('[review]\nshow_score = "false"\n')


def test_env_fallback_and_toml_precedence(monkeypatch):
    monkeypatch.setenv("REVIEWME_SHOW_MODEL", "1")
    monkeypatch.setenv("REVIEWME_SHOW_SCORE", "true")
    assert _project("").show_model is True
    assert _project("[review]\nshow_model = false\n").show_model is False
    simple = resolve_project(_config())
    assert (simple.show_model, simple.show_score) == (True, True)
