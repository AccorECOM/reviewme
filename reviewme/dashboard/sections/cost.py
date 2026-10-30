"""Cost: where the money goes, and what each dollar buys.

Totals per PR, per run, per dev, per reviewer and per model, then the ratios that say
whether a setting is worth its price: cost per finding acted upon (the honest unit
price), per 100 diff lines (comparable across teams), the share spent on verification
and on advice-only reviewers, the cache ratio (a low one means the prompt prefix keeps
changing), and the runs that hit `max_turns` (paid in full, cut short).

A model comparison appears for a reviewer only when it ran on two models or more in the
window: that is the A/B the numbers can actually support.
"""
from __future__ import annotations

from collections import defaultdict

from .. import charts as C
from .. import readings as R
from ..data import Context, cost_of
from ..i18n import plural, texts

ID = "cost"
TITLE = {"en": "Cost", "fr": "Coût"}

_T = {
    "en": {
        "total": "total cost", "per_pr": "per PR", "per_run": "per run",
        "per_followed": "per finding acted upon", "per_100": "per 100 diff lines",
        "verif": "spent on verification", "advisor": "spent on advice-only reviewers",
        "cache": "cache read ratio", "cache_sub": "cache reads / input tokens",
        "turns": "turns per reviewer run", "max_turns": "runs cut by max_turns",
        "silent": "runs with nothing new", "by_dev": "Per developer",
        "by_reviewer": "Per reviewer", "by_model": "Per model", "per_run_col": "Per run",
        "models": "Model comparison per reviewer",
        "models_note": "Only reviewers that ran on two models or more in this window.",
        "followed_rate": "Acted upon rate", "n_runs": "{n} runs", "n_runs_one": "{n} run",
    },
    "fr": {
        "total": "coût total", "per_pr": "par PR", "per_run": "par passage",
        "per_followed": "par remarque suivie d'effet", "per_100": "pour 100 lignes de diff",
        "verif": "dépensé en vérification", "advisor": "dépensé en relecteurs de conseil",
        "cache": "taux de cache", "cache_sub": "lectures cache / tokens d'entrée",
        "turns": "tours par relecteur", "max_turns": "passages coupés par max_turns",
        "silent": "passages sans rien de nouveau", "by_dev": "Par développeur",
        "by_reviewer": "Par relecteur", "by_model": "Par modèle", "per_run_col": "Par passage",
        "models": "Comparaison de modèles par relecteur",
        "models_note": "Seulement les relecteurs passés sur au moins deux modèles sur la "
                       "fenêtre.",
        "followed_rate": "Taux suivi d'effet", "n_runs": "{n} passages",
        "n_runs_one": "{n} passage",
    },
}


def _bucket(rows: list[dict], key) -> dict:
    out: dict = defaultdict(lambda: {"runs": 0, "cost_usd": 0.0})
    for r in rows:
        b = out[key(r)]
        b["runs"] += 1
        b["cost_usd"] += r.get("cost_usd", 0) or 0
    return {k: {**v, "cost_usd": round(v["cost_usd"], 4),
                "per_run": round(v["cost_usd"] / v["runs"], 4)}
            for k, v in sorted(out.items(), key=lambda kv: -kv[1]["cost_usd"])}


