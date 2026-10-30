"""Summary: the five numbers someone asks for first, and their trend.

"Is the bot worth it?" in one screen: how many PRs, how many findings and how many of
them led to a change, what it cost, the BLOCKERs that were fixed before the merge (the
save that pays for a month), and how often a human said anything at all — a bot nobody
reacts to is being ignored, whatever its precision.

Coverage (merged PRs the bot reviewed) and the median wait before a human signal go in the
explanatory line, not in a sixth tile: they qualify the five numbers rather than compete
with them.

Above the tiles, a "Debrief" card says the same things in a few sentences, plus the
headlines of the ROI, history, reliability and quality pages (`dashboard/debrief.py`);
`[stats] debrief = false` hides it.
"""
from __future__ import annotations

from collections import defaultdict

from .. import charts as C
from .. import debrief
from .. import readings as R
from ..data import Context, cost_of, coverage, first_signal, hours_between, median
from ..i18n import plural, texts
from .roi import amount, estimate

ID = "summary"
TITLE = {"en": "Summary", "fr": "Synthèse"}

_T = {
    "en": {
        "prs": "PRs reviewed", "prs_sub": "{n} runs", "prs_sub_one": "{n} run",
        "posted": "findings posted", "posted_sub": "{pct} acted upon",
        "cost": "total cost", "cost_sub": "{per_pr} per PR",
        "blockers": "BLOCKERs fixed before merge", "blockers_sub": "out of {n} posted",
        "signal": "findings with a human signal", "signal_sub": "{n} with a reaction or reply",
        "chart_cost": "Cost {per}", "chart_followed": "Findings acted upon {per}",
        "scope": "Inline findings of the live pipeline. Acted upon = the commented line "
                 "changed, or a reply says it was fixed.",
        "scope_coverage": "Coverage: {pct} of the PRs merged in the window were reviewed "
                          "({r} of {n}).",
        "scope_delay": "First human signal after {h} (median).",
        "saved": "hours saved (estimated)", "saved_sub": "≈ {v}, see ROI",
    },
    "fr": {
        "prs": "PR relues", "prs_sub": "{n} passages", "prs_sub_one": "{n} passage",
        "posted": "remarques postées", "posted_sub": "{pct} suivies d'effet",
        "cost": "coût total", "cost_sub": "{per_pr} par PR",
        "blockers": "BLOCKER corrigés avant merge", "blockers_sub": "sur {n} postés",
        "signal": "remarques avec un retour humain",
        "signal_sub": "{n} avec une réaction ou une réponse",
        "chart_cost": "Coût {per}", "chart_followed": "Remarques suivies d'effet {per}",
        "scope": "Remarques en ligne du pipeline live. Suivie d'effet = la ligne commentée "
                 "a changé, ou une réponse dit que c'est corrigé.",
        "scope_coverage": "Couverture : {pct} des PR mergées sur la fenêtre ont été relues "
                          "({r} sur {n}).",
        "scope_delay": "Premier retour humain après {h} (médiane).",
        "saved": "heures gagnées (estimation)", "saved_sub": "≈ {v}, voir ROI",
    },
}


