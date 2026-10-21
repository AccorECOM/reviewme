"""Tests du fan-out multi-reviewers (ADR v3 D1/D2/D4/D6).

Couvre les trois mécanismes qui coûtent cher s'ils lâchent :
  - la SÉLECTION (D4) : un reviewer qui se déclenche sur toutes les PR fait payer le projet ;
  - le PLAFOND (D6) : quatre reviewers zélés noient le développeur et le bot est désactivé ;
  - le CLOISONNEMENT (D3) : deux reviewers sur une même ligne ne doivent pas s'annuler.

Exécution : `pytest tests/` ou `python tests/test_fanout.py`.
"""
import logging
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reviewme.config import Config
from reviewme.diff_utils import LinePos
from reviewme.models import Finding, ReviewResult, Severity
from reviewme.projects import (
    ProjectConfig,
    ProjectConfigError,
    ReviewerSpec,
    load_project,
    select_reviewers,
)
from reviewme.reconciler import (
    PrContext,
    _apply_cap,
    marker_key,
    prepare,
)

LOGGER = logging.getLogger("test")
LOGGER.addHandler(logging.NullHandler())
PR = {"number": 7, "head": {"sha": "abcdef1234567890"}, "title": "PR de test"}


def _spec(rid, **kw):
    return ReviewerSpec(id=rid, project="p", system_prompt="x", **kw)


def _project(*specs):
    return ProjectConfig(name="p", directory=None, reviewers=tuple(specs))


def _config(**kw):
    base = dict(github_token="t", github_repo="o/r", confidence_threshold=80)
    base.update(kw)
    return Config(**base)


# --------------------------------------------------------------- sélection (D4)

def test_reviewer_sans_when_se_declenche_toujours():
    kept, skipped = select_reviewers(_project(_spec("tech")), ["src/Main.swift"])
    assert [s.id for s in kept] == ["tech"] and not skipped


def test_when_paths_evite_de_payer_pour_rien():
    project = _project(_spec("i18n", when_paths=("**/*.strings", "**/values*/strings.xml")))
    kept, skipped = select_reviewers(project, ["src/Main.swift", "README.md"])
    assert not kept and "when.paths" in skipped[0][1]

    kept, _ = select_reviewers(project, ["src/Main.swift", "fr.lproj/Localizable.strings"])
    assert [s.id for s in kept] == ["i18n"]


def test_when_paths_matche_a_la_racine_et_en_profondeur():
    project = _project(_spec("i18n", when_paths=("**/*.strings",)))
    for path in ("Localizable.strings", "a/b/c/Localizable.strings"):
        kept, _ = select_reviewers(project, [path])
        assert [s.id for s in kept] == ["i18n"], path


def test_requires_absent_skippe_sans_erreur():
    """Jira non configuré ou ticket introuvable : le reviewer `us` est écarté, pas en échec."""
    project = _project(_spec("tech"), _spec("us", requires=("jira_ticket",)))
    kept, skipped = select_reviewers(project, ["a.py"], available_context=set())
    assert [s.id for s in kept] == ["tech"]
    assert skipped[0][0].id == "us" and "jira_ticket" in skipped[0][1]

    kept, _ = select_reviewers(project, ["a.py"], available_context={"jira_ticket"})
    assert [s.id for s in kept] == ["tech", "us"]


def test_ordre_par_priorite():
    project = _project(_spec("i18n", priority=30), _spec("tech", priority=10), _spec("us", priority=20))
    kept, _ = select_reviewers(project, ["a.py"])
    assert [s.id for s in kept] == ["tech", "us", "i18n"]


# --------------------------------------------------------------- plafond (D6)

def _item(reviewer, sev, conf, line):
    return {"path": "a.swift", "line": line, "side": "RIGHT", "body": "b",
            "_severity": sev, "_confidence": conf, "_reviewer": reviewer}


class _Prep:
    def __init__(self, rid, batch):
        self.reviewer_id, self.batch, self.counts = rid, batch, {}


def test_plafond_garde_les_plus_graves():
    a = _Prep("tech", [_item("tech", Severity.MINOR, 99, 1), _item("tech", Severity.BLOCKER, 85, 2)])
    b = _Prep("i18n", [_item("i18n", Severity.IMPORTANT, 90, 3)])
    kept = _apply_cap([a, b], 2, LOGGER, 7)
    assert [c["_severity"] for c in kept] == [Severity.BLOCKER, Severity.IMPORTANT]
    assert a.counts["capped"] == 1 and b.counts["capped"] == 0


