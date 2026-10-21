"""Bloc « qualité » : ce que valent les remarques, à partir du registre enrichi.

`stats.py` répond à « combien et pour quel coût ». Ce module répond à « est-ce utile » :

- **précision approchée** par reviewer et par `rule_id` : part des remarques postées
  suivies d'effet (la ligne a disparu), approuvées (👍, ou réponse sans contestation),
  contestées (👎, ou réponse « faux positif » / « voulu ») ;
- **calibration** par tranche de confiance : la même chose, pour dire si le seuil coûtait
  du rappel ;
- **manqués approchés** : commentaires humains là où le bot s'est tu, ou s'est auto-censuré ;
- **témoin contre live** sur les mêmes PR ;
- **coût et durée** par PR et par reviewer.

Calcul pur sur des listes de dictionnaires : aucun appel réseau, aucune écriture.
Les runs `dry_run` comptent pour la calibration (leurs findings sont réels) mais pas pour
la précision (rien n'a été posté, donc rien n'a pu être suivi).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime

BANDS = ((0, 50), (51, 70), (71, 85), (86, 100))
CONTESTED_RULE_MIN = 3       # à partir de 3 contestations, une règle est à revoir


def band(confidence: int) -> str:
    for lo, hi in BANDS:
        if lo <= (confidence or 0) <= hi:
            return f"{lo}-{hi}"
    return f"{BANDS[-1][0]}-{BANDS[-1][1]}"


def latest(rows: list[dict], key: str) -> dict:
    """Dernière ligne par clé (les fichiers sont en ajout seul : la plus récente fait foi)."""
    out: dict = {}
    for r in rows:
        if r.get(key) is not None:
            out[r[key]] = r
    return out


def _verdict(e: dict | None) -> dict:
    """Ce qu'il est advenu d'UNE remarque postée, en quatre booléens."""
    e = e or {}
    reactions = Counter(r.get("content") for r in e.get("reactions", []))
    replies = e.get("replies", [])
    contested = reactions["-1"] > 0 or any(r.get("contests") for r in replies)
    fixed_reply = any(r.get("label") == "fixed" for r in replies)
    return {
        "followed": bool(e.get("fingerprint_gone_at")) or fixed_reply,
        # Approuvée = signal POSITIF explicite (👍 ou « corrigé ») ; une question ou une
        # réponse neutre n'approuve rien.
        "approved": (reactions["+1"] > 0 or fixed_reply) and not contested,
        "contested": contested,
        "answered": bool(replies) or bool(reactions),
    }


def _rate(n: int, d: int) -> float | None:
    return round(n / d, 3) if d else None


def _tally(items: list[dict]) -> dict:
    n = len(items)
    t = {k: sum(1 for i in items if i[k]) for k in ("followed", "approved", "contested",
                                                    "answered")}
    return {"posted": n, **t, "followed_rate": _rate(t["followed"], n),
            "approved_rate": _rate(t["approved"], n), "contested_rate": _rate(t["contested"], n)}


def compute(findings: list[dict], runs: list[dict], enrich: list[dict],
            prs: list[dict]) -> dict:
    live = [f for f in findings if f.get("variant", "live") == "live"]
    last_enrich = latest(enrich, "comment_id")
    last_pr = latest(prs, "pr")

    # Une remarque postée EN LIGNE = un comment_id ; on garde sa première apparition. Les
    # findings délivrés dans un commentaire global (mode `global`, repli 422) n'ont pas de
    # ligne à suivre : comptés à part, jamais dans le dénominateur de précision.
    posted: dict = {}
    in_summary = 0
    for f in live:
        if f.get("fate") != "posted" or f.get("dry_run"):
            continue
        if f.get("in_summary"):
            in_summary += 1
        elif f.get("comment_id"):
            posted.setdefault(f["comment_id"], f)
    judged = [{**f, **_verdict(last_enrich.get(cid))} for cid, f in posted.items()]

    # Calibration : chaque finding UNE fois — sa première apparition sur la PR. Sans ça,
    # un finding re-produit à chaque push pèserait autant de fois qu'il y a de commits.
    first_seen: dict = {}
    for f in sorted(live, key=lambda f: f.get("ts", "")):
        key = (f.get("pr"), f.get("reviewer"),
               f.get("fingerprint") or f"{f.get('path')}:{f.get('line')}:{f.get('run_id')}")
        first_seen.setdefault(key, f)
    unique = list(first_seen.values())

    by_reviewer: dict[str, list] = defaultdict(list)
    by_rule: dict[str, list] = defaultdict(list)
    by_band: dict[str, list] = defaultdict(list)
    by_severity: dict[str, list] = defaultdict(list)
    for j in judged:
        by_reviewer[j.get("reviewer") or "?"].append(j)
        by_rule[f"{j.get('reviewer')}:{j.get('rule_id') or '-'}"].append(j)
        by_band[band(j.get("confidence", 0))].append(j)
        by_severity[j.get("severity") or "?"].append(j)

    # Calibration : TOUS les findings live (postés ou non), par tranche et par sort.
    calibration = {}
    for lo, hi in BANDS:
        key = f"{lo}-{hi}"
        in_band = [f for f in unique if band(f.get("confidence", 0)) == key]
        # En dry-run, « posted » veut dire « aurait été posté » : on le dit.
        fates = Counter("would_post" if f.get("dry_run") and f.get("fate") == "posted"
                        else f.get("fate") for f in in_band)
        calibration[key] = {"findings": len(in_band), "fates": dict(fates),
                            **{k: v for k, v in _tally(by_band.get(key, [])).items()}}

    contested_rules = sorted(
        ({"rule": k, "contested": v["contested"], "posted": v["posted"]}
         for k, v in ((k, _tally(v)) for k, v in by_rule.items())
         if v["contested"] >= CONTESTED_RULE_MIN),
        key=lambda r: -r["contested"])

    remembered = [
        {"pr": e.get("pr"), "comment_id": e.get("comment_id"), "user": r.get("user"),
         "rule": r["remember"].get("rule"), "trusted": r["remember"].get("trusted", False)}
        for e in last_enrich.values() for r in e.get("replies", []) if r.get("remember")]

    misses = {
        "human_comments": sum(p.get("human_comments", 0) for p in last_pr.values()),
        "on_silent_lines": sum(p.get("human_comments_on_silent_lines", 0)
                               for p in last_pr.values()),
        "on_dropped_findings": sum(p.get("human_comments_on_dropped_findings", 0)
                                   for p in last_pr.values()),
    }

    # Coût et durée : par PR (toutes variantes séparées) et par reviewer.
    cost_pr: dict = defaultdict(lambda: {"cost_usd": 0.0, "duration_s": 0.0, "turns": 0,
                                         "runs": 0, "diff_lines": 0})
    cost_reviewer: dict = defaultdict(lambda: {"cost_usd": 0.0, "duration_s": 0.0,
                                               "runs": 0, "turn_cap_hits": 0})
    for r in runs:
        k = f"{r.get('variant', 'live')}:{r.get('pr')}"
        c = cost_pr[k]
        c["cost_usd"] += r.get("cost_usd", 0) or 0
        c["duration_s"] = max(c["duration_s"], r.get("duration_s", 0) or 0)
        c["turns"] += r.get("num_turns", 0) or 0
        c["runs"] += 1
        c["diff_lines"] = max(c["diff_lines"], r.get("diff_lines", 0) or 0)
        cr = cost_reviewer[f"{r.get('variant', 'live')}:{r.get('reviewer')}"]
        cr["cost_usd"] += r.get("cost_usd", 0) or 0
        cr["duration_s"] += r.get("duration_s", 0) or 0
        cr["runs"] += 1
        cr["turn_cap_hits"] += 1 if r.get("subtype") == "error_max_turns" else 0
    for d in (*cost_pr.values(), *cost_reviewer.values()):
        d["cost_usd"] = round(d["cost_usd"], 4)

    # Témoin contre live : mêmes (PR, commit), par reviewer — combien chacun aurait posté,
    # et combien de remarques ne sont que d'un côté (par empreinte).
    def would_post(variant: str) -> dict:
        out: dict = defaultdict(set)
        for f in findings:
            if f.get("variant", "live") == variant and f.get("fate") in ("posted", "deduped",
                                                                         "capped"):
                out[(f.get("pr"), f.get("head_sha"), f.get("reviewer"))].add(
                    f.get("fingerprint") or f"{f.get('path')}:{f.get('line')}")
        return out
    base, cur = would_post("baseline"), would_post("live")
    common_runs = {(p, h) for p, h, _ in base} & {(p, h) for p, h, _ in cur}
    baseline_vs_live: dict = defaultdict(lambda: {"prs": set(), "baseline": 0, "live": 0,
                                                  "both": 0})
    for key in set(base) | set(cur):
        if key[:2] not in common_runs:
            continue
        b, c = base.get(key, set()), cur.get(key, set())
        d = baseline_vs_live[key[2]]
        d["prs"].add(key[0])
        d["baseline"] += len(b)
        d["live"] += len(c)
        d["both"] += len(b & c)
    baseline_vs_live = {k: {**v, "prs": len(v["prs"])} for k, v in baseline_vs_live.items()}

    weekly: dict = defaultdict(list)
    for j in judged:
        try:
            iso = datetime.fromisoformat(j["ts"]).isocalendar()
            weekly[f"{iso.year}-W{iso.week:02d}"].append(j)
        except (KeyError, ValueError):
            continue

    return {
        "posted_judged": len(judged),
        "posted_in_summary": in_summary,
        "by_reviewer": {k: _tally(v) for k, v in sorted(by_reviewer.items())},
        "by_rule": {k: _tally(v) for k, v in sorted(by_rule.items())},
        "by_severity": {k: _tally(v) for k, v in sorted(by_severity.items())},
        "calibration": calibration,
        "contested_rules": contested_rules,
        "remembered_rules": remembered,
        "misses": misses,
        "cost_by_pr": dict(cost_pr),
        "cost_by_reviewer": dict(cost_reviewer),
        "baseline_vs_live": baseline_vs_live,
        "weekly": {k: _tally(v) for k, v in sorted(weekly.items())},
        "_judged": judged,                 # détail pour le rapport humain, retiré à l'export
    }
