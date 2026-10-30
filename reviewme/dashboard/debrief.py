"""The summary's opening debrief: a handful of generated sentences, no LLM.

The tiles answer "how much"; a reader opening the page for the first time (a manager, a
team that just installed the bot) wants the gist in words first. Each sentence is built
from numbers another section already computes, formatted like the tiles, and is only
written when its data exists: a sentence about a missing number would be a sentence
about nothing ("— % acted upon"). Deterministic: the same ledger gives the same text, so
the page can be diffed and tested. No sentence quotes a finding, a reply or a PR title.

Order: volume and coverage, quality, cost, ROI (the control group when the window has
one, else the `[stats.roi]` estimate), history (before vs the last era, with its caveat:
a trend, not a cause), reliability (only when something went wrong), and the most
contested rule (only from three contestations, like the quality page's table).
"""
from __future__ import annotations

from . import charts as C
from . import readings as R
from .data import Context
from .i18n import plural, texts

_T = {
    "en": {
        "title": "Debrief",
        "prs": "{n} PRs", "prs_one": "{n} PR", "runs": "{n} runs", "runs_one": "{n} run",
        "volume": "{prs} reviewed in the window ({runs}).",
        "volume_cov": "{prs} reviewed in the window ({runs}); {pct} of merged PRs covered "
                      "({r} of {n}).",
        "posted": "{n} findings posted", "posted_one": "{n} finding posted",
        "quality": "{posted}: {acted} acted upon, {contested} contested.",
        "quality_delay": "{posted}: {acted} acted upon, {contested} contested, first human "
                         "signal after {h} (median).",
        "cost": "Cost {total}: {per_pr} per PR.",
        "cost_followed": "Cost {total}: {per_pr} per PR, {per_followed} per finding acted "
                         "upon.",
        "roi_cg": "Reviewed vs unreviewed PRs (medians): {parts}.",
        "cg_first": "first human review after {ab}",
        "cg_merge": "merged after {ab}",
        "roi_est": "Estimated {h} of human time saved ({v}) for {api} of API cost; see ROI.",
        "history": "Before vs {era} (medians): {parts}; a trend, not a cause.",
        "h_merge": "to merge {ab}", "h_first": "to first human review {ab}",
        "after": "after",
        "reliability": "Incidents over {n} reviewer runs: CLI errors {e}, max_turns cuts "
                       "{m}.",
        "contested": "Most contested rule: {rule}, {c} times out of {p} posted.",
    },
    "fr": {
        "title": "En bref",
        "prs": "{n} PR", "runs": "{n} passages", "runs_one": "{n} passage",
        "volume": "{prs} relues sur la fenêtre ({runs}).",
        "volume_cov": "{prs} relues sur la fenêtre ({runs}) ; {pct} des PR mergées "
                      "couvertes ({r} sur {n}).",
        "posted": "{n} remarques postées", "posted_one": "{n} remarque postée",
        "quality": "{posted} : {acted} suivies d'effet, {contested} contestées.",
        "quality_delay": "{posted} : {acted} suivies d'effet, {contested} contestées, "
                         "premier retour humain après {h} (médiane).",
        "cost": "Coût {total} : {per_pr} par PR.",
        "cost_followed": "Coût {total} : {per_pr} par PR, {per_followed} par remarque "
                         "suivie d'effet.",
        "roi_cg": "PR relues contre non relues (médianes) : {parts}.",
        "cg_first": "première relecture humaine après {ab}",
        "cg_merge": "merge après {ab}",
        "roi_est": "Estimation : {h} de temps humain gagnées ({v}) pour {api} de coût API ; "
                   "voir ROI.",
        "history": "Avant contre {era} (médianes) : {parts} ; une tendance, pas une "
                   "cause.",
        "h_merge": "avant merge {ab}",
        "h_first": "avant première relecture humaine {ab}", "after": "après",
        "reliability": "Incidents sur {n} passages de relecteur : erreurs CLI {e}, "
                       "coupures max_turns {m}.",
        "contested": "Règle la plus contestée : {rule}, {c} fois sur {p} postées.",
    },
}


def title(language: str) -> str:
    return texts(_T, language)["title"]


