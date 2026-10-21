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
    prep = prepare(PR, _config(), _ctx(), result, LOGGER, "conseil", advice=True)
    assert prep.counts["advice"] == 1
    assert "Conseil pour la relecture" in prep.summary
    assert "Ordre des remboursements" in prep.summary and "Bien tester" not in prep.summary
    assert "inexistant.py" not in prep.summary and prep.batch == []


def test_conseil_coupe_par_defaut_hors_du_relecteur_dedie():
    result = ReviewResult("COMMENT", "s", [], advice=[
        Advice("Ordre", "Pourquoi.", ["src/payments/refund.py"])])
    prep = prepare(PR, _config(), _ctx(), result, LOGGER, "tech")
    assert prep.counts["advice"] == 0 and "Conseil" not in prep.summary


def test_squelette_du_relecteur_conseil():
    from reviewme.projects import TEMPLATES_DIR, _load_reviewer
    spec = _load_reviewer("p", TEMPLATES_DIR, "conseil", "")
    assert spec.advice and not spec.findings and not spec.verify
    assert spec.output_mode == "global" and spec.silent_when_clean
    tech = _load_reviewer("p", TEMPLATES_DIR, "tech", "")
    assert not tech.advice and tech.findings


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
    prep = prepare(PR, _config(), _ctx(), result, LOGGER, "conseil", advice=True)
    gh = GH()
    post_all(PR, _config(), gh, [prep], LOGGER)
    assert len(gh.posts) == 1 and "Conseil pour la relecture" in gh.posts[0]


def test_silencieux_mais_pas_muet_sur_un_conseil():
    result = ReviewResult("COMMENT", "Tout est couvert.", [], advice=[
        Advice("CA2 à tester à la main", "Le cas hors ligne n'a pas de test.",
               ["src/payments/refund.py"])])
    prep = prepare(PR, _config(), _ctx(), result, LOGGER, "conseil", output_mode="global",
                   silent_when_clean=True, advice=True)
    assert prep.global_body and "CA2 à tester" in prep.global_body
