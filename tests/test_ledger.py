"""Tests du registre (`ledger.py`) et du sort des findings dans `prepare` / `post_all`.

Ce qui compte : chaque finding produit par le modèle laisse UNE trace, posté ou non, avec
le bon sort. Un finding qui disparaît sans trace (sous le seuil, hors diff, plafonné) est
exactement ce qu'on ne peut plus mesurer ensuite.

Exécution : `pytest tests/`.
"""
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reviewme import ledger
from reviewme.config import Config
from reviewme.diff_utils import LinePos
from reviewme.models import Finding, ReviewResult, Severity, parse_review_output
from reviewme.projects import ProjectConfig, ReviewerSpec, load_project
from reviewme.reconciler import PrContext, fingerprint_hash, post_all, prepare

LOGGER = logging.getLogger("test-ledger")
LOGGER.addHandler(logging.NullHandler())
PR = {"number": 7, "head": {"sha": "abcdef1234567890"}, "base": {"sha": "0123456789ab"},
      "title": "PR de test"}
DIFF = "+++ b/a.swift\n@@ -1 +1,2 @@\n+let x = y!\n+let z = 1\n"


def _config(**kw):
    base = dict(github_token="t", github_repo="o/r", confidence_threshold=70, dry_run=True)
    base.update(kw)
    return Config(**base)


def _ctx():
    return PrContext(valid={"a.swift": {
        10: LinePos(10, "let x = y!", added=True),
        11: LinePos(11, "let z = 1", added=True),
    }})


def _f(line, conf, sev=Severity.IMPORTANT, path="a.swift", snippet=""):
    return Finding(path=path, line=line, severity=sev, message=f"m{line}", snippet=snippet,
                   confidence=conf, rule_id="r", severity_raw=sev.value)


def _spec(rid="tech"):
    return ReviewerSpec(id=rid, project="p", system_prompt="x")


def _project(directory=None, include_text=False):
    return ProjectConfig(name="p", directory=directory, reviewers=(_spec(),),
                         ledger_include_text=include_text)


class _FakeGH:
    """Client GitHub minimal : enregistre les écritures, renvoie des ids."""

    def __init__(self):
        self.posted = []

    def create_review(self, pr_number, commit_id, body, comments, event="COMMENT"):
        self.posted = comments
        return {"id": 999}

    def list_review_comments_of_review(self, pr_number, review_id):
        return [{"id": 100 + i, "body": c["body"]} for i, c in enumerate(self.posted)]

    def post_issue_comment(self, pr_number, body):
        return {"id": 555}


# --------------------------------------------------------------- sorts dans prepare

def test_chaque_finding_recoit_un_sort():
    findings = [_f(10, 95), _f(11, 40, snippet="let z = 1"), _f(300, 90, path="b.swift")]
    result = ReviewResult(status="COMMENT", summary="s", findings=findings)
    prep = prepare(PR, _config(), _ctx(), result, LOGGER, "tech")
    assert [f.fate for f in prep.findings] == ["posted", "dropped_low", "out_of_diff"]
    assert prep.findings[2].in_summary is True


def test_sous_le_seuil_reste_ancre_sur_la_ligne():
    """Sans ancrage, on ne peut pas croiser avec un commentaire humain sur la même ligne."""
    f = _f(11, 30, snippet="let z = 1")
    prepare(PR, _config(), _ctx(), ReviewResult("COMMENT", "s", [f]), LOGGER, "tech")
    assert f.fate == "dropped_low" and f.line_content == "let z = 1"


def test_deja_commente_est_deduplique_avec_son_commentaire():
    ctx = _ctx()
    ctx.inline_by_marker[("tech", fingerprint_hash("a.swift", "let x = y!"))] = {
        "id": 42, "position": 3}
    f = _f(10, 95)
    prepare(PR, _config(), ctx, ReviewResult("COMMENT", "s", [f]), LOGGER, "tech")
    assert f.fate == "deduped" and f.comment_id == 42


def test_plafond_marque_capped_et_post_rattache_les_ids():
    findings = [_f(10, 95, Severity.BLOCKER), _f(11, 80, Severity.MINOR)]
    result = ReviewResult("COMMENT", "s", findings)
    prep = prepare(PR, _config(dry_run=False, max_comments_per_pr=1), _ctx(), result,
                   LOGGER, "tech")
    gh = _FakeGH()
    post_all(PR, _config(dry_run=False, max_comments_per_pr=1), gh, [prep], LOGGER)
    assert findings[0].fate == "posted" and findings[0].comment_id == 100
    assert findings[1].fate == "capped" and findings[1].comment_id is None


def test_parsing_compte_les_findings_invalides():
    r = parse_review_output(json.dumps({"summary": "s", "findings": [
        {"path": "a", "line": 1, "severity": "warning", "confidence": 80},
        {"path": "", "line": 3}, {"line": "x"}, "pas un objet"]}))
    assert len(r.findings) == 1 and r.invalid == 3
    assert r.findings[0].severity_raw == "warning"


