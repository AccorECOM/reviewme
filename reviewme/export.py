"""`reviewme export --since 7d --out DIR` : l'artefact de fin de période, en deux sorties.

- **Machine** : `reviewme-artifact-<date>.tar.gz` — `findings.jsonl`, `runs.jsonl`,
  `enrich.jsonl`, `prs.jsonl` (filtrés sur la fenêtre), `config-snapshot/`, `stats.json`,
  `quality.json` (le bloc qualité déjà calculé) et `SCHEMA.md`, qui décrit chaque champ.
  C'est ce qu'on donne à une analyse pour juger les réglages.
- **Humain** : `rapport-semaine.md`, lisible tel quel dans GitHub ou Slack — les stats
  classiques, puis des stats « fun » qui entretiennent la boucle 👍/👎.

Le rapport ne nomme JAMAIS un développeur à propos d'une remarque négative : les seuls
classements de personnes portent sur la participation.
"""
from __future__ import annotations

import json
import shutil
import tarfile
import tempfile
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .config import ROOT_DIR
from .enrich import ENRICH_FILE, PRS_FILE
from .ledger import FINDINGS_FILE, RUNS_FILE, SNAPSHOT_DIR, read_jsonl
from .quality import compute, latest
from .stats import STATS_FILE

SCHEMA_DOC = ROOT_DIR / "docs" / "registre-schema.md"


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{round(x * 100)} %"


def _window(rows: list[dict], limite: str) -> list[dict]:
    return [r for r in rows if r.get("ts", "") >= limite]


def load(root: Path, since: timedelta | None = None) -> dict[str, list[dict]]:
    limite = (datetime.now(UTC) - since).isoformat(timespec="seconds") if since else ""
    return {name: _window(read_jsonl(root / name), limite)
            for name in (FINDINGS_FILE, RUNS_FILE, ENRICH_FILE, PRS_FILE)}


def quality_of(data: dict[str, list[dict]]) -> dict:
    return compute(data[FINDINGS_FILE], data[RUNS_FILE], data[ENRICH_FILE], data[PRS_FILE])


# --------------------------------------------------------------------------- rapport

def report(data: dict[str, list[dict]], q: dict, repo: str = "", since_label: str = "") -> str:
    findings, runs, enrich = data[FINDINGS_FILE], data[RUNS_FILE], data[ENRICH_FILE]
    live_runs = [r for r in runs if r.get("variant", "live") == "live"]
    prs = {r.get("pr") for r in live_runs}
    cost = sum(r.get("cost_usd", 0) or 0 for r in live_runs)
    by_pr = {k: v for k, v in q["cost_by_pr"].items() if k.startswith("live:")}
    durees = [v["duration_s"] for v in by_pr.values()]
    judged = q["_judged"]
    reactions = Counter(r.get("content") for e in latest(enrich, "comment_id").values()
                        for r in e.get("reactions", []))

    L = [f"# ReviewMe — rapport{(' ' + since_label) if since_label else ''}", ""]
    L += ["## Stats classiques", "",
          f"- **PR relues** : {len(prs)} ({len(live_runs)} passages de relecteur)",
          f"- **Remarques postées** : {len(judged)} — suivies d'effet "
          f"{sum(j['followed'] for j in judged)}, 👍 {reactions['+1']}, 👎 {reactions['-1']}, "
          f"contestées {sum(j['contested'] for j in judged)}",
          f"- **Coût** : {cost:.2f} $ au total, "
          f"{(cost / len(prs)) if prs else 0:.2f} $ par PR",
          f"- **Durée moyenne** d'une PR : {(sum(durees) / len(durees)) if durees else 0:.0f} s",
          ""]

    L += ["| Relecteur | Postées | Suivies d'effet | Approuvées | Contestées |",
          "|---|---:|---:|---:|---:|"]
    for rid, t in q["by_reviewer"].items():
        L.append(f"| {rid} | {t['posted']} | {t['followed']} ({_pct(t['followed_rate'])}) | "
                 f"{t['approved']} | {t['contested']} |")
    L += ["", "| Sévérité | Postées | Suivies d'effet |", "|---|---:|---:|"]
    for sev, t in q["by_severity"].items():
        L.append(f"| {sev} | {t['posted']} | {t['followed']} ({_pct(t['followed_rate'])}) |")

    L += ["", "**Calibration** (tous les findings, postés ou non) :", "",
          "| Confiance | Findings | Postés | Écartés sous le seuil | Suivies d'effet |",
          "|---|---:|---:|---:|---:|"]
    for b, c in q["calibration"].items():
        L.append(f"| {b} | {c['findings']} | {c['fates'].get('posted', 0)} | "
                 f"{c['fates'].get('dropped_low', 0)} | {c['followed']} |")

    m = q["misses"]
    L += ["", f"**Manqués approchés** : {m['on_silent_lines']} commentaire(s) humain(s) là où "
          f"le bot n'a rien dit, {m['on_dropped_findings']} là où il avait un finding écarté "
          f"(sur {m['human_comments']} commentaires humains)."]

    if q["baseline_vs_live"]:
        L += ["", "**Témoin contre live** (mêmes PR, mêmes commits) :", ""]
        for rid, v in sorted(q["baseline_vs_live"].items()):
            L.append(f"- {rid} : témoin {v['baseline']}, live {v['live']}, communes "
                     f"{v['both']} (sur {v['prs']} PR)")

    if q["contested_rules"]:
        L += ["", "**Règles à revoir** (≥ 3 contestations) :", ""]
        L += [f"- `{r['rule']}` : {r['contested']} contestation(s) sur {r['posted']} remarque(s)"
              for r in q["contested_rules"]]

    L += ["", "## Stats fun", ""]
    L += _fun(q, runs, enrich, findings, repo)
    return "\n".join(L) + "\n"