def test_plafond_departage_par_confiance():
    a = _Prep("tech", [_item("tech", Severity.BLOCKER, 82, 1), _item("tech", Severity.BLOCKER, 97, 2)])
    kept = _apply_cap([a], 1, LOGGER, 7)
    assert kept[0]["_confidence"] == 97


def test_pas_de_plafond_si_sous_le_seuil():
    a = _Prep("tech", [_item("tech", Severity.MINOR, 90, 1)])
    assert len(_apply_cap([a], 10, LOGGER, 7)) == 1 and a.counts == {}


# --------------------------------------------------------------- préparation (D2/D3)

def _result(findings, summary="Résumé.", parsed_ok=True):
    return ReviewResult(status="COMMENT", summary=summary, findings=findings, parsed_ok=parsed_ok)


def _finding(line=10, sev=Severity.BLOCKER, conf=95, path="a.swift", snippet="let x = y!"):
    return Finding(path=path, line=line, severity=sev, message="Souci ici",
                   snippet=snippet, confidence=conf)


def _ctx():
    # `valid_positions` renvoie {path: {ligne: LinePos}} : ici la ligne 10 est commentable.
    return PrContext(valid={"a.swift": {10: LinePos(10, "let x = y!", added=True)}})


def test_mode_global_ne_produit_aucun_inline():
    """Un `us` en mode global poste UN commentaire : « AC3 non couvert » n'a pas de ligne."""
    prep = prepare(PR, _config(), _ctx(), _result([_finding()]), LOGGER, "us", output_mode="global")
    assert prep.batch == [] and prep.global_body and prep.global_update_id is None
    assert marker_key(prep.global_body) == ("us", marker_key(prep.global_body)[1])


def test_mode_global_met_a_jour_au_lieu_dempiler():
    ctx = _ctx()
    from reviewme.reconciler import summary_fingerprint
    marker = summary_fingerprint("us")
    ctx.global_by_marker[marker_key(marker)] = {"id": 4242, "body": marker}
    prep = prepare(PR, _config(), ctx, _result([]), LOGGER, "us", output_mode="global")
    assert prep.global_update_id == 4242


def test_deux_reviewers_meme_ligne_ne_sannulent_pas():
    """Le bug que D3 évite : sans reviewer dans la clé, le second serait avalé."""
    ctx = _ctx()
    tech = prepare(PR, _config(), ctx, _result([_finding()]), LOGGER, "tech", prefix="[TECH]")
    # on simule que le commentaire de `tech` est déjà en ligne sur la PR
    for item in tech.batch:
        ctx.inline_by_marker[marker_key(item["body"])] = {"id": 1, "position": 3, "body": item["body"]}

    again = prepare(PR, _config(), ctx, _result([_finding()]), LOGGER, "tech")
    assert again.batch == [] and again.counts["deduped"] == 1        # dédup du même reviewer

    other = prepare(PR, _config(), ctx, _result([_finding()]), LOGGER, "i18n", prefix="[I18N]")
    assert len(other.batch) == 1 and other.counts["deduped"] == 0    # cloisonnement


def test_seuil_de_confiance_respecte():
    prep = prepare(PR, _config(), _ctx(), _result([_finding(conf=50)]), LOGGER, "tech")
    assert prep.batch == [] and prep.counts["dropped_low"] == 1


def test_ligne_imprecise_est_reancree_par_le_snippet():
    """Le LLM se trompe de quelques lignes : on retrouve la bonne par le contenu."""
    prep = prepare(PR, _config(), _ctx(), _result([_finding(line=999)]), LOGGER, "tech")
    assert len(prep.batch) == 1 and prep.batch[0]["line"] == 10


def test_finding_hors_diff_bascule_en_summary():
    """Un fichier absent du diff n'est pas commentable (anti-422) : il part dans le summary."""
    hors = _finding(path="jamais/touche.swift", snippet="autre chose")
    prep = prepare(PR, _config(), _ctx(), _result([hors]), LOGGER, "tech")
    assert prep.batch == [] and prep.counts["out_of_diff"] == 1
    assert "jamais/touche.swift" in prep.summary


def test_json_illisible_tombe_en_commentaire_global():
    prep = prepare(PR, _config(), _ctx(), _result([], parsed_ok=False), LOGGER, "tech")
    assert prep.counts["fallback"] and prep.global_body and not prep.batch


def test_prefixe_seulement_en_multi_reviewer():
    solo = prepare(PR, _config(), _ctx(), _result([_finding()]), LOGGER, "tech")
    multi = prepare(PR, _config(), _ctx(), _result([_finding()]), LOGGER, "tech", prefix="[TECH]")
    assert "[TECH]" not in solo.batch[0]["body"] and "[TECH]" in multi.batch[0]["body"]


