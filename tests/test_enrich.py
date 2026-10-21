"""Tests de l'enrichissement (`enrich.py`), du bloc qualité et de l'export.

Ce qui compte : distinguer une remarque suivie d'effet d'une remarque ignorée, une
réponse qui conteste d'une réponse qui approuve, et compter les commentaires humains là
où le bot s'est tu — sans jamais nommer un développeur pour une remarque négative.

Exécution : `pytest tests/`.
"""
import logging
import os
import sys
import tarfile
import tempfile
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reviewme import enrich as en
from reviewme import export as ex
from reviewme.ledger import FINDINGS_FILE, RUNS_FILE, write_part
from reviewme.quality import band, compute
from reviewme.reconciler import fingerprint_hash

LOGGER = logging.getLogger("test-enrich")
LOGGER.addHandler(logging.NullHandler())
TS = "2099-01-01T00:00:00+00:00"           # dans toute fenêtre glissante


def _row(cid, line, fate="posted", conf=90, rule="r1", sev="IMPORTANT", content=None,
         reviewer="tech", variant="live", head="h1", dry=False):
    fp = fingerprint_hash("a.swift", content) if content else ""
    return {"schema_version": 1, "run_id": "x", "ts": TS, "repo": "o/r", "pr": 7,
            "head_sha": head, "variant": variant, "reviewer": reviewer, "fingerprint": fp,
            "path": "a.swift", "line": line, "severity": sev, "confidence": conf,
            "rule_id": rule, "fate": fate, "comment_id": cid, "in_summary": False,
            "dry_run": dry}


class _GH:
    def __init__(self, file_content, comments, reactions=None, threads=None, perm="write"):
        self.file_content, self.comments = file_content, comments
        self.reactions, self.threads, self.perm = reactions or {}, threads or [], perm

    def get_pr(self, n):
        return {"number": n, "head": {"sha": "h2"}, "user": {"login": "auteur"},
                "state": "closed", "merged_at": "2099-01-02T00:00:00Z"}

    def list_review_comments(self, n):
        return self.comments

    def review_threads(self, n):
        return self.threads

    def list_comment_reactions(self, cid):
        return self.reactions.get(cid, [])

    def list_pr_commits(self, n):
        return [{"sha": "h1"}, {"sha": "h2"}]

    def get_file_at(self, path, ref):
        return self.file_content

    def get_permission(self, login):
        return self.perm


def _bot(cid, line):
    return {"id": cid, "path": "a.swift", "line": line, "in_reply_to_id": None,
            "body": f"**[IMPORTANT]** x\n<!-- reviewme:tech:{'a' * 10} -->",
            "user": {"login": "bot"}}


def _human(cid, line, body, parent=None, user="dev"):
    return {"id": cid, "path": "a.swift", "line": line, "in_reply_to_id": parent,
            "body": body, "user": {"login": user}, "created_at": "2099-01-01T01:00:00Z"}


def test_negation_ne_fait_pas_une_etiquette():
    assert en.label_reply("pas corrigé pour l'instant") == "other"
    assert en.label_reply("pas faux, je change") == "other"
    assert en.label_reply("not fixed yet") == "other"
    assert en.label_reply("Ce n'est pas le cas ici") == "false_positive"


def test_fichier_supprime_n_est_pas_un_suivi_d_effet():
    """Un 404 (fichier supprimé ou renommé) ne prouve pas que la remarque a été traitée."""
    rows = [_row(1, 10, content="let x = y!")]
    gh = _GH(None, [_bot(1, 10)])
    (line,), _ = en.enrich_pr(gh, 7, rows, {}, with_text=False, logger=LOGGER)
    assert line["fingerprint_gone_at"] is None and line["file_gone_at"] == "h2"


def test_commentaire_d_un_autre_bot_n_est_pas_un_manque():
    rows = [_row(1, 10, content="let x = y!")]
    autre = _human(8, 90, "Sonar : complexité")
    autre["user"] = {"login": "sonarcloud[bot]", "type": "Bot"}
    _, pr = en.enrich_pr(_GH("", [_bot(1, 10), autre]), 7, rows, {}, with_text=False,
                         logger=LOGGER)
    assert pr["human_comments"] == 0 and pr["human_comments_on_silent_lines"] == 0


