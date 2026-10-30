"""ROI: is the bot worth it? Three answers, from the most to the least solid.

1. **Measured on PRs (control group)**: reviewed vs unreviewed PRs of the SAME repository
   in the SAME window (`pr_cycles`, needs `[stats] control_group`): time to the first
   human review, to merge, human reviews and comments per PR, and how soon the bot
   answers. Same team, same weeks: the closest thing to an experiment the ledger has,
   though authors choose which PRs get the label.
2. **Measured over time**: the history section's before/after (`reviewme history`), in
   four numbers, with a link to it. Weaker: the team and the product changed too. With
   `[[stats.eras]]`, "before" the first era against the LAST one (the current setup),
   named on the tiles.
3. **Estimated**: findings acted upon, valued with `[stats.roi]` — minutes a finding
   saves, hours a BLOCKER caught before merge saves, the cost of an hour — against the
   API cost. Every assumption is printed next to the numbers: the estimate is only as
   good as them, and the reader must be able to disagree with one. Hidden, with the
   reason, until `[stats.roi]` exists.

A BLOCKER fixed before merge is counted once, at the blocker rate, not also as a finding.
The API cost is in USD (what the CLI reports); `usd_rate` converts it into `currency`, so
the ratio compares two amounts in the same unit.
"""
from __future__ import annotations

import statistics

from .. import charts as C
from .. import readings as R
from ..data import Context, cost_of, hours_between, median, pr_cycles
from ..i18n import texts
from . import history

ID = "roi"
TITLE = {"en": "ROI", "fr": "ROI"}

