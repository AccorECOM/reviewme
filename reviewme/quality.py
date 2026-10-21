"""The "quality" block: what the findings are worth, from the enriched ledger.

`stats.py` answers "how many and at what cost". This module answers "is it useful":

- **approximate precision** per reviewer and per `rule_id`: share of posted findings
  acted upon (the line disappeared), approved (👍, or a reply without contest),
  contested (👎, or a "false positive" / "intended" reply);
- **calibration** per confidence band: the same, to tell whether the threshold cost
  recall;
- **approximate misses**: human comments where the bot stayed silent, or self-censored;
- **baseline vs live** on the same PRs;
- **cost and duration** per PR and per reviewer.

Pure computation on lists of dicts: no network call, no write.
`dry_run` runs count for calibration (their findings are real) but not for precision
(nothing was posted, so nothing could be acted upon).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime

BANDS = ((0, 50), (51, 70), (71, 85), (86, 100))
CONTESTED_RULE_MIN = 3       # from 3 contests on, a rule needs revisiting


def band(confidence: int) -> str:
    for lo, hi in BANDS:
        if lo <= (confidence or 0) <= hi:
            return f"{lo}-{hi}"
    return f"{BANDS[-1][0]}-{BANDS[-1][1]}"


def latest(rows: list[dict], key: str) -> dict:
    """Last row per key (files are append-only: the most recent one wins)."""
    out: dict = {}
    for r in rows:
        if r.get(key) is not None:
            out[r[key]] = r
    return out


def _verdict(e: dict | None) -> dict:
    """What became of ONE posted finding, as four booleans."""
    e = e or {}
    reactions = Counter(r.get("content") for r in e.get("reactions", []))
    replies = e.get("replies", [])
    contested = reactions["-1"] > 0 or any(r.get("contests") for r in replies)
    fixed_reply = any(r.get("label") == "fixed" for r in replies)
    return {
        "followed": bool(e.get("fingerprint_gone_at")) or fixed_reply,
        # Approved = explicit POSITIVE signal (👍 or "fixed"); a question or a neutral
        # reply approves nothing.
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

    # A finding posted INLINE = one comment_id; we keep its first appearance. Findings
    # delivered in a global comment (`global` mode, 422 fallback) have no line to track:
    # counted separately, never in the precision denominator.
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

    # Calibration: each finding ONCE — its first appearance on the PR. Otherwise a finding
    # reproduced on every push would weigh as many times as there are commits.
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

    # Calibration: ALL live findings (posted or not), per band and per fate.
    calibration = {}
    for lo, hi in BANDS:
        key = f"{lo}-{hi}"
        in_band = [f for f in unique if band(f.get("confidence", 0)) == key]
        # In dry-run, "posted" means "would have been posted": say so.
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

    # Cost and duration: per PR (each variant separate) and per reviewer.
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

    # Baseline vs live: same (PR, commit), per reviewer — how many each would have posted,
    # and how many findings are only on one side (by fingerprint).
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
        "_judged": judged,                 # detail for the human report, removed on export
    }