# --------------------------------------------------------------- robustesse du contexte git

def test_fetch_de_la_branche_de_base_ne_casse_jamais_la_review():
    """Sur un clone CI peu profond ou un gros dépôt, ce fetch peut traîner. Il est
    best-effort : son échec ne doit pas faire tomber la review, qui n'en dépend pas."""
    import subprocess as sp
    import types
    from unittest.mock import patch

    import reviewme.run as R

    class _GH:
        def get_pr_diff(self, n):
            return ""            # sort juste après le fetch : on teste bien ce point-là
        def get_pr_files(self, n):
            return []

    pr = {"number": 1, "head": {"sha": "a" * 40}, "base": {"ref": "develop"},
          "title": "t", "user": {}}
    cfg = types.SimpleNamespace(repo_path=".", dry_run=True)

    for panne in (sp.TimeoutExpired("git fetch", 30), OSError("git introuvable")):
        with patch.object(R.subprocess, "run", side_effect=panne):
            out = R.run_review(pr, cfg, _GH(), LOGGER, force=True)
        assert "error" not in out, f"{type(panne).__name__} a fait tomber la review : {out}"


def test_dry_run_affiche_ce_qui_serait_poste():
    """Sans le contenu, le dry-run ne permet ni de juger la review ni de calibrer le seuil."""
    import logging as _logging

    from reviewme.reconciler import post_all

    lignes = []

    class _Catch(_logging.Handler):
        def emit(self, record):
            lignes.append(record.getMessage())

    logger = _logging.getLogger("test-dryrun")
    logger.addHandler(_Catch())
    logger.setLevel(_logging.INFO)   # sans ça, les INFO sont filtrés par le niveau du root
    prep = prepare(PR, _config(dry_run=True), _ctx(), _result([_finding()]), logger, "tech")
    post_all(PR, _config(dry_run=True), None, [prep], logger)

    joint = "\n".join(lignes)
    assert "a.swift:10" in joint          # où
    assert "Souci ici" in joint           # quoi
    assert "95" in joint                  # avec quelle confiance
    assert "reviewme:" not in joint       # le marqueur technique reste masqué


def test_requires_inconnu_est_refuse_au_chargement():
    """`requires = ["jira"]` au lieu de "jira_ticket" rendrait le reviewer inerte à vie."""
    import reviewme.projects as P
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_project(root, "p", {"us": ('requires = ["jira"]\n', "persona")})
        try:
            P.load_project("p", root)
        except P.ProjectConfigError as e:
            assert "jira_ticket" in str(e)      # le message donne la valeur attendue
        else:
            raise AssertionError("ProjectConfigError attendue")


def test_id_de_reviewer_libre():
    """Les noms tech/us/i18n sont des conventions, pas des valeurs imposées."""
    import reviewme.projects as P
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_project(root, "p", {"securite-mobile": ('output_mode = "inline"\n', "persona"),
                                   "perf": ("", "persona")})
        assert sorted(r.id for r in P.load_project("p", root).reviewers) == ["perf", "securite-mobile"]


def test_inline_et_global_sont_comptes_separement():
    """Un commentaire global se lisait « 1 inline » alors qu'aucun finding n'était passé."""
    from reviewme.reconciler import post_all

    class _GH:
        def __init__(self):
            self.globaux = 0
        def create_review(self, *a, **k):
            raise AssertionError("aucun inline ne devait être posté")
        def post_issue_comment(self, pr, body):
            self.globaux += 1
        def update_issue_comment(self, cid, body):
            self.globaux += 1

    # un reviewer `global` qui ne trouve rien poste quand même son commentaire de synthèse
    prep = prepare(PR, _config(), _ctx(), _result([]), LOGGER, "us", output_mode="global")
    gh = _GH()
    totaux = post_all(PR, _config(), gh, [prep], LOGGER)

    assert gh.globaux == 1
    assert totaux["posted_global"] == 1
    assert totaux["posted_inline"] == 0        # c'est la distinction qui manquait
    assert totaux["posted"] == 1               # le total reste juste


# --------------------------------------------------------------- cycle de vie sur un nouveau commit

def _commentaire_existant(ctx, finding, reviewer="tech", position=3, cid=1):
    """Simule un commentaire déjà posté par le bot sur ce finding."""
    from reviewme.reconciler import fingerprint_hash
    ctx.inline_by_marker[(reviewer, fingerprint_hash(finding.path, "let x = y!"))] = {
        "id": cid, "position": position, "path": finding.path, "line": finding.line,
        "body": "**[BLOCKER]** Souci ici", "user": {"login": "bot"},
    }