def _fun(q: dict, runs: list[dict], enrich: list[dict], findings: list[dict],
         repo: str) -> list[str]:
    out = []
    rules = [(k, v) for k, v in q["by_rule"].items() if v["posted"]]
    if rules:
        best = max(rules, key=lambda kv: (kv[1]["approved"], kv[1]["followed"]))
        if best[1]["approved"]:
            out.append(f"- 🏅 **Règle la plus approuvée** : `{best[0]}` "
                       f"({best[1]['approved']} approbation(s))")
        worst = max(rules, key=lambda kv: kv[1]["contested"])
        if worst[1]["contested"]:
            out.append(f"- 🤔 **Règle la plus contestée** : `{worst[0]}` "
                       f"({worst[1]['contested']} contestation(s))")
    useful = [(k, v) for k, v in q["by_reviewer"].items() if v["posted"] >= 3]
    if useful:
        k, v = max(useful, key=lambda kv: kv[1]["followed_rate"] or 0)
        out.append(f"- 🎯 **Relecteur le plus utile** : {k} "
                   f"({_pct(v['followed_rate'])} de remarques suivies d'effet)")
    live = {k: v for k, v in q["cost_by_pr"].items() if k.startswith("live:")}
    if live:
        k, v = max(live.items(), key=lambda kv: (kv[1]["turns"], kv[1]["cost_usd"]))
        out.append(f"- 🏋️ **PR qui a fait le plus travailler le bot** : #{k.split(':')[1]} "
                   f"({v['turns']} tours, {v['cost_usd']:.2f} $)")
        k, v = max(live.items(), key=lambda kv: kv[1]["diff_lines"])
        out.append(f"- 📏 **Plus gros diff relu** : #{k.split(':')[1]} "
                   f"({v['diff_lines']} lignes)")
    saves = [j for j in q["_judged"] if j.get("severity") == "BLOCKER" and j["followed"]]
    if saves:
        s = saves[0]
        lien = (f"https://github.com/{repo}/pull/{s['pr']}#discussion_r{s['comment_id']}"
                if repo else f"PR #{s['pr']}")
        out.append(f"- 🛟 **Sauvetage de la semaine** : un BLOCKER de {s.get('reviewer')} "
                   f"suivi d'effet — {lien}")
    # Participation uniquement : jamais de classement sur le contenu des réponses.
    actifs = Counter()
    for e in latest(enrich, "comment_id").values():
        for r in e.get("reactions", []):
            actifs[r.get("user")] += 1
        for r in e.get("replies", []):
            actifs[r.get("user")] += 1
    actifs.pop("", None)
    if actifs:
        top = ", ".join(f"{u} ({n})" for u, n in actifs.most_common(3))
        out.append(f"- 🙌 **Ils font vivre la boucle 👍/👎** : {top}")
    evites = sum(1 for j in q["_judged"] if j["followed"]
                 and not (latest(enrich, "comment_id").get(j["comment_id"]) or {})
                 .get("human_comments_nearby"))
    if evites:
        out.append(f"- ✋ **Remarques traitées sans qu'un humain ait eu à les écrire** : "
                   f"{evites}")
    return out or ["- Pas encore assez de retours pour des stats fun."]


# --------------------------------------------------------------------------- archive

def export(root: Path, out_dir: Path, since: timedelta, *, repo: str = "",
           since_label: str = "", stats_file: Path | None = None) -> tuple[Path, Path]:
    """Écrit l'archive machine et le rapport humain. Renvoie (archive, rapport)."""
    data = load(root, since)
    q = quality_of(data)
    rapport = report(data, q, repo, since_label)
    out_dir.mkdir(parents=True, exist_ok=True)
    date = datetime.now(UTC).strftime("%Y-%m-%d")

    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp) / f"reviewme-artifact-{date}"
        staging.mkdir()
        for name, rows in data.items():
            (staging / name).write_text(
                "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                encoding="utf-8")
        hashes = {r.get("config_hash") for r in data[FINDINGS_FILE] + data[RUNS_FILE]}
        for h in sorted(x for x in hashes if x):
            src = root / SNAPSHOT_DIR / h
            if src.is_dir():
                shutil.copytree(src, staging / SNAPSHOT_DIR / h)
        stats = stats_file or STATS_FILE
        if stats.is_file():
            shutil.copy(stats, staging / "stats.json")
        public = {k: v for k, v in q.items() if not k.startswith("_")}
        (staging / "quality.json").write_text(json.dumps(public, ensure_ascii=False, indent=1),
                                              encoding="utf-8")
        if SCHEMA_DOC.is_file():
            shutil.copy(SCHEMA_DOC, staging / "SCHEMA.md")
        (staging / "rapport-semaine.md").write_text(rapport, encoding="utf-8")

        archive = out_dir / f"reviewme-artifact-{date}.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(staging, arcname=staging.name)

    rapport_path = out_dir / "rapport-semaine.md"
    rapport_path.write_text(rapport, encoding="utf-8")
    return archive, rapport_path