def compute(ctx: Context) -> dict:
    judged = ctx.judged
    prs = {r.get("pr") for r in ctx.runs}
    cost = cost_of(ctx.runs)
    blockers = [j for j in judged if j.get("severity") == "BLOCKER"]
    periods = ctx.periods()
    cost_p: dict[str, float] = defaultdict(float)
    for r in ctx.runs:
        cost_p[ctx.period_of(r.get("ts"))] += r.get("cost_usd", 0) or 0
    followed_p: dict[str, int] = defaultdict(int)
    for j in judged:
        if j["followed"]:
            followed_p[ctx.period_of(j.get("ts"))] += 1
    followed = sum(1 for j in judged if j["followed"])
    answered = sum(1 for j in judged if j["answered"])
    delays = [h for j in judged if (h := hours_between(
        j.get("ts"), first_signal(ctx.enrich.get(j.get("comment_id"))))) is not None]
    cov = coverage(ctx)
    roi = estimate(ctx)
    out = {
        "roi_hours_saved": roi["hours_saved"] if roi["available"] else None,
        "roi_value": roi["value"] if roi["available"] else None,
        "roi_currency": roi.get("currency"),
        "prs": len(prs),
        "runs": len(ctx.run_groups()),
        "posted": len(judged),
        "followed": followed,
        "followed_rate": C.ratio(followed, len(judged)),
        "cost_usd": round(cost, 4),
        "cost_per_pr": round(cost / len(prs), 4) if prs else None,
        "blockers_posted": len(blockers),
        "blockers_fixed_before_merge": sum(1 for j in blockers if ctx.acted_before_merge(j)),
        "with_human_signal": answered,
        "human_signal_rate": C.ratio(answered, len(judged)),
        "judgment_delay_median_h": median(delays),
        "coverage_rate": cov.get("rate"),
        "coverage_merged": cov.get("merged"),
        "coverage_reviewed": cov.get("reviewed"),
        "periods": periods,
        "cost_by_period": [round(cost_p.get(p, 0), 4) for p in periods],
        "followed_by_period": [followed_p.get(p, 0) for p in periods],
        "config_changes": ctx.config_markers(periods),
    }
    # After the rest: the debrief restates these numbers, it does not recompute them.
    out["debrief"] = [{"id": i, "text": t} for i, t in debrief.sentences(ctx, out)] \
        if ctx.settings.debrief else []
    return out


def render(res: dict, ctx: Context) -> str:
    T = texts(_T, ctx.language)
    # Above the empty state too: the history headline exists without a run in the window.
    brief = debrief.card([(d["id"], d["text"]) for d in res["debrief"]], ctx.language)
    if not res["runs"]:
        return brief + C.card(C.empty(T["no_data"]))
    per = T["per_" + ctx.settings.period]
    items = [
        (str(res["prs"]), T["prs"], plural(T, "prs_sub", res["runs"])),
        (str(res["posted"]), T["posted"], T["posted_sub"].format(pct=C.pct(res["followed_rate"]))),
        (C.money(res["cost_usd"]), T["cost"], T["cost_sub"].format(per_pr=C.money(res["cost_per_pr"]))),
        (str(res["blockers_fixed_before_merge"]), T["blockers"],
         T["blockers_sub"].format(n=res["blockers_posted"])),
        (C.pct(res["human_signal_rate"]), T["signal"],
         T["signal_sub"].format(n=res["with_human_signal"])),
    ]
    if res["roi_hours_saved"] is not None:          # six tiles at most
        items.append((C.hours(res["roi_hours_saved"]), T["saved"],
                      T["saved_sub"].format(v=amount(res["roi_value"], res["roi_currency"]))))
    tiles = C.tiles(items)
    marks = res["config_changes"]
    note = T["marker_note"] if marks else ""
    cost_pts = list(zip(res["periods"], res["cost_by_period"], strict=True))
    followed_pts = list(zip(res["periods"], res["followed_by_period"], strict=True))
    cost = C.line(cost_pts, C.money, width=C.HALF, markers=marks, marker_label=T["marker"],
                  empty_text=T["no_data"])
    followed = C.line(followed_pts, C.num, width=C.HALF, markers=marks,
                      marker_label=T["marker"], empty_text=T["no_data"])
    scope = [T["scope"]]
    if res["coverage_rate"] is not None:
        scope.append(T["scope_coverage"].format(pct=C.pct(res["coverage_rate"]),
                                                r=res["coverage_reviewed"],
                                                n=res["coverage_merged"]))
    if res["judgment_delay_median_h"] is not None:
        scope.append(T["scope_delay"].format(h=C.hours(res["judgment_delay_median_h"])))
    return (brief + tiles + f'<p class="note">{C.esc(" ".join(scope))}</p>'
            + '<div class="grid2"><div>' + C.card(cost, T["chart_cost"].format(per=per), note,
                                                    R.trend(cost_pts, C.money, ctx.language,
                                                            current=ctx.current_period()))
            + "</div><div>" + C.card(followed, T["chart_followed"].format(per=per), note,
                                     R.trend(followed_pts, C.num, ctx.language,
                                             current=ctx.current_period()))
            + "</div></div>")