def test_nouveau_commit_probleme_toujours_la_ligne_inchangee():
    """Cas 1 : rien ne bouge → dédup, aucun nouveau commentaire, aucun bruit."""
    ctx = _ctx()
    f = _finding()
    _commentaire_existant(ctx, f, position=3)
    prep = prepare(PR, _config(), ctx, _result([f]), LOGGER, "tech")
    assert prep.batch == [] and prep.replies == []
    assert prep.counts["deduped"] == 1 and prep.counts["resolus"] == 0


def test_nouveau_commit_probleme_toujours_la_ligne_deplacee():
    """Cas 2 : la ligne a bougé mais le problème demeure → réponse dans le thread,
    jamais un nouveau commentaire top-level (sinon la PR se remplit de doublons)."""
    ctx = _ctx()
    f = _finding()
    _commentaire_existant(ctx, f, position=None)      # GitHub a marqué le commentaire obsolète
    prep = prepare(PR, _config(), ctx, _result([f]), LOGGER, "tech")
    assert prep.batch == []
    assert len(prep.replies) == 1
    assert "Toujours d'actualité" in prep.replies[0][1]
    assert prep.counts["resolus"] == 0


def test_nouveau_commit_probleme_corrige():
    """Cas 3 : la ligne a changé ET le finding a disparu → on répond que c'est réglé."""
    ctx = _ctx()
    _commentaire_existant(ctx, _finding(), position=None)
    prep = prepare(PR, _config(), ctx, _result([]), LOGGER, "tech")   # plus aucun finding
    assert len(prep.replies) == 1
    assert "n'apparaît plus" in prep.replies[0][1]
    assert prep.counts["resolus"] == 1


def test_finding_disparu_mais_ligne_inchangee_ne_conclut_rien():
    """Garde-fou : un modèle qui varie d'un run à l'autre ne doit pas annoncer une
    correction imaginaire. Sans changement de la ligne, on se tait."""
    ctx = _ctx()
    _commentaire_existant(ctx, _finding(), position=3)   # la ligne n'a PAS bougé
    prep = prepare(PR, _config(), ctx, _result([]), LOGGER, "tech")
    assert prep.replies == [] and prep.counts["resolus"] == 0


def test_un_autre_reviewer_ne_cloture_pas_les_remarques_du_premier():
    ctx = _ctx()
    _commentaire_existant(ctx, _finding(), reviewer="tech", position=None)
    prep = prepare(PR, _config(), ctx, _result([]), LOGGER, "architect")
    assert prep.replies == [] and prep.counts["resolus"] == 0


def test_feedback_produit_des_regles_attribuees():
    """Une règle sans auteur ne peut pas être contestée : l'attribution est le mécanisme."""
    from reviewme.feedback import collecter, en_regles
    from reviewme.reconciler import fingerprint

    marqueur = fingerprint("a.swift", "let x = y!", "tech")

    class _GH:
        def list_review_comments(self, n):
            return [
                {"id": 1, "in_reply_to_id": None, "path": "a.swift", "line": 10,
                 "body": f"**[BLOCKER]** Force-unwrap ici.\n{marqueur}", "user": {"login": "bot"}},
                {"id": 2, "in_reply_to_id": 1,
                 "body": "Toléré dans les tests, c'est notre convention.",
                 "user": {"login": "alice"}},
                {"id": 3, "in_reply_to_id": None, "path": "b.swift",
                 "body": "discussion entre humains", "user": {"login": "bob"}},
            ]

    echanges = collecter(_GH(), 42, LOGGER)
    assert len(echanges) == 1                      # le fil purement humain est ignoré
    assert echanges[0]["auteur"] == "alice"
    assert echanges[0]["reviewer"] == "tech"

    regles = en_regles(echanges, 42)
    assert "alice" in regles and "#42" in regles    # qui, et sur quelle PR
    assert "a.swift" in regles
    assert "À relire" in regles                     # proposé, pas décidé
    assert marqueur not in regles


# --------------------------------------------------------------- statistiques