_T = {
    "en": {
        "l1": "1. Measured on PRs: control group",
        "l1_tag": "measured, same repository, same period",
        "l1_none": "No control group in this window: `reviewme enrich` records the PRs "
                   "the bot never reviewed when `[stats] control_group` is on (the "
                   "default).",
        "l1_note": "PRs of prs.jsonl in the window. Authors choose which PRs the bot "
                   "reviews (label, drafts): the two groups are comparable, not identical.",
        "metric": "Metric", "reviewed": "Reviewed", "unreviewed": "Not reviewed",
        "m_prs": "PRs", "m_merged": "Merged", "m_first": "Median hours to first human review",
        "m_merge": "Median hours open to merge", "m_reviews": "Human reviews per PR",
        "m_comments": "Human comments per PR",
        "m_bot": "Bot's first run after opening (median)",
        "deltas": "Reviewed vs not reviewed", "gap": "Gap",
        "deltas_note": "Relative difference of the reviewed PRs; bar length = size of "
                       "the difference.",
        "l2": "2. Measured over time: before and after",
        "l2_tag": "measured, same repository, different periods",
        "l2_none": "No history: `reviewme history --since 2024-01-01` scans the PRs of "
                   "the past years.",
        "l2_no_start": "No start date: no live run in the ledger and no [stats] bot_since.",
        "l2_link": "Details per quarter on the History page.",
        "arrow": "{a} → {b}", "before_after": "before → after",
        "before_era": "before → {n}",
        "k_merge": "median hours to merge", "k_first": "median hours to first review",
        "k_threads": "human threads per PR", "k_hotfix": "hotfix or revert branches",
        "l3": "3. Estimated: time saved against cost", "l3_tag": "estimated",
        "l3_none": "No estimate: add [stats.roi] to project.toml (hourly_cost, and "
                   "optionally currency, usd_rate, minutes_per_finding, "
                   "minutes_per_blocker, human_first_review_hours).",
        "hours": "hours saved", "hours_sub": "acted upon: {f}, BLOCKERs: {b}",
        "value": "human time avoided", "api": "API cost", "api_sub": "{usd}",
        "ratio": "value / cost", "net": "net", "even": "break-even",
        "even_sub": "acted upon per PR needed, {a} actual",
        "feedback": "First feedback: bot after {b}, human after {h} ({src})",
        "src_measured": "measured, unreviewed PRs", "src_assumed": "assumed",
        "assumptions": "Assumptions: {c} per hour, {f} min per finding acted upon, {b} "
                       "per BLOCKER fixed before merge, 1 USD = {r} {cur}. Edit "
                       "[stats.roi] in project.toml.",
    },
    "fr": {
        "l1": "1. Mesuré sur les PR : groupe témoin",
        "l1_tag": "mesuré, même dépôt, même période",
        "l1_none": "Pas de groupe témoin sur cette fenêtre : `reviewme enrich` enregistre "
                   "les PR jamais relues par le bot quand `[stats] control_group` est "
                   "actif (par défaut).",
        "l1_note": "PR de prs.jsonl sur la fenêtre. Les auteurs choisissent les PR que "
                   "le bot relit (label, brouillons) : les deux groupes sont comparables, "
                   "pas identiques.",
        "metric": "Mesure", "reviewed": "Relues", "unreviewed": "Non relues",
        "m_prs": "PR", "m_merged": "Mergées",
        "m_first": "Heures médianes avant la première relecture humaine",
        "m_merge": "Heures médianes ouverture → merge",
        "m_reviews": "Relectures humaines par PR",
        "m_comments": "Commentaires humains par PR",
        "m_bot": "Premier passage du bot après l'ouverture (médiane)",
        "deltas": "Relues contre non relues", "gap": "Écart",
        "deltas_note": "Écart relatif des PR relues ; longueur de barre = taille de "
                       "l'écart.",
        "l2": "2. Mesuré dans le temps : avant et après",
        "l2_tag": "mesuré, même dépôt, périodes différentes",
        "l2_none": "Pas d'historique : `reviewme history --since 2024-01-01` lit les PR "
                   "des années passées.",
        "l2_no_start": "Pas de date de départ : aucun passage live dans le ledger et pas "
                       "de [stats] bot_since.",
        "l2_link": "Détail par trimestre sur la page Historique.",
        "arrow": "{a} → {b}", "before_after": "avant → après",
        "before_era": "avant → {n}",
        "k_merge": "heures médianes avant merge",
        "k_first": "heures médianes avant relecture",
        "k_threads": "fils humains par PR", "k_hotfix": "branches hotfix ou revert",
        "l3": "3. Estimé : temps gagné contre coût", "l3_tag": "estimé",
        "l3_none": "Pas d'estimation : ajoutez [stats.roi] dans project.toml "
                   "(hourly_cost, et au besoin currency, usd_rate, minutes_per_finding, "
                   "minutes_per_blocker, human_first_review_hours).",
        "hours": "heures gagnées", "hours_sub": "suivies d'effet : {f}, BLOCKER : {b}",
        "value": "temps humain évité", "api": "coût API", "api_sub": "{usd}",
        "ratio": "valeur / coût", "net": "net", "even": "point mort",
        "even_sub": "suivies d'effet par PR nécessaires, {a} réelles",
        "feedback": "Premier retour : bot après {b}, humain après {h} ({src})",
        "src_measured": "mesuré, PR non relues", "src_assumed": "hypothèse",
        "assumptions": "Hypothèses : {c} de l'heure, {f} min par remarque suivie d'effet, "
                       "{b} par BLOCKER corrigé avant merge, 1 USD = {r} {cur}. À modifier "
                       "dans [stats.roi] de project.toml.",
    },
}

_L1 = (("prs", "m_prs", C.num), ("merged", "m_merged", C.num),
       ("median_first_review_h", "m_first", C.hours), ("median_merge_h", "m_merge", C.hours),
       ("reviews_per_pr", "m_reviews", C.num), ("comments_per_pr", "m_comments", C.num),
       ("bot_first_run_min", "m_bot", None))
#: Metrics whose reviewed/unreviewed difference is drawn (counts are not effects).
_DELTAS = ("median_first_review_h", "median_merge_h", "reviews_per_pr", "comments_per_pr")
_KEYS = (("median_merge_h", "k_merge", C.hours), ("median_first_review_h", "k_first", C.hours),
         ("threads_per_pr", "k_threads", C.num), ("hotfix_share", "k_hotfix", C.pct))


def _mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 2) if values else None