def test_etiquettes_de_reponse():
    assert en.label_reply("Faux positif, ce n'est pas nil ici") == "false_positive"
    assert en.label_reply("C'est volontaire, on garde") == "intended"
    assert en.label_reply("Corrigé dans le commit suivant") == "fixed"
    assert en.label_reply("nit, plus tard") == "unimportant"
    assert en.label_reply("Tu peux préciser ?") == "question"
    assert en.label_reply("ok") == "other"


def test_fenetre():
    assert en.parse_since("14d") == timedelta(days=14)
    assert en.parse_since("36h") == timedelta(hours=36)


def _enrich_one(with_text=False):
    rows = [_row(1, 10, content="let x = y!"), _row(2, 20, content="let z = 1"),
            _row(None, 40, fate="dropped_low", conf=40, content="let w = 2")]
    comments = [
        _bot(1, 10), _bot(2, 20),
        _human(3, 10, "Corrigé, merci", parent=1),
        _human(4, 20, "Faux positif : c'est voulu", parent=2, user="auteur"),
        _human(5, 41, "Ici il manque un weak self"),          # près d'un finding écarté
        _human(6, 90, "Nommage ?"),                            # là où le bot n'a rien dit
        _human(7, 21, "@reviewme remember Pas de force unwrap dans les tests", parent=2),
    ]
    gh = _GH("let z = 1\nlet w = 2\n", comments,
             reactions={1: [{"content": "+1", "user": {"login": "dev"}}],
                        2: [{"content": "-1", "user": {"login": "auteur"}}]},
             threads=[{"isResolved": True, "resolvedBy": {"login": "dev"},
                       "comments": {"nodes": [{"databaseId": 1}]}}])
    return en.enrich_pr(gh, 7, rows, {}, with_text=with_text, logger=LOGGER)


def test_suivi_d_effet_et_reactions():
    lines, _ = _enrich_one()
    by = {line["comment_id"]: line for line in lines}
    assert by[1]["fingerprint_gone_at"] == "h2"          # `let x = y!` a disparu
    assert by[2]["fingerprint_gone_at"] is None          # `let z = 1` est toujours là
    assert by[1]["reactions"] == [{"content": "+1", "user": "dev", "role": "other"}]
    assert by[2]["reactions"][0]["role"] == "pr_author"
    assert by[1]["thread_resolved"] is True and by[1]["resolved_by"] == "dev"
    assert by[2]["replies"][0]["contests"] is True
    # Sans `include_text`, la règle elle-même n'est pas écrite : c'est du texte de PR.
    assert by[2]["replies"][1]["remember"] == {"trusted": True}
    assert "text" not in by[1]["replies"][0]


def test_par_pr_manques_et_cout_du_filtre():
    _, pr = _enrich_one()
    assert pr["state"] == "merged" and pr["commits_after_first_review"] == 1
    assert pr["human_comments"] == 2
    assert pr["human_comments_on_dropped_findings"] == 1
    assert pr["human_comments_on_silent_lines"] == 1
    assert "human_comments_text" not in pr


def test_textes_seulement_si_demande():
    lines, pr = _enrich_one(with_text=True)
    assert lines[0]["replies"][0]["text"] == "Corrigé, merci"
    assert lines[1]["replies"][1]["remember"]["rule"] == "Pas de force unwrap dans les tests"
    assert {c["text"] for c in pr["human_comments_text"]} >= {"Nommage ?"}


def test_qualite_et_calibration():
    lines, pr = _enrich_one()
    rows = [_row(1, 10, content="let x = y!"), _row(2, 20, content="let z = 1"),
            _row(None, 40, fate="dropped_low", conf=40, content="let w = 2")]
    q = compute(rows, [], lines, [pr])
    t = q["by_reviewer"]["tech"]
    assert t["posted"] == 2 and t["followed"] == 1 and t["contested"] == 1
    assert t["approved"] == 1
    assert q["calibration"]["0-50"]["fates"] == {"dropped_low": 1}
    assert q["calibration"]["86-100"]["followed"] == 1
    assert q["misses"]["on_dropped_findings"] == 1
    assert band(70) == "51-70" and band(71) == "71-85"