def test_stats_sans_contenu_et_idempotentes():
    """Ces compteurs transitent par un cache de CI : aucun contenu de PR n'y entre.
    Et rejouer un build ne doit pas gonfler les totaux."""
    from reviewme.stats import enregistrer, resume

    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "stats.json"
        for _ in range(2):                        # même (PR, commit) deux fois
            enregistrer(147, "abc123def456", "alice", "p", ["tech"],
                        {"posted": 3, "deduped": 0, "dropped_low": 1}, 0.29, 120_000, 4.3,
                        LOGGER, f)
        enregistrer(148, "ff00aa11bb22", "bob", "p", ["tech", "i18n"],
                    {"posted": 0, "deduped": 3}, 1.44, 90_000, 2.9, LOGGER, f)

        brut = f.read_text(encoding="utf-8")
        for interdit in ("summary", "pr_title", "message", "snippet"):
            assert interdit not in brut, f"contenu de review dans les stats : {interdit}"

        r = resume(f)
        assert r["runs"] == 2 and r["prs"] == 2          # le rejeu n'a pas doublé
        assert r["remarques_postees"] == 3
        assert r["remarques_dedupliquees"] == 3          # ce qu'on a évité de reposter
        assert r["cout_total_usd"] == 1.73
        assert set(r["par_auteur"]) == {"alice", "bob"}


def test_stats_illisibles_ne_bloquent_pas():
    from reviewme.stats import enregistrer, resume

    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "stats.json"
        f.write_text("{ceci n'est pas du json", encoding="utf-8")
        enregistrer(1, "a" * 12, "x", "p", [], {}, 0.0, 0, 0.0, LOGGER, f)
        assert resume(f)["runs"] == 1               # reparti proprement


def test_rapport_html_autonome_et_sans_contenu():
    """La page circule : aucune requête réseau, et aucun contenu de PR dedans."""
    from reviewme.report import generer
    from reviewme.stats import enregistrer

    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "stats.json"
        enregistrer(147, "abc123def456", "alice", "p", ["tech"],
                    {"posted": 3, "deduped": 2, "dropped_low": 1}, 0.29, 120_000, 4.3, LOGGER, f)
        page = generer(f)

    assert page.startswith("<!doctype html>")
    for externe in ("http://", "https://", "<script"):
        assert externe not in page, f"la page n'est pas autonome : {externe}"
    assert "alice" in page and "0.29" in page          # les compteurs y sont
    assert "#7C3AED" in page                            # palette validée par le script du skill


# --------------------------------------------------------------- câblage du contexte

def test_tous_les_blocs_de_contexte_arrivent_dans_le_prompt():
    """Le vrai risque du fan-out : un bloc calculé mais jamais transmis au modèle.

    Vérifie le câblage complet — conventions du dépôt, faits du precheck, contexte
    externe, historique — jusqu'au prompt réellement passé à la CLI.
    """
    from unittest.mock import patch

    import reviewme.run as R

    capture = {}

    def _faux_reviewer(pr_number, pr_title, pr_diff, config, spec=None, extra_context=""):
        capture["extra"] = extra_context
        capture["spec"] = spec
        return _result([])

    with tempfile.TemporaryDirectory() as tmp:
        depot = Path(tmp)
        (depot / "AGENTS.md").write_text("Règle maison : 4 espaces.", encoding="utf-8")
        rdir = depot / "r"
        rdir.mkdir()
        (rdir / "p.py").write_text(
            "import sys\nd = sys.stdin.read()\n"
            "print('FAITS: ' + str(len(d.splitlines())) + ' lignes de diff')\n",
            encoding="utf-8")

        spec = _spec("tech", context_read=("AGENTS.md",), precheck="p.py",
                     directory=rdir, requires=("jira_ticket",))
        cfg = _config(repo_path=str(depot))

        with patch.object(R, "run_reviewer", _faux_reviewer):
            R._run_one(spec, 7, "titre", "+import A\n+import B\n", cfg,
                       {"jira_ticket": "TICKET: couvre le cas X"}, LOGGER,
                       historique="HISTORIQUE: alice a répondu")

    extra = capture["extra"]
    assert "TICKET: couvre le cas X" in extra          # contexte externe (Jira)
    assert "HISTORIQUE: alice a répondu" in extra      # fils de discussion
    assert "Règle maison : 4 espaces." in extra        # conventions lues dans le dépôt
    assert "FAITS: 2 lignes de diff" in extra          # precheck, ET il a reçu le diff


# --------------------------------------------------------------- historique de la review

