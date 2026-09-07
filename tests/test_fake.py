"""Tests du mode simulé (`REVIEWME_FAKE`).

Ce qui compte n'est pas que le mode « rende quelque chose », mais qu'il rende quelque
chose d'EXPLOITABLE par le reste de la chaîne :
  - des findings ancrés sur de vraies lignes du diff, sinon la validation anti-422 les
    rejette tous et on ne teste plus rien ;
  - des empreintes distinctes par reviewer, sinon le dédup masque le fan-out ;
  - des confiances qui encadrent le seuil, pour voir le filtrage à l'œuvre.

Exécution : `python tests/test_fake.py`.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reviewme.fake import _lignes_ajoutees, review_simulee  # noqa: E402

DIFF = """diff --git a/src/App.swift b/src/App.swift
index 1111111..2222222 100644
--- a/src/App.swift
+++ b/src/App.swift
@@ -10,6 +10,8 @@ struct App {
 let a = 1
+let ajout1 = 2
+let ajout2 = 3
 let b = 4
-let supprime = 5
+let ajout3 = 6
diff --git a/src/Autre.swift b/src/Autre.swift
--- a/src/Autre.swift
+++ b/src/Autre.swift
@@ -1,2 +1,3 @@
 debut
+let ajout4 = 7
"""

_resultats = []


def verifie(nom, condition, detail=""):
    _resultats.append((nom, condition, detail))
    print(f"  {'ok  ' if condition else 'ECHEC'} {nom}" + (f" — {detail}" if detail and not condition else ""))


def test_numeros_de_ligne_cote_droit():
    """Une ligne supprimée ne doit PAS consommer de numéro côté RIGHT."""
    ajouts = list(_lignes_ajoutees(DIFF))
    attendu = [
        ("src/App.swift", 11, "let ajout1 = 2"),
        ("src/App.swift", 12, "let ajout2 = 3"),
        ("src/App.swift", 14, "let ajout3 = 6"),   # 13 = `let b`, la suppression ne compte pas
        ("src/Autre.swift", 2, "let ajout4 = 7"),
    ]
    verifie("test_numeros_de_ligne_cote_droit", ajouts == attendu, f"obtenu {ajouts}")


def test_findings_ancres_sur_le_diff():
    """Chaque finding doit pointer une ligne réellement ajoutée."""
    reels = {(c, n) for c, n, _ in _lignes_ajoutees(DIFF)}
    r = review_simulee("tech", DIFF)
    hors = [(f.path, f.line) for f in r.findings if (f.path, f.line) not in reels]
    verifie("test_findings_ancres_sur_le_diff", not hors, f"hors diff : {hors}")


def test_confiances_encadrent_le_seuil():
    r = review_simulee("tech", DIFF)
    conf = sorted(f.confidence for f in r.findings)
    verifie("test_confiances_encadrent_le_seuil",
            any(c < 70 for c in conf) and any(c >= 70 for c in conf), f"confiances {conf}")


def test_reviewers_ancrent_a_des_endroits_differents():
    """Sinon les quatre produisent la même empreinte et le dédup masque le fan-out."""
    ancres = {rid: {(f.path, f.line) for f in review_simulee(rid, DIFF).findings}
              for rid in ("tech", "architect", "us", "i18n")}
    distincts = len({frozenset(v) for v in ancres.values()})
    verifie("test_reviewers_ancrent_a_des_endroits_differents", distincts > 1,
            f"{distincts} jeu(x) d'ancrage distinct(s) pour 4 reviewers")


def test_aucun_cout_et_metadata_marquee():
    r = review_simulee("tech", DIFF)
    verifie("test_aucun_cout_et_metadata_marquee",
            r.metadata["cost_usd"] == 0.0 and r.metadata.get("fake") is True and r.parsed_ok)


def test_diff_vide_ne_casse_pas():
    r = review_simulee("tech", "")
    verifie("test_diff_vide_ne_casse_pas", r.findings == [] and r.parsed_ok)


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        fn()
    echecs = [n for n, ok, _ in _resultats if not ok]
    print("\n" + ("TOUS LES TESTS PASSENT" if not echecs else f"ECHECS : {echecs}"))
    sys.exit(1 if echecs else 0)
