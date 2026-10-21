"""`reviewme export --since 7d --out DIR`: the end-of-period artifact, in two outputs.

- **Machine**: `reviewme-artifact-<date>.tar.gz` — `findings.jsonl`, `runs.jsonl`,
  `enrich.jsonl`, `prs.jsonl` (filtered on the window), `config-snapshot/`, `stats.json`,
  `quality.json` (the already-computed quality block) and `SCHEMA.md`, which describes
  every field. This is what an analysis is given to judge the settings.
- **Human**: `weekly-report.md`, readable as-is in GitHub or Slack — the usual stats,
  then "fun" stats that keep the 👍/👎 loop alive.

The report NEVER names a developer in connection with a negative finding: the only
rankings of people are about participation.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import tarfile
import tempfile
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .config import ROOT_DIR
from .enrich import ENRICH_FILE, PRS_FILE
from .ledger import FINDINGS_FILE, RUNS_FILE, SNAPSHOT_DIR, read_all
from .quality import compute, latest
from .stats import STATS_FILE

SCHEMA_DOC = ROOT_DIR / "docs" / "ledger-schema.md"
REPORT_FILE = "weekly-report.md"


_LOGIN_KEYS = ("user", "author", "resolved_by")


def _alias(login):
    if not login:
        return login
    return "u-" + hashlib.sha256(str(login).encode()).hexdigest()[:8]


def pseudonymize(value):
    """Replace logins with a stable alias (`u-1a2b3c4d`) in the machine archive.

    The analysis needs to know that two reactions come from the same person, and whether
    it is the PR author (`role`), not WHO contests what. The human report only names
    people for participation.
    """
    if isinstance(value, list):
        return [pseudonymize(v) for v in value]
    if isinstance(value, dict):
        return {k: (_alias(v) if k in _LOGIN_KEYS and isinstance(v, str) else pseudonymize(v))
                for k, v in value.items()}
    return value


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{round(x * 100)} %"


def _window(rows: list[dict], limite: str) -> list[dict]:
    return [r for r in rows if r.get("ts", "") >= limite]


def load(root: Path, since: timedelta | None = None) -> dict[str, list[dict]]:
    limite = (datetime.now(UTC) - since).isoformat(timespec="seconds") if since else ""
    return {name: _window(read_all(root, name), limite)
            for name in (FINDINGS_FILE, RUNS_FILE, ENRICH_FILE, PRS_FILE)}


def quality_of(data: dict[str, list[dict]]) -> dict:
    return compute(data[FINDINGS_FILE], data[RUNS_FILE], data[ENRICH_FILE], data[PRS_FILE])


# --------------------------------------------------------------------------- report

#: Texts of the human report, in the project's language (`[review] language`).
_R = {
    "en": {
        "title": "# ReviewMe — report{since}", "classic": "## Usual stats",
        "prs": "- **PRs reviewed**: {prs} ({runs} reviewer runs)",
        "posted": "- **Findings posted**: {n} — acted upon {followed}, 👍 {up}, 👎 {down}, "
                  "contested {contested}",
        "cost": "- **Cost**: {total:.2f} $ in total, {per_pr:.2f} $ per PR",
        "duration": "- **Average duration** of a PR: {s:.0f} s",
        "t_reviewer": "| Reviewer | Posted | Acted upon | Approved | Contested |",
        "t_severity": "| Severity | Posted | Acted upon |",
        "calibration": "**Calibration** (every finding, posted or not):",
        "t_calib": "| Confidence | Findings | Posted | Dropped below threshold | Acted upon |",
        "misses": "**Approximate misses**: {silent} human comment(s) where the bot said "
                  "nothing, {dropped} where it had a dropped finding (out of {total} human "
                  "comments).",
        "baseline": "**Baseline vs live** (same PRs, same commits):",
        "baseline_row": "- {rid}: baseline {b}, live {l}, common {both} (over {prs} PRs)",
        "rules": "**Rules to review** (≥ 3 contestations):",
        "rule_row": "- `{rule}`: {c} contestation(s) out of {p} finding(s)",
        "fun": "## Fun stats",
        "best_rule": "- 🏅 **Most approved rule**: `{rule}` ({n} approval(s))",
        "worst_rule": "- 🤔 **Most contested rule**: `{rule}` ({n} contestation(s))",
        "useful": "- 🎯 **Most useful reviewer**: {rid} ({pct} of findings acted upon)",
        "busiest": "- 🏋️ **PR that kept the bot busiest**: #{pr} ({turns} turns, {cost:.2f} $)",
        "biggest": "- 📏 **Biggest diff reviewed**: #{pr} ({lines} lines)",
        "save": "- 🛟 **Save of the week**: a BLOCKER from {rid} acted upon — {link}",
        "loop": "- 🙌 **Keeping the 👍/👎 loop alive**: {top}",
        "avoided": "- ✋ **Findings handled without a human having to write them**: {n}",
        "empty": "- Not enough feedback yet for fun stats.",
    },
    "fr": {
        "title": "# ReviewMe — rapport{since}", "classic": "## Stats classiques",
        "prs": "- **PR relues** : {prs} ({runs} passages de relecteur)",
        "posted": "- **Remarques postées** : {n} — suivies d'effet {followed}, 👍 {up}, "
                  "👎 {down}, contestées {contested}",
        "cost": "- **Coût** : {total:.2f} $ au total, {per_pr:.2f} $ par PR",
        "duration": "- **Durée moyenne** d'une PR : {s:.0f} s",
        "t_reviewer": "| Relecteur | Postées | Suivies d'effet | Approuvées | Contestées |",
        "t_severity": "| Sévérité | Postées | Suivies d'effet |",
        "calibration": "**Calibration** (tous les findings, postés ou non) :",
        "t_calib": "| Confiance | Findings | Postés | Écartés sous le seuil | Suivies d'effet |",
        "misses": "**Manqués approchés** : {silent} commentaire(s) humain(s) là où le bot n'a "
                  "rien dit, {dropped} là où il avait un finding écarté (sur {total} "
                  "commentaires humains).",
        "baseline": "**Témoin contre live** (mêmes PR, mêmes commits) :",
        "baseline_row": "- {rid} : témoin {b}, live {l}, communes {both} (sur {prs} PR)",
        "rules": "**Règles à revoir** (≥ 3 contestations) :",
        "rule_row": "- `{rule}` : {c} contestation(s) sur {p} remarque(s)",
        "fun": "## Stats fun",
        "best_rule": "- 🏅 **Règle la plus approuvée** : `{rule}` ({n} approbation(s))",
        "worst_rule": "- 🤔 **Règle la plus contestée** : `{rule}` ({n} contestation(s))",
        "useful": "- 🎯 **Relecteur le plus utile** : {rid} ({pct} de remarques suivies "
                  "d'effet)",
        "busiest": "- 🏋️ **PR qui a fait le plus travailler le bot** : #{pr} ({turns} tours, "
                   "{cost:.2f} $)",
        "biggest": "- 📏 **Plus gros diff relu** : #{pr} ({lines} lignes)",
        "save": "- 🛟 **Sauvetage de la semaine** : un BLOCKER de {rid} suivi d'effet — {link}",
        "loop": "- 🙌 **Ils font vivre la boucle 👍/👎** : {top}",
        "avoided": "- ✋ **Remarques traitées sans qu'un humain ait eu à les écrire** : {n}",
        "empty": "- Pas encore assez de retours pour des stats fun.",
    },
}


def report(data: dict[str, list[dict]], q: dict, repo: str = "", since_label: str = "",
           language: str = "en") -> str:
    R = _R.get(language, _R["en"])
    findings, runs, enrich = data[FINDINGS_FILE], data[RUNS_FILE], data[ENRICH_FILE]
    live_runs = [r for r in runs if r.get("variant", "live") == "live"]
    prs = {r.get("pr") for r in live_runs}
    cost = sum(r.get("cost_usd", 0) or 0 for r in live_runs)
    by_pr = {k: v for k, v in q["cost_by_pr"].items() if k.startswith("live:")}
    durees = [v["duration_s"] for v in by_pr.values()]
    judged = q["_judged"]
    reactions = Counter(r.get("content") for e in latest(enrich, "comment_id").values()
                        for r in e.get("reactions", []))

    L = [R["title"].format(since=(" " + since_label) if since_label else ""), ""]
    L += [R["classic"], "",
          R["prs"].format(prs=len(prs), runs=len(live_runs)),
          R["posted"].format(n=len(judged), followed=sum(j["followed"] for j in judged),
                             up=reactions["+1"], down=reactions["-1"],
                             contested=sum(j["contested"] for j in judged)),
          R["cost"].format(total=cost, per_pr=(cost / len(prs)) if prs else 0),
          R["duration"].format(s=(sum(durees) / len(durees)) if durees else 0),
          ""]

    L += [R["t_reviewer"], "|---|---:|---:|---:|---:|"]
    for rid, t in q["by_reviewer"].items():
        L.append(f"| {rid} | {t['posted']} | {t['followed']} ({_pct(t['followed_rate'])}) | "
                 f"{t['approved']} | {t['contested']} |")
    L += ["", R["t_severity"], "|---|---:|---:|"]
    for sev, t in q["by_severity"].items():
        L.append(f"| {sev} | {t['posted']} | {t['followed']} ({_pct(t['followed_rate'])}) |")

    L += ["", R["calibration"], "", R["t_calib"], "|---|---:|---:|---:|---:|"]
    for b, c in q["calibration"].items():
        L.append(f"| {b} | {c['findings']} | {c['fates'].get('posted', 0)} | "
                 f"{c['fates'].get('dropped_low', 0)} | {c['followed']} |")

    m = q["misses"]
    L += ["", R["misses"].format(silent=m["on_silent_lines"], dropped=m["on_dropped_findings"],
                                 total=m["human_comments"])]

    if q["baseline_vs_live"]:
        L += ["", R["baseline"], ""]
        for rid, v in sorted(q["baseline_vs_live"].items()):
            L.append(R["baseline_row"].format(rid=rid, b=v["baseline"], l=v["live"],
                                              both=v["both"], prs=v["prs"]))

    if q["contested_rules"]:
        L += ["", R["rules"], ""]
        L += [R["rule_row"].format(rule=r["rule"], c=r["contested"], p=r["posted"])
              for r in q["contested_rules"]]

    L += ["", R["fun"], ""]
    L += _fun(q, runs, enrich, findings, repo, R)
    return "\n".join(L) + "\n"


def _fun(q: dict, runs: list[dict], enrich: list[dict], findings: list[dict],
         repo: str, R: dict) -> list[str]:
    out = []
    rules = [(k, v) for k, v in q["by_rule"].items() if v["posted"]]
    if rules:
        best = max(rules, key=lambda kv: (kv[1]["approved"], kv[1]["followed"]))
        if best[1]["approved"]:
            out.append(R["best_rule"].format(rule=best[0], n=best[1]["approved"]))
        worst = max(rules, key=lambda kv: kv[1]["contested"])
        if worst[1]["contested"]:
            out.append(R["worst_rule"].format(rule=worst[0], n=worst[1]["contested"]))
    useful = [(k, v) for k, v in q["by_reviewer"].items() if v["posted"] >= 3]
    if useful:
        k, v = max(useful, key=lambda kv: kv[1]["followed_rate"] or 0)
        out.append(R["useful"].format(rid=k, pct=_pct(v["followed_rate"])))
    live = {k: v for k, v in q["cost_by_pr"].items() if k.startswith("live:")}
    if live:
        k, v = max(live.items(), key=lambda kv: (kv[1]["turns"], kv[1]["cost_usd"]))
        out.append(R["busiest"].format(pr=k.split(":")[1], turns=v["turns"], cost=v["cost_usd"]))
        k, v = max(live.items(), key=lambda kv: kv[1]["diff_lines"])
        out.append(R["biggest"].format(pr=k.split(":")[1], lines=v["diff_lines"]))
    saves = [j for j in q["_judged"] if j.get("severity") == "BLOCKER" and j["followed"]]
    if saves:
        s = saves[0]
        lien = (f"https://github.com/{repo}/pull/{s['pr']}#discussion_r{s['comment_id']}"
                if repo else f"PR #{s['pr']}")
        out.append(R["save"].format(rid=s.get("reviewer"), link=lien))
    # Participation only: never a ranking based on the content of replies.
    actifs = Counter()
    for e in latest(enrich, "comment_id").values():
        for r in e.get("reactions", []):
            actifs[r.get("user")] += 1
        for r in e.get("replies", []):
            actifs[r.get("user")] += 1
    actifs.pop("", None)
    if actifs:
        out.append(R["loop"].format(top=", ".join(f"{u} ({n})" for u, n in actifs.most_common(3))))
    evites = sum(1 for j in q["_judged"] if j["followed"]
                 and not (latest(enrich, "comment_id").get(j["comment_id"]) or {})
                 .get("human_comments_nearby"))
    if evites:
        out.append(R["avoided"].format(n=evites))
    return out or [R["empty"]]


# --------------------------------------------------------------------------- archive

def export(root: Path, out_dir: Path, since: timedelta, *, repo: str = "",
           since_label: str = "", stats_file: Path | None = None,
           language: str = "en") -> tuple[Path, Path]:
    """Write the machine archive and the human report. Returns (archive, report)."""
    data = load(root, since)
    q = quality_of(data)
    rapport = report(data, q, repo, since_label, language)
    out_dir.mkdir(parents=True, exist_ok=True)
    date = datetime.now(UTC).strftime("%Y-%m-%d")

    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp) / f"reviewme-artifact-{date}"
        staging.mkdir()
        for name, rows in data.items():
            (staging / name).write_text(
                "".join(json.dumps(pseudonymize(r), ensure_ascii=False) + "\n" for r in rows),
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
        (staging / REPORT_FILE).write_text(rapport, encoding="utf-8")

        archive = out_dir / f"reviewme-artifact-{date}.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(staging, arcname=staging.name)

    rapport_path = out_dir / REPORT_FILE
    rapport_path.write_text(rapport, encoding="utf-8")
    return archive, rapport_path