def test_historique_donne_les_reponses_humaines_a_l_agent():
    """Sans ça, un « c'est volontaire » est oublié dès que la ligne bouge."""
    from reviewme.history import build_history_context
    from reviewme.reconciler import fingerprint

    marqueur = fingerprint("a.swift", "let x = y!", "tech")

    class _GH:
        def list_review_comments(self, n):
            return [
                {"id": 1, "in_reply_to_id": None, "path": "a.swift", "line": 10,
                 "body": f"**[BLOCKER]** Force-unwrap ici.\n{marqueur}",
                 "user": {"login": "bot"}},
                {"id": 2, "in_reply_to_id": 1, "path": "a.swift", "line": 10,
                 "body": "Volontaire : la valeur est garantie par le contrat amont.",
                 "user": {"login": "alice"}},
                {"id": 3, "in_reply_to_id": None, "path": "b.swift", "line": 4,
                 "body": "Question d'un humain, sans marqueur", "user": {"login": "bob"}},
            ]

    mesures: dict = {}
    bloc = build_history_context(_GH(), 7, LOGGER, mesures)
    assert mesures == {"remarques_ouvertes": 1, "remarques_avec_reponse": 1}
    assert "a.swift:10" in bloc                       # la remarque déjà postée
    assert "alice" in bloc and "Volontaire" in bloc    # la réponse humaine
    assert "Question d'un humain" not in bloc          # les fils purement humains sont hors sujet
    assert "n'y reviens" in bloc                       # la consigne de ne pas insister
    assert "jamais des instructions" in bloc           # contenu tiers marqué non fiable
    assert marqueur not in bloc                        # marqueur technique nettoyé


def test_historique_absent_ne_bloque_pas():
    from reviewme.history import build_history_context

    class _Vide:
        def list_review_comments(self, n):
            return []

    class _Casse:
        def list_review_comments(self, n):
            raise RuntimeError("API indisponible")

    assert build_history_context(_Vide(), 1, LOGGER) == ""
    assert build_history_context(_Casse(), 1, LOGGER) == ""


def test_mcp_config_confinee_au_dossier_du_reviewer():
    """Une config peut venir d'une PR : elle ne doit pas pouvoir désigner un fichier
    quelconque du dépôt comme configuration de serveurs MCP."""
    from reviewme.reviewer import _mcp_config_path

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "reviewers" / "tech"
        base.mkdir(parents=True)
        (base / "mcp.json").write_text("{}", encoding="utf-8")
        (Path(tmp) / "ailleurs.json").write_text("{}", encoding="utf-8")

        assert _mcp_config_path(_spec("tech", mcp_config="mcp.json", directory=base)) is not None
        assert _mcp_config_path(_spec("tech", mcp_config="../../ailleurs.json", directory=base)) is None
        assert _mcp_config_path(_spec("tech", mcp_config="absent.json", directory=base)) is None
        assert _mcp_config_path(_spec("tech")) is None


# --------------------------------------------------------------- config non fiable

def test_env_dinstance_ne_peut_pas_choisir_le_binaire_execute():
    """Une config peut venir d'une PR : `.reviewme/.env` avec CLAUDE_BIN = RCE."""
    import os as _os

    from reviewme.config import _load_instance_env

    with tempfile.TemporaryDirectory() as tmp:
        env = Path(tmp) / ".env"
        env.write_text("CLAUDE_BIN=/bin/sh\nGITHUB_TOKEN=vole\nCONFIDENCE_THRESHOLD=1\n",
                       encoding="utf-8")
        for k in ("CLAUDE_BIN", "GITHUB_TOKEN", "CONFIDENCE_THRESHOLD"):
            _os.environ.pop(k, None)
        try:
            _load_instance_env(env)
            assert "CLAUDE_BIN" not in _os.environ      # ce qui s'exécute
            assert "GITHUB_TOKEN" not in _os.environ    # où l'on s'authentifie
            assert _os.environ["CONFIDENCE_THRESHOLD"] == "1"   # réglage anodin : lu
        finally:
            for k in ("CLAUDE_BIN", "GITHUB_TOKEN", "CONFIDENCE_THRESHOLD"):
                _os.environ.pop(k, None)


def test_precheck_ne_peut_pas_sortir_du_dossier_du_reviewer():
    from reviewme.precheck import run_precheck

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "reviewers" / "tech"
        base.mkdir(parents=True)
        piege = Path(tmp) / "evasion.sh"
        piege.write_text("#!/bin/sh\necho pwned\n", encoding="utf-8")
        piege.chmod(0o755)
        spec = _spec("tech", precheck="../../evasion.sh", directory=base)
        assert run_precheck(spec, _config(repo_path=tmp), LOGGER) == ""


# --------------------------------------------------------------- plugins

def test_sans_plugin_declare_aucune_installation():
    from reviewme.plugins import ensure_plugins
    ensure_plugins(_spec("tech"), "/bin/false", LOGGER)   # ne doit rien lancer, donc ne pas échouer