def test_global_et_repli_hors_du_denominateur():
    """Un finding délivré dans un commentaire global n'a pas de ligne à suivre."""
    rows = [_row(1, 10, content="a"), {**_row(55, 20, content="b"), "in_summary": True},
            {**_row(55, 30, content="c"), "in_summary": True}]
    q = compute(rows, [], [], [])
    assert q["by_reviewer"]["tech"]["posted"] == 1 and q["posted_in_summary"] == 2


def test_calibration_compte_un_finding_une_fois():
    """Re-produit à chaque push, un finding ne pèse qu'une fois dans la calibration."""
    rows = [{**_row(None, 40, fate="dropped_low", conf=40, content="a"), "run_id": str(i),
             "ts": f"2099-01-0{i + 1}T00:00:00+00:00"} for i in range(3)]
    assert compute(rows, [], [], [])["calibration"]["0-50"]["findings"] == 1


def test_question_n_approuve_pas():
    rows = [_row(1, 10, content="a")]
    enrich = [{"comment_id": 1, "reactions": [],
               "replies": [{"label": "question", "contests": False}]}]
    assert compute(rows, [], enrich, [])["by_reviewer"]["tech"]["approved"] == 0


def test_temoin_contre_live():
    rows = [_row(None, 10, content="a", dry=True), _row(None, 20, content="b", dry=True),
            _row(None, 10, content="a", variant="baseline", dry=True),
            _row(None, 30, content="c", variant="baseline", dry=True)]
    v = compute(rows, [], [], [])["baseline_vs_live"]["tech"]
    assert (v["baseline"], v["live"], v["both"], v["prs"]) == (2, 2, 1, 1)


def test_regle_contestee_trois_fois():
    rows = [_row(i, i, rule="r-bruyante") for i in range(1, 4)]
    enrich = [{"comment_id": i, "replies": [{"label": "false_positive", "contests": True}],
               "reactions": []} for i in range(1, 4)]
    q = compute(rows, [], enrich, [])
    assert q["contested_rules"][0]["rule"] == "tech:r-bruyante"


def test_export_archive_et_rapport():
    lines, pr = _enrich_one()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "registre"
        write_part(root, FINDINGS_FILE, [_row(1, 10, content="let x = y!", sev="BLOCKER"),
                                       _row(2, 20, content="let z = 1")])
        write_part(root, RUNS_FILE, [{"ts": TS, "pr": 7, "variant": "live", "reviewer": "tech",
                                    "cost_usd": 0.8, "duration_s": 60, "num_turns": 14,
                                    "diff_lines": 120}])
        write_part(root, en.ENRICH_FILE, lines)
        write_part(root, en.PRS_FILE, [pr])
        archive, rapport = ex.export(root, Path(tmp) / "out", timedelta(days=7), repo="o/r",
                                     stats_file=Path(tmp) / "absent.json")
        with tarfile.open(archive) as tar:
            noms = {Path(n).name for n in tar.getnames()}
            membre = next(m for m in tar.getmembers() if m.name.endswith("enrich.jsonl"))
            archive_text = tar.extractfile(membre).read().decode()
        texte = rapport.read_text()
    assert {"findings.jsonl", "runs.jsonl", "enrich.jsonl", "prs.jsonl", "quality.json",
            "SCHEMA.md", "rapport-semaine.md"} <= noms
    assert "## Stats classiques" in texte and "## Stats fun" in texte
    assert "Sauvetage" in texte and "discussion_r1" in texte
    # Participation uniquement : l'auteur qui a contesté n'est pas désigné comme tel.
    assert "auteur" not in texte.split("## Stats fun")[0]
    assert "dev" not in archive_text and "u-" in archive_text
