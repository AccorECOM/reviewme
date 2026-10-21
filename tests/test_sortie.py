"""Tests de la sortie (PR 3) : routage par sévérité, code préexistant, clé Jira.

Ce qui compte : seules les remarques graves et introduites par la PR partent en ligne ;
le reste va dans le résumé, sans se perdre.

Exécution : `pytest tests/`.
"""
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reviewme.config import Config
from reviewme.context.jira import extract_ticket_key
from reviewme.diff_utils import LinePos
from reviewme.models import Finding, ReviewResult, Severity
from reviewme.preexisting import removed_lines, tag_preexisting
from reviewme.reconciler import PrContext, prepare

LOGGER = logging.getLogger("test-sortie")
LOGGER.addHandler(logging.NullHandler())
PR = {"number": 7, "head": {"sha": "abcdef1234567890"}, "title": "PR de test"}


def _config(**kw):
    base = dict(github_token="t", github_repo="o/r", confidence_threshold=50, dry_run=True)
    base.update(kw)
    return Config(**base)


def _ctx():
    return PrContext(valid={"a.swift": {n: LinePos(n, f"l{n}", added=True)
                                       for n in range(10, 14)}})


def _f(line, sev, **kw):
    return Finding(path="a.swift", line=line, severity=sev, message=f"m{line}",
                   confidence=90, **kw)


def test_mineures_et_preexistantes_dans_le_resume():
    fs = [_f(10, Severity.BLOCKER), _f(11, Severity.MINOR),
          _f(12, Severity.IMPORTANT, preexisting=True)]
    prep = prepare(PR, _config(), _ctx(), ReviewResult("COMMENT", "s", fs), LOGGER, "tech")
    assert [c["line"] for c in prep.batch] == [10]
    assert [(f.fate, f.in_summary) for f in fs] == [("posted", False), ("posted", True),
                                                    ("posted", True)]
    assert "Minor findings" in prep.summary and "m11" in prep.summary
    assert "Pre-existing code" in prep.summary and "m12" in prep.summary


def test_seuil_inline_configurable():
    fs = [_f(11, Severity.MINOR)]
    prep = prepare(PR, _config(inline_min_severity="MINOR"), _ctx(),
                   ReviewResult("COMMENT", "s", fs), LOGGER, "tech")
    assert len(prep.batch) == 1


def test_coerce_ne_monte_plus_les_avertissements():
    assert Severity.coerce("warning") is Severity.MINOR
    assert Severity.coerce("medium") is Severity.MINOR
    assert Severity.coerce("high") is Severity.IMPORTANT
    assert Severity.coerce("critical") is Severity.BLOCKER


def test_cle_jira_titre_avant_branche():
    pr = {"title": "feat(Recherche) - PROJ-4321 - partage", "head": {"ref": "feat/PROJ-1111"},
          "body": "voir aussi PROJ-2222"}
    assert extract_ticket_key(pr) == "PROJ-4321"
    pr["title"] = "Partage de la liste"
    assert extract_ticket_key(pr) == "PROJ-1111"


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


def test_code_deplace_tague_preexistant():
    """Un bloc déplacé dans un nouveau fichier par la PR est préexistant ; une ligne
    vraiment nouvelle ne l'est pas."""
    bloc = "\n".join(f"let valeurNumero{i} = calculComplexe({i}, facteur: {i * 3})"
                     for i in range(12)) + "\n"
    with tempfile.TemporaryDirectory() as tmp:
        _git(tmp, "init", "-q", "-b", "main")
        Path(tmp, "Old.swift").write_text(bloc)
        _git(tmp, "add", ".")
        _git(tmp, "commit", "-qm", "base")
        _git(tmp, "update-ref", "refs/remotes/origin/main", "HEAD")
        _git(tmp, "checkout", "-qb", "pr")
        Path(tmp, "Old.swift").unlink()
        Path(tmp, "New.swift").write_text("let toutNeuf = 42\n" + bloc)
        _git(tmp, "add", "-A")
        _git(tmp, "commit", "-qm", "deplacement")
        diff = subprocess.run(["git", "diff", "--no-renames", "origin/main", "HEAD"], cwd=tmp,
                              capture_output=True, text=True).stdout
        lignes = Path(tmp, "New.swift").read_text().splitlines()
        neuf = Finding(path="New.swift", line=1, severity=Severity.BLOCKER, message="x")
        deplace = Finding(path="New.swift", line=5, severity=Severity.BLOCKER, message="y")
        n = tag_preexisting([neuf, deplace], tmp, "main", LOGGER,
                            lambda f: (f.line, lignes[f.line - 1]), removed_lines(diff))
    assert n == 1 and deplace.preexisting and not neuf.preexisting