def test_plugin_en_echec_leve_une_erreur_explicite():
    from reviewme.plugins import PluginError, ensure_plugins
    spec = _spec("tech", plugins_install=("plugin-qui-nexiste-pas",))
    try:
        ensure_plugins(spec, "/bin/false", LOGGER)        # /bin/false : échoue toujours
    except PluginError as e:
        assert "plugin-qui-nexiste-pas" in str(e)
    else:
        raise AssertionError("PluginError attendue")


def test_parsing_de_plugin_list():
    """`claude plugin list` mélange identifiants et lignes de détail."""
    import reviewme.plugins as P
    sortie = ("Installed plugins:\n\n"
              "  ❯ context7@claude-plugins-official\n"
              "    Version: unknown\n"
              "    Scope: user\n"
              "  ❯ autre-plugin@perso\n")
    original = P._run
    P._run = lambda cli, args, logger: (0, sortie)
    try:
        assert P.installed_plugins("claude", LOGGER) == {"context7", "autre-plugin"}
    finally:
        P._run = original


# --------------------------------------------------------------- moteur LLM

def test_modele_par_reviewer_prime_sur_la_variable_globale():
    """Un relecteur factuel n'a pas besoin du même modèle qu'une analyse d'architecture."""
    import os as _os

    from reviewme.reviewer import pick_model

    original = _os.environ.get("CLAUDE_MODEL")
    _os.environ["CLAUDE_MODEL"] = "modele-global"
    try:
        assert pick_model(_spec("i18n", model="modele-du-reviewer")) == "modele-du-reviewer"
        assert pick_model(_spec("tech")) == "modele-global"
        _os.environ.pop("CLAUDE_MODEL")
        assert pick_model(_spec("tech")) == ""          # vide : la CLI décide
    finally:
        _os.environ.pop("CLAUDE_MODEL", None)
        if original is not None:
            _os.environ["CLAUDE_MODEL"] = original


def test_claude_bin_permet_un_wrapper():
    """Pointer un wrapper maison (passerelle, quotas, logs) sans toucher au code."""
    from reviewme.reviewer import _find_claude
    assert _find_claude(_config(claude_bin="/bin/echo")) == "/bin/echo"
    assert _find_claude(_config(claude_bin="  ")) == _find_claude(_config())   # vide = défaut


def test_claude_bin_introuvable_message_clair():
    from reviewme.reviewer import _find_claude
    try:
        _find_claude(_config(claude_bin="/nexiste/pas/binaire"))
    except RuntimeError as e:
        assert "CLAUDE_BIN" in str(e)
    else:
        raise AssertionError("RuntimeError attendue")


# --------------------------------------------------------------- conventions du dépôt (D13)

def test_context_read_injecte_les_fichiers_et_liste_les_dossiers():
    from reviewme.repo_context import build_repo_context
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        (repo / "AGENTS.md").write_text("Règle : 4 espaces.", encoding="utf-8")
        (repo / "docs").mkdir()
        (repo / "docs" / "style.md").write_text("...", encoding="utf-8")

        spec = _spec("tech", context_read=("AGENTS.md", "docs/"))
        bloc = build_repo_context(spec, str(repo), LOGGER)
        assert "Règle : 4 espaces." in bloc          # fichier : contenu injecté
        assert "`docs/style.md`" in bloc             # dossier : inventaire seulement
        assert "..." not in bloc.replace("Règle : 4 espaces.", "")


def test_context_read_signale_un_chemin_mort():
    """Le gain du déclaratif : un chemin obsolète se voit au lieu de priver le reviewer."""
    import logging as _logging

    from reviewme.repo_context import build_repo_context

    captured = []

    class _Catch(_logging.Handler):
        def emit(self, record):
            captured.append(record.getMessage())

    logger = _logging.getLogger("test-mort")
    logger.addHandler(_Catch())
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "AGENTS.md").write_text("x", encoding="utf-8")
        build_repo_context(_spec("tech", context_read=("AGENTS.md", "PARTI.md")), tmp, logger)
    assert any("PARTI.md" in m and "introuvable" in m for m in captured)


def test_context_read_refuse_de_sortir_du_depot():
    from reviewme.repo_context import build_repo_context
    with tempfile.TemporaryDirectory() as tmp:
        spec = _spec("tech", context_read=("../../etc/passwd", "/etc/hosts"))
        assert build_repo_context(spec, tmp, LOGGER) == ""