# --------------------------------------------------------------- écriture du registre

def _record(tmp, project=None, **cfg):
    findings = [_f(10, 95), _f(11, 40, snippet="let z = 1")]
    result = ReviewResult("COMMENT", "s", findings, invalid=1,
                          metadata={"model": "claude-x", "cost_usd": 0.5, "total_turns": 12,
                                    "max_turns": 30, "subtype": "success",
                                    "context_files": [{"path": "AGENTS.md", "bytes": 10,
                                                       "truncated": False,
                                                       "source": "context.read"}]})
    config = _config(**cfg)
    prep = prepare(PR, config, _ctx(), result, LOGGER, "tech")
    run_id = ledger.record(PR, config, project or _project(), [prep], [(_spec(), result)],
                           DIFF, LOGGER, root=Path(tmp))
    return run_id, ledger.read_jsonl(Path(tmp) / "findings.jsonl"), \
        ledger.read_jsonl(Path(tmp) / "runs.jsonl")


def test_registre_garde_les_ecartes_avec_leur_confiance():
    with tempfile.TemporaryDirectory() as tmp:
        run_id, rows, _runs = _record(tmp)
    assert run_id and {r["run_id"] for r in rows} == {run_id}
    fates = sorted((r["fate"], r["confidence"]) for r in rows)
    assert fates == [("dropped_low", 40), ("invalid", 0), ("posted", 95)]
    low = next(r for r in rows if r["fate"] == "dropped_low")
    assert low["fingerprint"] == fingerprint_hash("a.swift", "let z = 1")
    assert low["variant"] == "live" and low["head_sha"] == PR["head"]["sha"]
    assert low["verification"]["status"] == "skipped"


def test_registre_sans_texte_par_defaut():
    with tempfile.TemporaryDirectory() as tmp:
        _, rows, _ = _record(tmp)
    assert all("message" not in r for r in rows)


def test_registre_avec_texte_si_le_projet_le_demande():
    with tempfile.TemporaryDirectory() as tmp:
        _, rows, _ = _record(tmp, project=_project(include_text=True))
    posted = next(r for r in rows if r["fate"] == "posted")
    assert posted["message"] == "m10"


def test_ligne_de_run():
    with tempfile.TemporaryDirectory() as tmp:
        _, _, runs = _record(tmp)
    (run,) = runs
    assert run["model"] == "claude-x" and run["num_turns"] == 12 and run["max_turns"] == 30
    assert run["subtype"] == "success" and run["threshold"] == 70
    assert run["diff_files"] == 1 and run["diff_lines"] == 2
    assert run["fates"] == {"posted": 1, "dropped_low": 1, "invalid": 1}
    assert run["context_files"][0]["path"] == "AGENTS.md"


def test_variante_temoin(monkeypatch):
    monkeypatch.setenv("REVIEWME_LEDGER_VARIANT", "baseline")
    with tempfile.TemporaryDirectory() as tmp:
        _, rows, runs = _record(tmp)
    assert {r["variant"] for r in rows + runs} == {"baseline"}


def test_mode_simule_ecrit_a_part(monkeypatch):
    monkeypatch.delenv("REVIEWME_LEDGER_DIR", raising=False)
    assert ledger.ledger_dir(_config(fake=True)).name == "ledger-simule"
    assert ledger.ledger_dir(_config()).name == "ledger"


def test_hash_de_config_suit_les_reglages():
    with tempfile.TemporaryDirectory() as tmp:
        pdir = Path(tmp) / "p"
        (pdir / "reviewers" / "tech").mkdir(parents=True)
        (pdir / "reviewers" / "tech" / "system.md").write_text("persona")
        (pdir / "reviewers" / "tech" / "reviewer.toml").write_text("priority = 1\n")
        (pdir / "project.toml").write_text("[ledger]\ninclude_text = true\n")
        project = load_project("p", Path(tmp))
        assert project.ledger_include_text is True
        h1 = ledger.config_hash(project)
        assert h1 == ledger.config_hash(project)
        (pdir / "reviewers" / "tech" / "system.md").write_text("persona v2")
        assert ledger.config_hash(project) != h1

        out = Path(tmp) / "registre"
        ledger.snapshot_config(project, out, h1)
        manifest = json.loads((out / "config-snapshot" / h1 / "manifest.json").read_text())
        assert "reviewers/tech/system.md" in manifest["files"]
        assert (out / "config-snapshot" / h1 / "reviewers/tech/reviewer.toml").is_file()
        assert not (out / "config-snapshot" / h1 / "reviewers/tech/system.md").exists()


def test_registre_ne_casse_jamais_la_review():
    """Un registre illisible ou une entrée incohérente : None, pas d'exception."""
    assert ledger.record(PR, _config(), _project(), [object()], [], DIFF, LOGGER,
                         root=Path("/nonexistent/x")) is None