def _group(rows: list[dict], reviewed: bool) -> dict:
    out = {
        "prs": len(rows),
        "merged": sum(1 for c in rows if c["merged_at"]),
        "median_first_review_h": median([h for c in rows
                                         if (h := c["hours_to_first_human_review"])
                                         is not None]),
        "median_merge_h": median([h for c in rows if (h := c["hours_to_merge"]) is not None]),
        "reviews_per_pr": _mean([c["human_reviews"] for c in rows
                                 if c["human_reviews"] is not None]),
        "comments_per_pr": _mean([c["human_comments"] for c in rows
                                  if c["human_comments"] is not None]),
        "bot_first_run_min": None,
    }
    if reviewed:
        delays = [h * 60 for c in rows
                  if (h := hours_between(c["created_at"], c["first_bot_run_at"])) is not None]
        out["bot_first_run_min"] = round(statistics.median(delays)) if delays else None
    return out


def control_group(ctx: Context) -> dict:
    """Level 1: reviewed vs unreviewed PRs of the window."""
    if not any(p.get("control_group") for p in ctx.prs.values()):
        return {"available": False}
    cycles = pr_cycles(ctx)
    r = _group([c for c in cycles if c["reviewed"]], True)
    u = _group([c for c in cycles if not c["reviewed"]], False)
    deltas = {m: round((r[m] - u[m]) / u[m], 4) for m in _DELTAS
              if r[m] is not None and u[m]}
    return {"available": bool(u["prs"]), "reviewed": r, "unreviewed": u, "deltas": deltas}


def over_time(ctx: Context) -> dict:
    """Level 2: four numbers of the history section, before vs after."""
    ba = history.before_after(ctx)
    if not ba["available"]:
        return {"available": False, "reason": "no_history"}
    if ba["before"] is None:
        return {"available": False, "reason": "no_start"}
    return {"available": True, "bot_start": ba["bot_start"], "after_name": ba["after_name"],
            "numbers": {m: {"before": ba["before"][m], "after": ba["after"][m]}
                        for m, _, _ in _KEYS}}


def estimate(ctx: Context) -> dict:
    """Level 3, from `[stats.roi]`. Also feeds the summary's "hours saved" tile."""
    roi = dict(ctx.settings.roi)
    if not roi:
        return {"available": False}
    judged = ctx.judged
    followed = sum(1 for j in judged if j["followed"])
    blockers = sum(1 for j in judged if j.get("severity") == "BLOCKER"
                   and ctx.acted_before_merge(j))
    mpf, mpb = roi["minutes_per_finding"], roi["minutes_per_blocker"]
    hourly, rate = roi["hourly_cost"], roi["usd_rate"]
    minutes = (followed - blockers) * mpf + blockers * mpb
    hours = minutes / 60
    value = hours * hourly
    api_usd = cost_of(ctx.runs)
    api = api_usd * rate
    prs = len({r.get("pr") for r in ctx.runs})
    per_finding = mpf / 60 * hourly
    cycles = pr_cycles(ctx)
    measured = median([h for c in cycles if not c["reviewed"]
                       and (h := c["hours_to_first_human_review"]) is not None])
    human_h, src = ((measured, "measured") if measured is not None
                    else (roi.get("human_first_review_hours"), "assumed"))
    if human_h is None:
        src = ""
    bot = [h * 60 for c in cycles if c["reviewed"]
           and (h := hours_between(c["created_at"], c["first_bot_run_at"])) is not None]
    return {
        "available": True, "currency": ctx.settings.roi_currency, "assumptions": roi,
        "followed": followed, "blockers_fixed_before_merge": blockers,
        "hours_saved": round(hours, 1), "value": round(value, 2),
        "api_cost_usd": round(api_usd, 4), "api_cost": round(api, 2),
        "ratio": round(value / api, 1) if api else None,
        "net": round(value - api, 2),
        "prs": prs,
        "break_even_per_pr": round(api / prs / per_finding, 2) if prs and per_finding else None,
        "followed_per_pr": round(followed / prs, 2) if prs else None,
        "bot_first_run_min": round(statistics.median(bot)) if bot else None,
        "human_first_review_h": human_h, "human_first_review_source": src,
    }


def compute(ctx: Context) -> dict:
    return {"control_group": control_group(ctx), "over_time": over_time(ctx),
            "estimate": estimate(ctx)}


def amount(x: float | None, currency: str) -> str:
    return "—" if x is None else f"{x:,.0f} {currency}".replace(",", " ")