def test_sans_context_read_aucun_bloc():
    from reviewme.repo_context import build_repo_context
    with tempfile.TemporaryDirectory() as tmp:
        assert build_repo_context(_spec("tech"), tmp, LOGGER) == ""


# --------------------------------------------------------------- dépôt externe (D12)

def test_config_home_externe_prime_sur_le_core():
    """Un dépôt de config à part (un dépôt par organisation) sans forker le moteur."""
    import reviewme.projects as P
    original = os.environ.pop("REVIEWME_CONFIG_HOME", None)
    try:
        assert P.projects_dir() == P.PROJECTS_DIR              # défaut : dans le repo du core
        os.environ["REVIEWME_CONFIG_HOME"] = "/tmp/mon-instance"
        assert P.projects_dir() == Path("/tmp/mon-instance/projects")
        # la Config prime sur l'environnement (utile en test et en multi-instance)
        assert P.projects_dir(_config(config_home="/autre/depot")) == Path("/autre/depot/projects")
    finally:
        os.environ.pop("REVIEWME_CONFIG_HOME", None)
        if original is not None:
            os.environ["REVIEWME_CONFIG_HOME"] = original


def test_config_home_vide_retombe_sur_le_core():
    import reviewme.projects as P
    original = os.environ.pop("REVIEWME_CONFIG_HOME", None)
    try:
        os.environ["REVIEWME_CONFIG_HOME"] = "   "            # variable posée mais vide
        assert P.projects_dir() == P.PROJECTS_DIR
    finally:
        os.environ.pop("REVIEWME_CONFIG_HOME", None)
        if original is not None:
            os.environ["REVIEWME_CONFIG_HOME"] = original


# --------------------------------------------------------------- config projet (D1)

def _write_project(root: Path, name: str, reviewers: dict[str, tuple[str, str]]):
    """Un projet = un dossier `reviewers/` ; chaque sous-dossier EST un reviewer."""
    pdir = root / name
    (pdir / "reviewers").mkdir(parents=True)
    for rid, (toml, system) in reviewers.items():
        rdir = pdir / "reviewers" / rid
        rdir.mkdir()
        (rdir / "reviewer.toml").write_text(toml, encoding="utf-8")
        if system is not None:
            (rdir / "system.md").write_text(system, encoding="utf-8")
    return pdir


def test_chargement_projet_et_erreurs_explicites():
    import reviewme.projects as P
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        original = P.PROJECTS_DIR
        P.PROJECTS_DIR = root
        try:
            # les reviewers sont DÉDUITS des dossiers : rien à déclarer ailleurs
            _write_project(root, "ok-repo", {
                "tech": ('output_mode = "inline"\npriority = 5\n', "persona"),
                "i18n": ('output_mode = "mixed"\n', "persona i18n"),
            })
            project = load_project("ok-repo")
            assert sorted(s.id for s in project.reviewers) == ["i18n", "tech"]
            assert project.reviewer("tech").priority == 5

            # `enabled = false` met en sommeil sans supprimer le dossier
            _write_project(root, "dormant", {
                "tech": ("", "persona"),
                "i18n": ("enabled = false\n", "persona i18n"),
            })
            assert [s.id for s in load_project("dormant").reviewers] == ["tech"]

            _write_project(root, "bad-mode", {"tech": ('output_mode = "carrier-pigeon"\n', "persona")})
            _expect_error(load_project, "bad-mode", "output_mode")

            _write_project(root, "no-persona", {"tech": ("", None)})
            _expect_error(load_project, "no-persona", "system.md")

            _write_project(root, "vide", {})
            _expect_error(load_project, "vide", "aucun reviewer")

            _write_project(root, "tous-eteints", {"tech": ("enabled = false\n", "persona")})
            _expect_error(load_project, "tous-eteints", "désactivés")

            (root / "sans-dossier-reviewers").mkdir()
            (root / "sans-dossier-reviewers" / "common").mkdir()
            _expect_error(load_project, "sans-dossier-reviewers", "`reviewers/` manquant")

            # dossier vide = submodule git non initialisé : le message doit le dire
            (root / "submodule-vide").mkdir()
            _expect_error(load_project, "submodule-vide", "git submodule update --init")

            _expect_error(load_project, "jamais-cree", "introuvable")
        finally:
            P.PROJECTS_DIR = original


def _expect_error(fn, arg, needle):
    try:
        fn(arg)
    except ProjectConfigError as e:
        assert needle in str(e), f"message peu clair pour '{needle}' : {e}"
    else:
        raise AssertionError(f"ProjectConfigError attendue pour {arg}")


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