def compute(ctx: Context) -> dict:
    runs, groups = ctx.runs, ctx.run_groups()
    total = cost_of(runs)
    prs = {r.get("pr") for r in runs}
    followed = sum(1 for j in ctx.judged if j["followed"])
    lines = sum(max((r.get("diff_lines", 0) or 0) for r in rows) for rows in groups.values())
    verif = [r["verification"].get("cost_usd", 0) or 0 for r in runs
             if isinstance(r.get("verification"), dict)]
    by_reviewer_rows: dict[str, list] = defaultdict(list)
    for r in runs:
        by_reviewer_rows[r.get("reviewer") or "?"].append(r)
    advisors = [rid for rid, rows in by_reviewer_rows.items()
                if sum(r.get("advice", 0) or 0 for r in rows) > 0
                and sum(r.get("findings", 0) or 0 for r in rows) == 0]
    input_tokens = sum(r.get("input_tokens", 0) or 0 for r in runs)
    silent = ctx.silent_runs()

    by_dev: dict = defaultdict(lambda: {"runs": 0, "cost_usd": 0.0})
    for rows in groups.values():
        d = by_dev[ctx.author_of(rows[0].get("pr"))]
        d["runs"] += 1
        d["cost_usd"] += cost_of(rows)

    models = {}
    for rid, rows in by_reviewer_rows.items():
        seen = {r.get("model") or "?" for r in rows}
        if len(seen) < 2:
            continue
        per = _bucket(rows, lambda r: r.get("model") or "?")
        for m, v in per.items():
            posted = [j for j in ctx.judged if j.get("reviewer") == rid
                      and (j.get("model") or "?") == m]
            v["posted"] = len(posted)
            v["followed_rate"] = C.ratio(sum(1 for j in posted if j["followed"]), len(posted))
        models[rid] = per

    return {
        "total_usd": round(total, 4),
        "per_pr": round(total / len(prs), 4) if prs else None,
        "per_run": round(total / len(groups), 4) if groups else None,
        "per_followed": round(total / followed, 4) if followed else None,
        "per_100_lines": round(100 * total / lines, 4) if lines else None,
        "verification_share": C.ratio(sum(verif), total) if verif and total else None,
        "advisor_reviewers": sorted(advisors),
        "advisor_share": C.ratio(sum(cost_of(by_reviewer_rows[a]) for a in advisors), total)
        if advisors and total else None,
        "cache_ratio": C.ratio(sum(r.get("cache_read_tokens", 0) or 0 for r in runs),
                               input_tokens),
        "turns_per_run": round(sum(r.get("num_turns", 0) or 0 for r in runs) / len(runs), 1)
        if runs else None,
        "max_turns_hits": sum(1 for r in runs if r.get("subtype") == "error_max_turns"),
        "silent_runs": len(silent),
        "silent_cost_usd": round(sum(cost_of(rows) for rows in silent.values()), 4),
        "by_dev": {k: {**v, "cost_usd": round(v["cost_usd"], 4)}
                   for k, v in sorted(by_dev.items(), key=lambda kv: -kv[1]["cost_usd"])},
        "by_reviewer": _bucket(runs, lambda r: r.get("reviewer") or "?"),
        "by_model": _bucket(runs, lambda r: r.get("model") or "?"),
        "models_by_reviewer": models,
    }


def render(res: dict, ctx: Context) -> str:
    T = texts(_T, ctx.language)
    if not res["by_reviewer"]:
        return C.card(C.empty(T["no_data"]))

    def bucket_table(first: str, data: dict) -> str:
        return C.table([first, T["runs"], T["cost"], T["per_run_col"]],
                       [(k, v["runs"], C.money(v["cost_usd"]), C.money(v["per_run"], 3))
                        for k, v in data.items()], right=(1, 2, 3))

    def bucket_reading(data: dict) -> str:
        return R.top(T["cost"], [(k, v["cost_usd"]) for k, v in data.items()], C.money,
                     ctx.language)

    out = [
        C.tiles([
            (C.money(res["total_usd"]), T["total"]),
            (C.money(res["per_pr"]), T["per_pr"]),
            (C.money(res["per_run"]), T["per_run"]),
            (C.money(res["per_followed"]), T["per_followed"]),
            (C.money(res["per_100_lines"], 3), T["per_100"]),
            (C.pct(res["verification_share"]), T["verif"]),
            (C.pct(res["advisor_share"]), T["advisor"], ", ".join(res["advisor_reviewers"])),
            (C.pct(res["cache_ratio"]), T["cache"], T["cache_sub"]),
            (C.num(res["turns_per_run"]), T["turns"]),
            (str(res["max_turns_hits"]), T["max_turns"]),
            (C.money(res["silent_cost_usd"]), T["silent"],
             plural(T, "n_runs", res["silent_runs"])),
        ]),
        C.card(C.hbars([(k, v["cost_usd"], C.money(v["cost_usd"]))
                        for k, v in list(res["by_dev"].items())[:15]], T["no_data"]),
               T["by_dev"], reading=bucket_reading(res["by_dev"])),
        '<div class="grid2"><div>',
        C.card(bucket_table(T["reviewer"], res["by_reviewer"]), T["by_reviewer"],
               reading=bucket_reading(res["by_reviewer"])),
        "</div><div>",
        C.card(bucket_table(T["model"], res["by_model"]), T["by_model"],
               reading=bucket_reading(res["by_model"])),
        "</div></div>",
    ]
    if res["models_by_reviewer"]:
        rows = [(rid, m, v["runs"], C.money(v["per_run"], 3), v["posted"],
                 C.pct(v["followed_rate"]))
                for rid, per in res["models_by_reviewer"].items() for m, v in per.items()]
        out.append(C.card(C.table([T["reviewer"], T["model"], T["runs"], T["per_run_col"],
                                   T["posted"], T["followed_rate"]], rows,
                                  right=(2, 3, 4, 5)), T["models"], T["models_note"],
                          R.top(T["per_run_col"], [(f"{rid} · {m}", v["per_run"])
                                                   for rid, per in res["models_by_reviewer"].items()
                                                   for m, v in per.items()],
                                lambda x: C.money(x, 3), ctx.language)))
    return "".join(out)
