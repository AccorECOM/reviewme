"""Tuning: what the settings do, measured.

- **Calibration** per confidence band: if the 51-70 band is acted upon as often as the
  86-100 one, the threshold is costing findings;
- **baseline vs live**: the control pipeline on the same PRs and commits;
- **per settings state** (`config_hash`): runs, findings, acted-upon rate and cost
  between two changes — the before/after of each tweak;
- the rules people contest, with the prompt file that names each one (looked up in the
  current project folder: where to go and fix it) and, once `reviewme triage` ran, their
  main cause (a rule contested as `missing_context` needs a convention written down, one
  contested as `false_positive` needs its prompt fixed), and the `@reviewme remember` requests
  still to review. Never their text here: who asked, on which PR, and whether they have
  write access.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from ...triage import CAUSES as TRIAGE_CAUSES
from .. import charts as C
from .. import readings as R
from ..data import Context, cost_of
from ..i18n import texts

ID = "tuning"
TITLE = {"en": "Tuning", "fr": "Réglages"}

_T = {
    "en": {
        "calibration": "Calibration per confidence band",
        "calibration_note": "Every live finding once, posted or not. Acted upon: of the "
                            "posted ones.",
        "band": "Confidence", "dropped_low": "Below threshold",
        "dropped_unverified": "Refuted", "baseline": "Baseline vs live",
        "baseline_note": "Same PRs and commits: findings each pipeline would post, and "
                         "those both found.",
        "baseline_none": "No baseline run on the same commits in this window.",
        "b_baseline": "Baseline", "b_live": "Live", "b_both": "Both",
        "configs": "Per settings state", "config": "config_hash", "from": "From",
        "to": "To", "contested_rules": "Rules contested 3 times or more",
        "none_contested": "No rule contested 3 times yet.",
        "remembered": "Remember requests to review",
        "remembered_none": "No @reviewme remember request in this window.",
        "by": "By", "trusted": "Write access", "yes": "yes", "no": "no",
        "file": "File", "main_cause": "Main cause (triage)",
    },
    "fr": {
        "calibration": "Calibration par tranche de confiance",
        "calibration_note": "Chaque remarque live une fois, postée ou non. Suivies "
                            "d'effet : parmi les postées.",
        "band": "Confiance", "dropped_low": "Sous le seuil",
        "dropped_unverified": "Réfutées", "baseline": "Témoin contre live",
        "baseline_note": "Mêmes PR, mêmes commits : remarques que chaque pipeline "
                         "posterait, et celles trouvées par les deux.",
        "baseline_none": "Aucun passage témoin sur les mêmes commits sur cette fenêtre.",
        "b_baseline": "Témoin", "b_live": "Live", "b_both": "Communes",
        "configs": "Par état des réglages", "config": "config_hash", "from": "Du",
        "to": "Au", "contested_rules": "Règles contestées 3 fois ou plus",
        "none_contested": "Aucune règle contestée 3 fois pour l'instant.",
        "remembered": "Demandes remember à relire",
        "remembered_none": "Aucune demande @reviewme remember sur cette fenêtre.",
        "by": "Par", "trusted": "Droit d'écriture", "yes": "oui", "no": "non",
        "file": "Fichier", "main_cause": "Cause principale (tri)",
    },
}


def main_causes(ctx: Context) -> dict[str, str]:
    """`reviewer:rule_id` -> its most frequent triage cause (same key as
    `quality.contested_rules`). Ties go to the taxonomy order, so the result is stable."""
    per_rule: dict[str, Counter] = defaultdict(Counter)
    for r in ctx.triage.values():
        per_rule[f"{r.get('reviewer')}:{r.get('rule_id') or '-'}"][r.get("cause")] += 1
    order = {c: i for i, c in enumerate(TRIAGE_CAUSES)}
    return {k: min(v, key=lambda c: (-v[c], order.get(c, len(order))))
            for k, v in per_rule.items()}


def compute(ctx: Context) -> dict:
    q = ctx.quality
    causes = main_causes(ctx)
    configs: dict = defaultdict(lambda: {"first": None, "last": None, "runs": set(),
                                         "cost_usd": 0.0, "posted": 0, "followed": 0})
    for r in sorted(ctx.runs, key=lambda r: r.get("ts", "")):
        c = configs[r.get("config_hash") or "?"]
        c["first"] = c["first"] or r.get("ts")
        c["last"] = r.get("ts")
        c["runs"].add(ctx.run_key(r))
        c["cost_usd"] += r.get("cost_usd", 0) or 0
    for j in ctx.judged:
        c = configs[j.get("config_hash") or "?"]
        c["posted"] += 1
        c["followed"] += 1 if j["followed"] else 0
    return {
        "calibration": q["calibration"],
        "baseline_vs_live": q["baseline_vs_live"],
        "baseline_runs": len(ctx.run_groups(ctx.baseline_runs)),
        "baseline_cost_usd": round(cost_of(ctx.baseline_runs), 4),
        "configs": [{"config_hash": h, "from": ctx.period_of(c["first"]),
                     "to": ctx.period_of(c["last"]), "runs": len(c["runs"]),
                     "posted": c["posted"], "followed": c["followed"],
                     "followed_rate": C.ratio(c["followed"], c["posted"]),
                     "cost_usd": round(c["cost_usd"], 4)}
                    for h, c in sorted(configs.items(), key=lambda kv: kv[1]["first"] or "")],
        "contested_rules": [{**r, "file": ctx.rule_file(r["rule"]),
                             "main_cause": causes.get(r["rule"])}
                            for r in q["contested_rules"]],
        # Who, where, trusted: never the rule text, even when the ledger keeps it.
        "remembered": [{"pr": r.get("pr"), "user": ctx.name_of(r.get("user")),
                        "trusted": bool(r.get("trusted"))} for r in q["remembered_rules"]],
    }


def render(res: dict, ctx: Context) -> str:
    T = texts(_T, ctx.language)
    calib = C.table([T["band"], T["findings"], T["posted"], T["dropped_low"],
                     T["dropped_unverified"], T["followed"]],
                    [(b, c["findings"], c["fates"].get("posted", 0) + c["fates"].get("would_post", 0),
                      c["fates"].get("dropped_low", 0), c["fates"].get("dropped_unverified", 0),
                      f'{c["followed"]} · {C.pct(c["followed_rate"])}')
                     for b, c in res["calibration"].items()], right=(1, 2, 3, 4, 5))
    base = C.table([T["reviewer"], T["prs"], T["b_baseline"], T["b_live"], T["b_both"]],
                   [(k, v["prs"], v["baseline"], v["live"], v["both"])
                    for k, v in sorted(res["baseline_vs_live"].items())], right=(1, 2, 3, 4),
                   empty_text=T["baseline_none"])
    configs = C.table([T["config"], T["from"], T["to"], T["runs"], T["posted"], T["followed"],
                       T["cost"]],
                      [(c["config_hash"], c["from"] or "—", c["to"] or "—", c["runs"],
                        c["posted"], f'{c["followed"]} · {C.pct(c["followed_rate"])}',
                        C.money(c["cost_usd"])) for c in res["configs"]],
                      right=(3, 4, 5, 6), empty_text=T["no_data"])
    # The column only once a triage ran: a column of dashes would read as "no cause".
    triaged = bool(ctx.triage)
    contested = C.table([T["rule"], T["contested"], T["posted"], T["file"],
                         *([T["main_cause"]] if triaged else [])],
                        [(r["rule"], r["contested"], r["posted"], r["file"] or "—",
                          *([T[f"cause_{r['main_cause']}"] if r.get("main_cause") in
                             TRIAGE_CAUSES else "—"] if triaged else []))
                         for r in res["contested_rules"]], right=(1, 2),
                        empty_text=T["none_contested"])
    remembered = C.table([T["pr"], T["by"], T["trusted"]],
                         [(f'#{r["pr"]}', r["user"], T["yes"] if r["trusted"] else T["no"])
                          for r in res["remembered"]], empty_text=T["remembered_none"])
    L = ctx.language
    bl = res["baseline_vs_live"].values()
    return "".join([
        C.card(calib, T["calibration"], T["calibration_note"], R.top(
            T["followed"], [(b, c["followed_rate"]) for b, c in res["calibration"].items()],
            C.pct, L)),
        C.card(configs, T["configs"], reading=R.top(
            T["followed"], [(c["config_hash"], c["followed_rate"]) for c in res["configs"]],
            C.pct, L)),
        C.card(base, T["baseline"], T["baseline_note"] if res["baseline_vs_live"] else "",
               R.compare(T["findings"], T["b_baseline"].lower(),
                         sum(v["baseline"] for v in bl), T["b_live"].lower(),
                         sum(v["live"] for v in bl), C.num, L)),
        '<div class="grid2"><div>',
        C.card(contested, T["contested_rules"], reading=R.top(
            T["contested"], [(r["rule"], r["contested"]) for r in res["contested_rules"]],
            C.num, L)),
        "</div><div>",
        C.card(remembered, T["remembered"], reading=R.share(
            T["trusted"], sum(1 for r in res["remembered"] if r["trusted"]),
            len(res["remembered"]), L)),
        "</div></div>",
    ])
