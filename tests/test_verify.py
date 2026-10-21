"""Tests de la passe de vérification (`verify.py`) et de ses effets sur le posting.

Ce qui compte :
  - un finding non confirmé n'atteint jamais la PR, mais reste au registre ;
  - une panne du vérificateur ne rend pas le bot muet ;
  - la sortie du vérificateur est une donnée : illisible = aucun verdict, pas un crash.

Exécution : `pytest tests/`.
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
PR = {"number": 7, "head": {"sha": "abcdef1234567890"}, "title": "PR de test"}


def _config(**kw):
    base = dict(github_token="t", github_repo="o/r", confidence_threshold=50, dry_run=True)
    base.update(kw)
    return Config(**base)


def _spec(**kw):
    return ReviewerSpec(id="tech", project="p", system_prompt="persona", **kw)


def _findings():
    return [Finding(path="a.swift", line=10 + i, severity=Severity.IMPORTANT, message=f"m{i}",
                    confidence=90) for i in range(4)]


def test_verdicts_et_statuts():
    fs = _findings() + _findings()[:1]
    texte = json.dumps({"verdicts": [
        {"id": 0, "verdict": "confirmed", "confidence": 90, "citation": "a.swift:10",
         "reason": "vu"},
        {"id": 1, "verdict": "refuted", "confidence": 80, "citation": "", "reason": "géré"},
        {"id": 2, "verdict": "confirmed", "confidence": 70, "citation": ""},
        {"id": "x", "verdict": "confirmed"},
        {"id": 3, "verdict": "unsure", "citation": "a.swift:10"},
        {"id": 0, "verdict": "refuted"},                   # doublon : le premier compte
    ]})
    assert verify.apply_verdicts(fs, f"Voici :\n```json\n{texte}\n```") == 4
    assert [f.verification["status"] for f in fs] == ["confirmed", "refuted", "uncited",
                                                      "invalid", "missing"]


def test_citation_verifiee_en_python():
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "src").mkdir()
        (Path(tmp) / "src" / "A.swift").write_text("a\nb\nc\n")
        assert verify.valid_citation("src/A.swift:3", tmp, set()) == "src/A.swift:3"
        assert verify.valid_citation("`./src/A.swift:2`", tmp, set()) == "src/A.swift:2"
        assert verify.valid_citation("src/A.swift:4", tmp, set()) == ""      # hors fichier
        assert verify.valid_citation("src/Nope.swift:1", tmp, set()) == ""
        assert verify.valid_citation("src/Nope.swift:1", tmp, {"src/Nope.swift"})
        assert verify.valid_citation("../../etc/passwd:1", tmp, set()) == ""
        assert verify.valid_citation("géré par le middleware, vérifié", tmp, set()) == ""
        assert verify.valid_citation("src/A.swift:2 let x = secret", tmp, set()) == ""


def test_plafond_de_tours_ne_vaut_pas_infirmation():
    """Un finding que le vérificateur n'a pas eu le temps de juger passe, non vérifié."""
    fs = _findings()[:2]
    texte = json.dumps({"verdicts": [{"id": 0, "verdict": "confirmed",
                                      "citation": "a.swift:10"}]})
    verify.apply_verdicts(fs, texte, complete=False)
    assert fs[1].verification["status"] == "error"


def test_budget_epuise_par_la_review(monkeypatch):
    def jamais(*a, **kw):
        raise AssertionError("le vérificateur ne doit pas être appelé")
    monkeypatch.setattr("reviewme.reviewer.invoke_cli", jamais)
    fs = _findings()
    bilan = verify.verify_findings(7, "t", "+x", fs, _config(max_budget_usd=1.0), _spec(),
                                   spent_usd=1.2)
    assert bilan["status"] == "no_budget" and bilan["unverified"] == 4


def test_verificateur_recoit_le_contexte_sans_la_persona(monkeypatch):
    vu = {}

    def capture(prompt, config, spec, **kw):
        vu["prompt"], vu["kw"] = prompt, kw
        return json.dumps({"verdicts": []}), {"subtype": "success"}
    monkeypatch.setattr("reviewme.reviewer.invoke_cli", capture)
    verify.verify_findings(7, "t", "+x", _findings(), _config(), _spec(),
                           extra_context="# Ticket PROJ-1\nCA3 : lien de partage")
    assert "CA3 : lien de partage" in vu["prompt"] and "persona" not in vu["prompt"]
    assert vu["kw"]["reviewer_tools"] is False and vu["kw"]["timeout"] == verify.TIMEOUT_S
    assert vu["kw"]["max_turns"] >= 4 * 4


def test_sortie_illisible_ne_pose_aucun_verdict():
    fs = _findings()
    assert verify.apply_verdicts(fs, "je n'ai pas compris") == -1
    assert all(f.verification is None for f in fs)


