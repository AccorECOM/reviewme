"""Garde-fou de la migration v0.2 -> v3 du fingerprint (ADR v3 D3).

Le risque couvert : la première review v3 sur une PR déjà commentée par la v0.2 reposte
TOUT en double parce que les anciens marqueurs (sans identifiant de reviewer) ne sont plus
reconnus. Ces tests figent les deux invariants qui l'empêchent :

  1. le HASH est inchangé (le reviewer_id est un préfixe, il n'entre pas dans le sha1) ;
  2. un marqueur sans reviewer est attribué à `tech`.

Exécution : `pytest tests/` ou `python tests/test_fingerprint_migration.py`.
"""
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reviewme.diff_utils import normalize_line
from reviewme.reconciler import (
    _MARKER_RE,
    LEGACY_REVIEWER_ID,
    fingerprint,
    fingerprint_hash,
    marker_key,
)

PATH = "src/dashboard/DashboardService.swift"
LINE = '        let authToken = "sk-live-XXXX"'


def _legacy_hash(path: str, line_content: str) -> str:
    """Formule d'origine, recopiée à l'identique. Ne pas la « moderniser » : c'est elle
    qui sert de témoin — si `fingerprint_hash` s'en écarte, tous les commentaires déjà
    postés deviennent orphelins."""
    return hashlib.sha1(f"{path}\n{normalize_line(line_content)}".encode()).hexdigest()[:10]


def test_hash_inchange_depuis_v02():
    # Si ce test casse, TOUS les commentaires déjà postés deviennent orphelins -> doublons.
    assert fingerprint_hash(PATH, LINE) == _legacy_hash(PATH, LINE)


def test_reviewer_id_absent_du_hash():
    assert fingerprint_hash(PATH, LINE) == fingerprint_hash(PATH, LINE)
    assert "tech" not in fingerprint_hash(PATH, LINE)
    # deux reviewers -> même hash, marqueurs distincts
    assert fingerprint(PATH, LINE, "tech") != fingerprint(PATH, LINE, "us")


def test_marqueur_legacy_attribue_a_tech():
    legacy = f"<!-- reviewme:{_legacy_hash(PATH, LINE)} -->"
    assert marker_key(legacy) == (LEGACY_REVIEWER_ID, _legacy_hash(PATH, LINE))


def test_migration_zero_doublon():
    """LE test de migration : un commentaire v0.2 et un finding v3 du reviewer `tech`
    produisent la MÊME clé de dédup -> le finding est dédupliqué, pas reposté."""
    ancien = f"<!-- reviewme:{_legacy_hash(PATH, LINE)} -->"          # posté par la v0.2
    nouveau = fingerprint(PATH, LINE, "tech")                          # calculé par la v3
    assert marker_key(ancien) == marker_key(nouveau)


def test_reviewers_ne_se_dedupent_pas_entre_eux():
    """Deux reviewers sur la MÊME ligne doivent coexister (sinon le second est avalé)."""
    assert marker_key(fingerprint(PATH, LINE, "tech")) != marker_key(fingerprint(PATH, LINE, "us"))
    assert marker_key(fingerprint(PATH, LINE, "us")) == ("us", fingerprint_hash(PATH, LINE))


def test_commentaire_humain_non_reconnu():
    assert marker_key("Bonne remarque, je corrige.") is None
    assert marker_key("") is None
    assert marker_key(None) is None


def test_les_deux_formes_sont_nettoyees_du_corps():
    """`_degraded_body` retire le marqueur avant l'affichage : les 2 formes doivent partir."""
    for body in (f"texte <!-- reviewme:{_legacy_hash(PATH, LINE)} -->",
                 f"texte {fingerprint(PATH, LINE, 'i18n')}"):
        assert "reviewme:" not in _MARKER_RE.sub("", body)


def test_id_avec_tiret_et_underscore():
    for rid in ("i18n", "us", "tech-ios", "sec_audit"):
        assert marker_key(fingerprint(PATH, LINE, rid)) == (rid, fingerprint_hash(PATH, LINE))


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok   {name}")
            except AssertionError as e:
                fails += 1
                print(f"  FAIL {name}: {e}")
    print("\n" + ("TOUS LES TESTS PASSENT" if not fails else f"{fails} ÉCHEC(S)"))
    sys.exit(1 if fails else 0)