def _heading(title: str, tag: str) -> str:
    return f'<h3>{C.esc(title)} <span class="tag">{C.esc(tag)}</span></h3>'


def _signed(rate: float) -> str:
    return f"{'+' if rate > 0 else '−' if rate < 0 else ''}{abs(round(rate * 100))} %"


def _minutes(m: float | None) -> str:
    if m is None:
        return "—"
    return f"{round(m)} min" if m < 120 else C.hours(m / 60)


def render(res: dict, ctx: Context) -> str:
    T = texts(_T, ctx.language)
    out = [_heading(T["l1"], T["l1_tag"])]
    l1 = res["control_group"]
    if not l1["available"]:
        out.append(C.card(C.empty(T["l1_none"])))
    else:
        r, u = l1["reviewed"], l1["unreviewed"]
        rows = [(T[label], (fmt or _minutes)(r[m]), (fmt or _minutes)(u[m]))
                for m, label, fmt in _L1 if r[m] is not None or u[m] is not None]
        table = C.table([T["metric"], T["reviewed"], T["unreviewed"]], rows, right=(1, 2))
        bars = C.hbars([(T[label], abs(l1["deltas"][m]), _signed(l1["deltas"][m]))
                        for m, label, _ in _L1 if m in l1["deltas"]], T["no_data"])
        # Stacked, not side by side: three columns of long labels need the full width.
        out.append(C.card(table, "", T["l1_note"], R.compare(
            T["m_merge"], T["reviewed"].lower(), r["median_merge_h"],
            T["unreviewed"].lower(), u["median_merge_h"], C.hours, ctx.language))
            + C.card(bars, T["deltas"], T["deltas_note"], R.top(
                T["gap"], [(T[label], abs(l1["deltas"][m]), _signed(l1["deltas"][m]))
                           for m, label, _ in _L1 if m in l1["deltas"]],
                C.num, ctx.language, size=True)))

    out.append(_heading(T["l2"], T["l2_tag"]))
    l2 = res["over_time"]
    if not l2["available"]:
        out.append(C.card(C.empty(T["l2_none" if l2["reason"] == "no_history"
                                    else "l2_no_start"])))
    else:
        sub = (T["before_era"].format(n=l2["after_name"]) if l2["after_name"]
               else T["before_after"])
        out.append(C.tiles([(T["arrow"].format(a=fmt(v["before"]), b=fmt(v["after"])),
                             T[label], sub)
                            for m, label, fmt in _KEYS if (v := l2["numbers"][m])]))
        out.append(f'<p class="note"><a href="#history">{C.esc(T["l2_link"])}</a></p>')

    out.append(_heading(T["l3"], T["l3_tag"]))
    e = res["estimate"]
    if not e["available"]:
        out.append(C.card(C.empty(T["l3_none"])))
        return "".join(out)
    cur, a = e["currency"], e["assumptions"]
    out.append(C.tiles([
        (C.hours(e["hours_saved"]), T["hours"],
         T["hours_sub"].format(f=e["followed"], b=e["blockers_fixed_before_merge"])),
        (amount(e["value"], cur), T["value"]),
        (amount(e["api_cost"], cur), T["api"], T["api_sub"].format(usd=C.money(e["api_cost_usd"]))),
        ("—" if e["ratio"] is None else f"× {C.num(e['ratio'])}", T["ratio"]),
        (amount(e["net"], cur), T["net"]),
        (C.num(e["break_even_per_pr"], 2), T["even"],
         T["even_sub"].format(a=C.num(e["followed_per_pr"], 2))),
    ]))
    notes = [T["assumptions"].format(c=amount(a["hourly_cost"], cur),
                                     f=C.num(a["minutes_per_finding"]),
                                     b=_minutes(a["minutes_per_blocker"]),
                                     r=C.num(a["usd_rate"], 2), cur=cur)]
    if e["human_first_review_source"] and e["bot_first_run_min"] is not None:
        notes.append(T["feedback"].format(b=_minutes(e["bot_first_run_min"]),
                                          h=C.hours(e["human_first_review_h"]),
                                          src=T["src_" + e["human_first_review_source"]]) + ".")
    out.append(f'<p class="note">{C.esc(" ".join(notes))}</p>')
    return "".join(out)
