"""Usage: who gets reviewed, how often, and what the re-reviews cost.

A review runs on EVERY push. Most of the cost of a busy PR is not its first review but
the tenth, which re-finds what is already commented: the "re-review tax" (runs that
posted nothing new) is the number that says whether `when.paths` or a skip label would
pay off. Participation (reactions, replies, `@reviewme remember`) says whether the
feedback loop is alive.

**Coverage** (PRs merged in the window that the bot reviewed, from the control group of
`prs.jsonl`) says whether the numbers describe the team's work or a corner of it; per
developer, it shows who is never reviewed. **Trigger** (`REVIEWME_TRIGGER`) splits the runs
of the PR flow from re-runs by hand and scheduled passes.

People appear here only for volume, coverage and participation, never next to a judgment
of their code (see `export.py`, same rule).
"""
from __future__ import annotations

from collections import Counter, defaultdict

from .. import charts as C
from .. import readings as R
from ..data import Context, cost_of, coverage, parse_ts
from ..i18n import plural, texts

ID = "usage"
TITLE = {"en": "Usage", "fr": "Usage"}
HEAVY_RUNS = 5               # a PR reviewed 5 times or more is worth a look
HIST_CAP = 10                # runs-per-PR histogram: 10 and more in one bar

_T = {
    "en": {
        "prs_by_runs": "PRs per number of runs",
        "per_dev": "Per developer", "received": "Findings received",
        "dist": "Runs per PR", "dist_note": "Number of PRs by how many runs they took.",
        "heavy": "PRs with {n} runs or more", "heavy_none": "No PR reached {n} runs.",
        "tax": "runs that posted nothing new", "tax_sub": "{n} runs · {cost}", "tax_sub_one": "{n} run · {cost}",
        "participants": "devs who reacted or replied", "remember": "remember requests",
        "resolved": "threads resolved by a person",
        "loop": "Feedback loop", "loop_note": "Reactions and replies, per person.",
        "weekday": "Runs by weekday (UTC)", "hour": "Runs by hour (UTC)",
        "days": "Mon,Tue,Wed,Thu,Fri,Sat,Sun",
        "coverage": "merged PRs reviewed", "coverage_sub": "{r} of {n}",
        "coverage_off": "needs enrich with [stats] control_group",
        "col_coverage": "Coverage", "trigger": "Runs by trigger",
        "trigger_note": "REVIEWME_TRIGGER of the deployment; unknown = not set.",
    },
    "fr": {
        "prs_by_runs": "PR par nombre de passages",
        "per_dev": "Par développeur", "received": "Remarques reçues",
        "dist": "Passages par PR", "dist_note": "Nombre de PR selon leur nombre de passages.",
        "heavy": "PR avec {n} passages ou plus", "heavy_none": "Aucune PR n'a atteint "
                                                               "{n} passages.",
        "tax": "passages sans rien de nouveau", "tax_sub": "{n} passages · {cost}",
        "tax_sub_one": "{n} passage · {cost}",
        "participants": "devs ayant réagi ou répondu", "remember": "demandes remember",
        "resolved": "fils résolus par une personne",
        "loop": "Boucle de retour", "loop_note": "Réactions et réponses, par personne.",
        "weekday": "Passages par jour (UTC)", "hour": "Passages par heure (UTC)",
        "days": "lun,mar,mer,jeu,ven,sam,dim",
        "coverage": "PR mergées relues", "coverage_sub": "{r} sur {n}",
        "coverage_off": "demande enrich avec [stats] control_group",
        "col_coverage": "Couverture", "trigger": "Passages par déclencheur",
        "trigger_note": "REVIEWME_TRIGGER du déploiement ; unknown = non renseigné.",
    },
}


def compute(ctx: Context) -> dict:
    groups = ctx.run_groups()
    devs: dict[str, dict] = defaultdict(lambda: {"prs": set(), "runs": 0, "findings": 0,
                                                 "followed": 0, "cost_usd": 0.0})
    runs_per_pr: Counter = Counter()
    cost_per_pr: dict = defaultdict(float)
    weekday, hour = [0] * 7, [0] * 24
    triggers: Counter = Counter()
    for rows in groups.values():
        triggers[rows[0].get("trigger") or "unknown"] += 1
        pr = rows[0].get("pr")
        d = devs[ctx.author_of(pr)]
        d["prs"].add(pr)
        d["runs"] += 1
        d["cost_usd"] += cost_of(rows)
        runs_per_pr[pr] += 1
        cost_per_pr[pr] += cost_of(rows)
        dt = parse_ts(rows[0].get("ts"))
        if dt:
            weekday[dt.weekday()] += 1
            hour[dt.hour] += 1
    for j in ctx.judged:
        d = devs[ctx.author_of(j.get("pr"))]
        d["findings"] += 1
        d["followed"] += 1 if j["followed"] else 0

    hist = Counter(min(n, HIST_CAP) for n in runs_per_pr.values())
    silent = ctx.silent_runs()
    people: Counter = Counter()
    remember = resolved = 0
    for e in ctx.enrich.values():
        for r in e.get("reactions", []) + e.get("replies", []):
            if r.get("user"):
                people[ctx.name_of(r["user"])] += 1
        remember += sum(1 for r in e.get("replies", []) if r.get("remember"))
        resolved += 1 if e.get("thread_resolved") and e.get("resolved_by") else 0

    return {
        "per_dev": {k: {**v, "prs": len(v["prs"]), "cost_usd": round(v["cost_usd"], 4),
                        "followed_rate": C.ratio(v["followed"], v["findings"])}
                    for k, v in sorted(devs.items(), key=lambda kv: -kv[1]["runs"])},
        "runs_per_pr_hist": {(f"{n}+" if n == HIST_CAP else str(n)): hist.get(n, 0)
                             for n in range(1, (max(hist) if hist else 0) + 1)},
        "heavy_prs": [{"pr": pr, "runs": n, "cost_usd": round(cost_per_pr[pr], 4)}
                      for pr, n in sorted(runs_per_pr.items(), key=lambda kv: -kv[1])
                      if n >= HEAVY_RUNS],
        "silent_runs": len(silent),
        "silent_share": C.ratio(len(silent), len(groups)),
        "silent_cost_usd": round(sum(cost_of(rows) for rows in silent.values()), 4),
        "participants": len(people),
        "participation": dict(people.most_common()),
        "remember": remember,
        "threads_resolved": resolved,
        "by_weekday": weekday,
        "by_hour": hour,
        "by_trigger": dict(triggers.most_common()),
        "coverage": coverage(ctx),
    }