def test_panne_du_verificateur_laisse_passer(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("CLI tombée")
    monkeypatch.setattr("reviewme.reviewer.invoke_cli", boom)
    fs = _findings()
    bilan = verify.verify_findings(7, "t", "+x", fs, _config(), _spec())
    assert bilan["status"] == "error" and bilan["dropped"] == 0
    assert {f.verification["status"] for f in fs} == {"error"}


def test_prepare_ecarte_le_non_confirme():
    ctx = PrContext(valid={"a.swift": {n: LinePos(n, f"l{n}", added=True)
                                       for n in range(10, 14)}})
    fs = _findings()
    statuts = ["confirmed", "refuted", "missing", "error"]
    for f, s in zip(fs, statuts, strict=True):
        f.verification = {"status": s, "confidence": 80}
    prep = prepare(PR, _config(), ctx, ReviewResult("COMMENT", "s", fs), LOGGER, "tech")
    assert [f.fate for f in fs] == ["posted", "dropped_unverified", "dropped_unverified",
                                    "posted"]
    assert prep.counts["dropped_unverified"] == 2 and len(prep.batch) == 2


def test_mode_simule_infirme_un_sur_deux():
    fs = _findings()
    bilan = verify.verify_findings(7, "t", "+x", fs, _config(fake=True), _spec())
    assert bilan["candidates"] == 4 and bilan["refuted"] == 2


def test_raison_du_verificateur_seulement_avec_le_texte():
    f = _findings()[0]
    f.fate, f.verification = "posted", {"status": "confirmed", "confidence": 9,
                                        "citation": "a:1", "reason": "texte sur la PR"}
    result = ReviewResult("COMMENT", "s", [f])
    (sans,) = finding_rows({}, "tech", result, [f], with_text=False)
    (avec,) = finding_rows({}, "tech", result, [f], with_text=True)
    assert "reason" not in sans["verification"] and avec["verification"]["reason"]


def test_methode_injectee_avant_le_contrat():
    prompt = _build_prompt(7, "t", "/tmp/x.diff", _config(), _spec())
    assert prompt.index("Phase 2 : vérification") < prompt.index("Contrat de sortie")
    assert "evidence" in prompt


def test_global_ne_reprend_pas_la_synthese_si_des_findings_sont_ecartes():
    ctx = PrContext(valid={"a.swift": {n: LinePos(n, f"l{n}", added=True)
                                       for n in range(10, 14)}})
    fs = _findings()[:2]
    fs[0].verification = {"status": "confirmed"}
    fs[1].verification = {"status": "refuted"}
    result = ReviewResult("COMMENT", "m0 et m1 sont deux problèmes", fs)
    prep = prepare(PR, _config(), ctx, result, LOGGER, "us", output_mode="global")
    assert "m1" not in prep.global_body and "m0" in prep.global_body


def test_finding_infirme_n_est_pas_annonce_corrige():
    """Commentaire posté au run 1, finding infirmé au run 2 : pas de faux « corrigé »."""
    from reviewme.reconciler import fingerprint_hash
    ctx = PrContext(valid={"a.swift": {10: LinePos(10, "l10", added=True)}})
    ctx.inline_by_marker[("tech", fingerprint_hash("a.swift", "l10"))] = {
        "id": 5, "position": None}
    f = _findings()[0]
    f.verification = {"status": "refuted"}
    prep = prepare(PR, _config(), ctx, ReviewResult("COMMENT", "s", [f]), LOGGER, "tech")
    assert not any("n'apparaît plus" in msg for _, msg in prep.replies)


def test_commentaire_signale_l_absence_de_verification():
    ctx = PrContext(valid={"a.swift": {10: LinePos(10, "l10", added=True)}})
    f = _findings()[0]
    f.verification = {"status": "error"}
    prep = prepare(PR, _config(), ctx, ReviewResult("COMMENT", "s", [f]), LOGGER, "tech")
    assert "non vérifiée" in prep.batch[0]["body"]


def test_invocation_cli(monkeypatch):
    from reviewme import reviewer
    vu = {}

    class _R:
        stdout = json.dumps({"result": "{}", "num_turns": 3, "subtype": "success"})
        stderr, returncode = "", 0

    def run(cmd, **kw):
        vu["cmd"], vu["kw"] = cmd, kw
        return _R()
    monkeypatch.setattr(reviewer.subprocess, "run", run)
    monkeypatch.setattr(reviewer, "_find_claude", lambda config=None: "claude")
    texte, meta = reviewer.invoke_cli("p", _config(claude_agent="perso"), _spec(),
                                      max_turns=12, budget=0.5, timeout=60,
                                      reviewer_tools=False)
    cmd = vu["cmd"]
    assert cmd[cmd.index("--max-turns") + 1] == "12"
    assert cmd[cmd.index("--max-budget-usd") + 1] == "0.5"
    assert "--agent" not in cmd and vu["kw"]["timeout"] == 60
    assert cmd[cmd.index("--allowedTools") + 1] == reviewer._ALLOWED_TOOLS
    # Ni hooks ni MCP du dépôt relu (celui de la PR).
    assert cmd[cmd.index("--setting-sources") + 1] == "user" and "--strict-mcp-config" in cmd
    assert meta["max_turns"] == 12 and texte == "{}"


def test_silencieux_hors_mode_global_refuse():
    with tempfile.TemporaryDirectory() as tmp:
        rdir = Path(tmp) / "p" / "reviewers" / "tech"
        rdir.mkdir(parents=True)
        (rdir / "system.md").write_text("x")
        (rdir / "reviewer.toml").write_text("silent_when_clean = true\n")
        with pytest.raises(ProjectConfigError):
            load_project("p", Path(tmp))


def test_silencieux_quand_rien_en_mode_global():
    ctx = PrContext(valid={})
    prep = prepare(PR, _config(), ctx, ReviewResult("COMMENT", "RAS", []), LOGGER, "us",
                   output_mode="global", silent_when_clean=True)
    assert prep.global_body is None
    # Sans l'option, le comportement historique est conservé.
    prep = prepare(PR, _config(), ctx, ReviewResult("COMMENT", "RAS", []), LOGGER, "us",
                   output_mode="global")
    assert prep.global_body


def test_reglages_du_reviewer_toml():
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
