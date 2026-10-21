"""Tests du « Conseil pour la relecture » : un point qu'un humain doit valider sur la PR.

Ce qui compte : il n'apparaît que s'il est applicable (ancré dans des fichiers de la PR),
jamais en remarque en ligne, et il suffit à faire poster le résumé.

Exécution : `pytest tests/`.
"""
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reviewme.config import Config
from reviewme.diff_utils import LinePos
from reviewme.models import Advice, ReviewResult, parse_review_output
from reviewme.reconciler import PrContext, post_all, prepare

LOGGER = logging.getLogger("test-conseil")
LOGGER.addHandler(logging.NullHandler())
PR = {"number": 7, "head": {"sha": "abcdef1234567890"}, "title": "PR de test"}


def _config(**kw):
    base = dict(github_token="t", github_repo="o/r", confidence_threshold=50, dry_run=False)
    base.update(kw)
    return Config(**base)


def _ctx():
    return PrContext(valid={"src/payments/refund.py": {3: LinePos(3, "x", added=True)}})


def test_parsing_des_conseils():
    r = parse_review_output(json.dumps({"summary": "s", "findings": [], "advice": [
        {"topic": "Ordre des remboursements", "why": "Deux appels inversés.",
         "paths": "src/payments/refund.py"},
        {"topic": "", "why": "sans sujet"},
        {"topic": "a", "why": "b"}, {"topic": "c", "why": "d"}, {"topic": "e", "why": "f"},
    ]}))
    assert len(r.advice) == 3
    assert r.advice[0].paths == ["src/payments/refund.py"]


def test_conseil_non_ancre_ecarte():
    result = ReviewResult("COMMENT", "s", [], advice=[
        Advice("Bien tester", "C'est important.", ["ailleurs/x.py"]),
        Advice("Ordre des remboursements", "Le remboursement passe avant l'annulation.",
               ["src/payments/refund.py", "inexistant.py"]),
    ])
    prep = prepare(PR, _config(), _ctx(), result, LOGGER, "advisor", advice=True)
    assert prep.counts["advice"] == 1
    assert "Review tip" in prep.summary
    assert "Ordre des remboursements" in prep.summary and "Bien tester" not in prep.summary
    assert "inexistant.py" not in prep.summary and prep.batch == []


def test_conseil_coupe_par_defaut_hors_du_relecteur_dedie():
    result = ReviewResult("COMMENT", "s", [], advice=[
        Advice("Ordre", "Pourquoi.", ["src/payments/refund.py"])])
    prep = prepare(PR, _config(), _ctx(), result, LOGGER, "tech")
    assert prep.counts["advice"] == 0 and "Review tip" not in prep.summary


def test_squelette_du_relecteur_conseil():
    from reviewme.projects import TEMPLATES_DIR, _load_reviewer
    spec = _load_reviewer("p", TEMPLATES_DIR, "advisor", "")
    assert not spec.findings and not spec.verify
    assert spec.output_mode == "global" and spec.silent_when_clean
    tech = _load_reviewer("p", TEMPLATES_DIR, "tech", "")
    assert tech.findings


def test_un_conseil_seul_fait_poster_le_resume():
    class GH:
        posts: list = []

        def list_issue_comments(self, n):
            return []

        def post_issue_comment(self, n, body):
            self.posts.append(body)
            return {"id": 1}

    result = ReviewResult("COMMENT", "s", [], advice=[
        Advice("Ordre", "Pourquoi.", ["src/payments/refund.py"])])
    prep = prepare(PR, _config(), _ctx(), result, LOGGER, "advisor", advice=True)
    gh = GH()
    post_all(PR, _config(), gh, [prep], LOGGER)
    assert len(gh.posts) == 1 and "Review tip" in gh.posts[0]


def test_silencieux_mais_pas_muet_sur_un_conseil():
    result = ReviewResult("COMMENT", "Tout est couvert.", [], advice=[
        Advice("CA2 à tester à la main", "Le cas hors ligne n'a pas de test.",
               ["src/payments/refund.py"])])
    prep = prepare(PR, _config(), _ctx(), result, LOGGER, "advisor", output_mode="global",
                   silent_when_clean=True, advice=True)
    assert prep.global_body and "CA2 à tester" in prep.global_body