def render(res: dict, ctx: Context) -> str:
    T = texts(_T, ctx.language)
    if not res["per_dev"]:
        return C.card(C.empty(T["no_data"]))
    cov = res["coverage"]
    by_dev = cov.get("by_dev", {})

    def dev_cov(k: str) -> str:
        c = by_dev.get(k)
        return f'{c["reviewed"]}/{c["merged"]} · {C.pct(c["rate"])}' if c else "—"

    # Authors who merged PRs without a single review appear too: they ARE the coverage gap.
    zero = {"prs": 0, "runs": 0, "findings": 0, "followed": 0, "followed_rate": None,
            "cost_usd": 0.0}
    per_dev = {**res["per_dev"], **{k: zero for k in by_dev if k not in res["per_dev"]}}
    devs = C.table([T["dev"], T["prs"], T["runs"], T["received"], T["followed"], T["cost"],
                    T["col_coverage"]],
                   [(k, v["prs"], v["runs"], v["findings"],
                     f'{v["followed"]} · {C.pct(v["followed_rate"])}', C.money(v["cost_usd"]),
                     dev_cov(k))
                    for k, v in per_dev.items()], right=(1, 2, 3, 4, 5, 6))
    if cov["available"]:
        cov_tile = (C.pct(cov["rate"]), T["coverage"],
                    T["coverage_sub"].format(r=cov["reviewed"], n=cov["merged"]))
    else:
        cov_tile = ("—", T["coverage"], T["coverage_off"])
    heavy = C.table([T["pr"], T["runs"], T["cost"]],
                    [(f'#{h["pr"]}', h["runs"], C.money(h["cost_usd"]))
                     for h in res["heavy_prs"]], right=(1, 2),
                    empty_text=T["heavy_none"].format(n=HEAVY_RUNS))
    days = T["days"].split(",")
    loop = [(k, n, str(n)) for k, n in list(res["participation"].items())[:12]]
    L = ctx.language
    weekday = list(zip(days, res["by_weekday"], strict=True))
    hours = [(f"{h:02d}", n) for h, n in enumerate(res["by_hour"])]
    return "".join([
        C.tiles([
            (C.pct(res["silent_share"]), T["tax"],
             plural(T, "tax_sub", res["silent_runs"], cost=C.money(res["silent_cost_usd"]))),
            (str(res["participants"]), T["participants"]),
            (str(res["remember"]), T["remember"]),
            (str(res["threads_resolved"]), T["resolved"]),
            cov_tile,
        ]),
        C.card(devs, T["per_dev"], reading=R.top(
            T["runs"], [(k, v["runs"]) for k, v in per_dev.items()], C.num, L)),
        '<div class="grid2"><div>',
        C.card(C.bars(list(res["runs_per_pr_hist"].items()), C.num, width=C.HALF), T["dist"],
               T["dist_note"], R.top(T["prs_by_runs"], list(res["runs_per_pr_hist"].items()),
                                     C.num, L)),
        "</div><div>",
        C.card(heavy, T["heavy"].format(n=HEAVY_RUNS), reading=R.top(
            T["runs"], [(f'#{h["pr"]}', h["runs"]) for h in res["heavy_prs"]], C.num, L)),
        "</div></div>",
        '<div class="grid2"><div>',
        C.card(C.hbars(loop, T["no_data"]), T["loop"], T["loop_note"],
               R.top(T["count"], loop, C.num, L)),
        "</div><div>",
        C.card(C.hbars([(k, n, str(n)) for k, n in res["by_trigger"].items()], T["no_data"]),
               T["trigger"], T["trigger_note"],
               R.top(T["runs"], list(res["by_trigger"].items()), C.num, L)),
        "</div></div>",
        '<div class="grid2"><div>',
        C.card(C.bars(weekday, C.num, width=C.HALF), T["weekday"],
               reading=R.top(T["runs"], weekday, C.num, L)),
        "</div><div>",
        C.card(C.bars(hours, C.num, width=C.HALF), T["hour"],
               reading=R.top(T["runs"], hours, C.num, L)),
        "</div></div>",
    ])