def _pairs(T: dict, pairs: list[tuple[str, float | None, float | None]], language: str,
           arrow: bool = False) -> str:
    """The `a vs b` (or `a → b`, a change over time) parts whose two sides are known,
    joined; "" when none is. `readings.versus` writes the pair, as under the charts."""
    def ab(a: float, b: float) -> str:
        return f"{C.hours(a)} → {C.hours(b)}" if arrow else R.versus(a, b, C.hours, language)
    return ", ".join(T[k].format(ab=ab(a, b))
                     for k, a, b in pairs if a is not None and b is not None)


def sentences(ctx: Context, summary: dict) -> list[tuple[str, str]]:
    """`[(id, sentence)]` in display order, only those whose data exists. `summary` is
    the summary section's own `compute` result: the debrief restates it, never
    recomputes it differently."""
    from .sections import history, reliability, roi
    T = texts(_T, ctx.language)
    out: list[tuple[str, str]] = []

    if summary["prs"]:
        prs, runs = plural(T, "prs", summary["prs"]), plural(T, "runs", summary["runs"])
        if summary["coverage_rate"] is not None:
            out.append(("volume", T["volume_cov"].format(
                prs=prs, runs=runs, pct=C.pct(summary["coverage_rate"]),
                r=summary["coverage_reviewed"], n=summary["coverage_merged"])))
        else:
            out.append(("volume", T["volume"].format(prs=prs, runs=runs)))

    if summary["posted"]:
        contested = sum(1 for j in ctx.judged if j.get("contested"))
        values = {"posted": plural(T, "posted", summary["posted"]),
                  "acted": C.pct(summary["followed_rate"]),
                  "contested": C.pct(C.ratio(contested, summary["posted"]))}
        delay = summary["judgment_delay_median_h"]
        out.append(("quality", T["quality_delay"].format(**values, h=C.hours(delay))
                    if delay is not None else T["quality"].format(**values)))

    if summary["runs"] and summary["cost_per_pr"] is not None:
        total, per_pr = C.money(summary["cost_usd"]), C.money(summary["cost_per_pr"])
        if summary["followed"]:
            out.append(("cost", T["cost_followed"].format(
                total=total, per_pr=per_pr,
                per_followed=C.money(summary["cost_usd"] / summary["followed"]))))
        else:
            out.append(("cost", T["cost"].format(total=total, per_pr=per_pr)))

    cg = roi.control_group(ctx)
    parts = _pairs(T, [
        ("cg_first", cg["reviewed"]["median_first_review_h"],
         cg["unreviewed"]["median_first_review_h"]),
        ("cg_merge", cg["reviewed"]["median_merge_h"], cg["unreviewed"]["median_merge_h"]),
    ], ctx.language) if cg["available"] else ""
    if parts:
        out.append(("roi", T["roi_cg"].format(parts=parts)))
    elif summary["runs"]:
        # Not on an empty window: "0 h saved for 0 EUR" restates the absence of runs.
        est = roi.estimate(ctx)
        if est["available"]:
            out.append(("roi", T["roi_est"].format(
                h=C.hours(est["hours_saved"]), v=roi.amount(est["value"], est["currency"]),
                api=roi.amount(est["api_cost"], est["currency"]))))

    ba = history.before_after(ctx)
    if ba["available"] and ba["before"] is not None:
        b, a = ba["before"], ba["after"]
        parts = _pairs(T, [("h_merge", b["median_merge_h"], a["median_merge_h"]),
                           ("h_first", b["median_first_review_h"],
                            a["median_first_review_h"])], ctx.language, arrow=True)
        if parts:
            out.append(("history", T["history"].format(era=ba["after_name"] or T["after"],
                                                       parts=parts)))

    rel = reliability.compute(ctx)
    if rel["is_error"] or rel["max_turns"]:
        out.append(("reliability", T["reliability"].format(
            e=rel["is_error"], m=rel["max_turns"], n=rel["reviewer_runs"])))

    rules = ctx.quality.get("contested_rules") or []
    if rules:                              # already only rules contested 3 times or more
        top = rules[0]
        out.append(("contested", T["contested"].format(rule=top["rule"], c=top["contested"],
                                                       p=top["posted"])))
    return out


def card(items: list[tuple[str, str]], language: str) -> str:
    if not items:
        return ""
    body = '<ul class="debrief">' + "".join(f"<li>{C.esc(s)}</li>" for _, s in items) \
        + "</ul>"
    return C.card(body, title(language))