def test_reviewers_du_core_actives_par_project_toml():
    import tempfile
    from pathlib import Path

    import pytest

    from reviewme.projects import ProjectConfigError, load_project
    with tempfile.TemporaryDirectory() as tmp:
        pdir = Path(tmp) / "boutique"
        pdir.mkdir()
        (pdir / "project.toml").write_text('[reviewers]\ncore = ["tech", "advisor"]\n')
        ids = sorted(s.id for s in load_project("boutique", Path(tmp)).reviewers)
        assert ids == ["advisor", "tech"]

        # Un dossier local du même nom prime sur le reviewer du core.
        local = pdir / "reviewers" / "tech"
        local.mkdir(parents=True)
        (local / "system.md").write_text("persona locale")
        tech = next(s for s in load_project("boutique", Path(tmp)).reviewers if s.id == "tech")
        assert tech.system_prompt == "persona locale"

        (pdir / "project.toml").write_text('[reviewers]\ncore = ["inexistant"]\n')
        with pytest.raises(ProjectConfigError):
            load_project("boutique", Path(tmp))


def test_reviewer_du_core_adapte_par_project_toml():
    import tempfile
    from pathlib import Path

    import pytest

    from reviewme.projects import ProjectConfigError, load_project
    with tempfile.TemporaryDirectory() as tmp:
        pdir = Path(tmp) / "boutique"
        pdir.mkdir()
        (pdir / "project.toml").write_text(
            '[reviewers]\ncore = ["tech"]\n\n[reviewers.tech]\n'
            'read = ["Documentation/ARCHITECTURE.md"]\npaths = ["src/**"]\n'
            'instructions = "Les services sont dans src/services."\nmax_turns = 50\n')
        tech = load_project("boutique", Path(tmp)).reviewers[0]
        assert tech.context_read[-1] == "Documentation/ARCHITECTURE.md"
        assert "AGENTS.md" in tech.context_read          # la base du core est gardée
        assert tech.when_paths == ("src/**",) and tech.max_turns == 50
        assert tech.system_prompt.endswith("Les services sont dans src/services.")

        (pdir / "project.toml").write_text(
            '[reviewers]\ncore = ["tech"]\n\n[reviewers.tech]\nsystem = "x"\n')
        with pytest.raises(ProjectConfigError):
            load_project("boutique", Path(tmp))


def test_langue_des_textes_postes():
    """Les titres du bot suivent `[review] language` ; l'anglais par défaut."""
    result = ReviewResult("COMMENT", "s", [], advice=[
        Advice("Ordre", "Pourquoi.", ["src/payments/refund.py"])])
    fr = prepare(PR, _config(), _ctx(), result, LOGGER, "advisor", advice=True, language="fr")
    assert "Conseil pour la relecture" in fr.summary and "review automatisée" in fr.summary
    en = prepare(PR, _config(), _ctx(), result, LOGGER, "advisor", advice=True)
    assert "Review tip" in en.summary and "automated review" in en.summary


def test_langue_du_projet():
    import tempfile
    from pathlib import Path

    import pytest

    from reviewme.projects import ProjectConfigError, load_project
    from reviewme.reviewer import _build_prompt
    with tempfile.TemporaryDirectory() as tmp:
        pdir = Path(tmp) / "boutique"
        pdir.mkdir()
        (pdir / "project.toml").write_text('[review]\nlanguage = "fr"\n\n[reviewers]\ncore = ["tech"]\n')
        project = load_project("boutique", Path(tmp))
        assert project.language == "fr" and project.reviewers[0].language == "fr"
        prompt = _build_prompt(1, "t", "/x.diff", _config(), project.reviewers[0])
        assert "in French" in prompt
        (pdir / "project.toml").write_text('[review]\nlanguage = "klingon"\n')
        with pytest.raises(ProjectConfigError):
            load_project("boutique", Path(tmp))


def test_bot_reconnu_quelle_que_soit_la_langue():
    from reviewme.enrich import _is_bot
    assert _is_bot({"body": "Still relevant. _ReviewMe · automated review_"})
    assert _is_bot({"body": "Toujours. _ReviewMe · review automatisée_"})
