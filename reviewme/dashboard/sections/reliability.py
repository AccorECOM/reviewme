"""Reliability: the runs that did not go as planned.

Each of these costs money or findings without saying so on the PR: a CLI error, a GitHub
write that failed (`post_failed`, the review exists only in the ledger), an answer that
broke the output contract, a run cut by `max_turns`, findings pushed into a summary
comment. Durations (p50/p95) per reviewer and the context bytes actually injected say
whether a slow reviewer is slow because of what it is given.
"""
from __future__ import annotations

import math
from collections import defaultdict

from .. import charts as C
from .. import readings as R
from ..data import Context
from ..i18n import texts

ID = "reliability"
TITLE = {"en": "Reliability", "fr": "Fiabilité"}
INCIDENTS = ("is_error", "post_failed", "parse_failed", "max_turns")

_T = {
    "en": {
        "is_error": "CLI errors", "post_failed": "GitHub writes failed",
        "parse_failed": "answers off contract", "max_turns": "cut by max_turns",
        "in_summary": "findings in a summary comment",
        "in_summary_sub": "global mode or inline fallback",
        "context": "context injected", "context_sub": "{avg} per reviewer run",
        "durations": "Duration per reviewer", "p50": "p50", "p95": "p95",
        "incidents": "Incidents {per}", "incidents_note": "Reviewer runs with at least one "
                                                         "of the four incidents above.",
        "per_period": "Per period", "incident_runs": "Runs with an incident", "incident": "Incidents",
    },
    "fr": {
        "is_error": "erreurs du CLI", "post_failed": "écritures GitHub échouées",
        "parse_failed": "réponses hors contrat", "max_turns": "coupés par max_turns",
        "in_summary": "remarques dans un commentaire global",
        "in_summary_sub": "mode global ou repli",
        "context": "contexte injecté", "context_sub": "{avg} par passage de relecteur",
        "durations": "Durée par relecteur", "p50": "p50", "p95": "p95",
        "incidents": "Incidents {per}", "incidents_note": "Passages de relecteur avec au "
                                                         "moins un des quatre incidents "
                                                         "ci-dessus.",
        "per_period": "Par période", "incident_runs": "Passages avec incident", "incident": "Incidents",
    },
}


def _flags(r: dict) -> dict[str, bool]:
    return {"is_error": bool(r.get("is_error")),
            "post_failed": r.get("status") == "post_failed",
            "parse_failed": r.get("parsed_ok") is False,
            "max_turns": r.get("subtype") == "error_max_turns"}


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile: on a handful of runs, an interpolated p95 invents a value
    nobody observed."""
    if not values:
        return None
    s = sorted(values)
    k = min(len(s) - 1, max(0, math.ceil(round(p * len(s), 6)) - 1))
    return s[k]


def _bytes(n: float) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024 or unit == "MB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} MB"


def compute(ctx: Context) -> dict:
    runs = ctx.runs
    totals = dict.fromkeys(INCIDENTS, 0)
    per_period: dict = defaultdict(lambda: {"runs": 0, "incidents": 0})
    durations: dict[str, list] = defaultdict(list)
    ctx_bytes = 0
    for r in runs:
        flags = _flags(r)
        for k, v in flags.items():
            totals[k] += v
        p = per_period[ctx.period_of(r.get("ts"))]
        p["runs"] += 1
        p["incidents"] += 1 if any(flags.values()) else 0
        durations[r.get("reviewer") or "?"].append(r.get("duration_s", 0) or 0)
        ctx_bytes += sum((c.get("bytes", 0) or 0) for c in r.get("context_files") or [])
    periods = ctx.periods()
    return {
        **totals,
        "reviewer_runs": len(runs),
        "in_summary": ctx.quality["posted_in_summary"],
        "context_bytes": ctx_bytes,
        "context_bytes_per_run": round(ctx_bytes / len(runs)) if runs else None,
        "durations": {k: {"runs": len(v), "p50_s": percentile(v, .5),
                          "p95_s": percentile(v, .95)} for k, v in sorted(durations.items())},
        "periods": periods,
        "per_period": [{"period": p, **per_period[p]} for p in periods],
    }


def render(res: dict, ctx: Context) -> str:
    T = texts(_T, ctx.language)
    if not res["reviewer_runs"]:
        return C.card(C.empty(T["no_data"]))
    per = T["per_" + ctx.settings.period]
    avg = res["context_bytes_per_run"]
    durations = C.table([T["reviewer"], T["runs"], T["p50"], T["p95"]],
                        [(k, v["runs"], f'{C.num(v["p50_s"])} s', f'{C.num(v["p95_s"])} s')
                         for k, v in res["durations"].items()], right=(1, 2, 3))
    periods = C.table([T["period"], T["runs"], T["incident"]],
                      [(p["period"], p["runs"], p["incidents"]) for p in res["per_period"]],
                      right=(1, 2))
    chart = C.bars([(p["period"], p["incidents"]) for p in res["per_period"]], C.num,
                   markers=ctx.config_markers(res["periods"]), marker_label=T["marker"])
    return "".join([
        C.tiles([*((str(res[k]), T[k]) for k in INCIDENTS),
                 (str(res["in_summary"]), T["in_summary"], T["in_summary_sub"]),
                 (_bytes(res["context_bytes"]), T["context"],
                  T["context_sub"].format(avg=_bytes(avg or 0)))]),
        C.card(chart, T["incidents"].format(per=per), T["incidents_note"],
               R.trend([(p["period"], p["incidents"]) for p in res["per_period"]], C.num,
                       ctx.language, current=ctx.current_period())),
        '<div class="grid2"><div>',
        C.card(durations, T["durations"], reading=R.top(
            T["p95"], [(k, v["p95_s"], f'{C.num(v["p95_s"])} s')
                       for k, v in res["durations"].items()], C.num, ctx.language)),
        "</div><div>",
        C.card(periods, T["per_period"], reading=R.share(
            T["incident_runs"], sum(p["incidents"] for p in res["per_period"]),
            sum(p["runs"] for p in res["per_period"]), ctx.language)),
        "</div></div>",
    ])