def test_ligne_de_contexte_est_preexistante():
    """Fichier renommé : ses lignes inchangées sont du contexte dans le diff."""
    f = Finding(path="a", line=3, severity=Severity.IMPORTANT, message="x")
    assert tag_preexisting([f], ".", "", LOGGER, lambda f: (3, "x", False), set()) == 1
    assert f.preexisting


def test_code_copie_n_est_pas_preexistant():
    """Recoller un bloc existant (sans le supprimer ailleurs) introduit le code : pas de tag."""
    bloc = "\n".join(f"let valeurNumero{i} = calculComplexe({i}, facteur: {i * 3})"
                     for i in range(12)) + "\n"
    with tempfile.TemporaryDirectory() as tmp:
        _git(tmp, "init", "-q", "-b", "main")
        Path(tmp, "Old.swift").write_text(bloc)
        _git(tmp, "add", ".")
        _git(tmp, "commit", "-qm", "base")
        _git(tmp, "update-ref", "refs/remotes/origin/main", "HEAD")
        _git(tmp, "checkout", "-qb", "pr")
        Path(tmp, "Copie.swift").write_text(bloc)
        _git(tmp, "add", "-A")
        _git(tmp, "commit", "-qm", "copie")
        diff = subprocess.run(["git", "diff", "origin/main", "HEAD"], cwd=tmp,
                              capture_output=True, text=True).stdout
        lignes = bloc.splitlines()
        copie = Finding(path="Copie.swift", line=5, severity=Severity.BLOCKER, message="y")
        n = tag_preexisting([copie], tmp, "main", LOGGER,
                            lambda f: (f.line, lignes[f.line - 1]), removed_lines(diff))
    assert n == 0 and not copie.preexisting


def test_blocker_deplace_reste_en_ligne():
    fs = [_f(10, Severity.BLOCKER, preexisting=True), _f(11, Severity.IMPORTANT, preexisting=True)]
    prep = prepare(PR, _config(), _ctx(), ReviewResult("COMMENT", "s", fs), LOGGER, "tech")
    assert [c["line"] for c in prep.batch] == [10]
    assert "code moved by this PR" in prep.batch[0]["body"] and "m11" in prep.summary


def test_mineure_deja_en_ligne_pas_en_double():
    from reviewme.reconciler import fingerprint_hash
    ctx = _ctx()
    ctx.inline_by_marker[("tech", fingerprint_hash("a.swift", "l11"))] = {"id": 3, "position": 2}
    f = _f(11, Severity.MINOR)
    prep = prepare(PR, _config(), ctx, ReviewResult("COMMENT", "s", [f]), LOGGER, "tech")
    assert f.fate == "deduped" and "m11" not in prep.summary


def test_resume_seul_mis_a_jour_pas_reposte():
    from reviewme.reconciler import post_all

    class GH:
        def __init__(self, existants):
            self.existants, self.posts, self.maj = existants, [], []

        def list_issue_comments(self, n):
            return self.existants

        def post_issue_comment(self, n, body):
            self.posts.append(body)
            return {"id": 1}

        def update_issue_comment(self, cid, body):
            self.maj.append(cid)
            return {"id": cid}

    fs = [_f(11, Severity.MINOR)]
    cfg = _config(dry_run=False)
    prep = prepare(PR, cfg, _ctx(), ReviewResult("COMMENT", "s", fs), LOGGER, "tech")
    gh = GH([])
    post_all(PR, cfg, gh, [prep], LOGGER)
    assert len(gh.posts) == 1
    gh2 = GH([{"id": 42, "body": gh.posts[0]}])
    post_all(PR, cfg, gh2, [prep], LOGGER)
    assert gh2.posts == [] and gh2.maj == [42]


def test_seuil_inline_invalide_refuse(monkeypatch):
    import pytest

    from reviewme.config import load_config
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("REVIEWME_INLINE_MIN_SEVERITY", "WARNING")
    with pytest.raises(RuntimeError):
        load_config(require_repo=False)


def test_sans_base_rien_n_est_tague():
    with tempfile.TemporaryDirectory() as tmp:
        f = Finding(path="a", line=1, severity=Severity.BLOCKER, message="x")
        assert tag_preexisting([f], tmp, "main", LOGGER) == 0 and not f.preexisting
