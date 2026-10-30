"""History: the team's PR workflow before and after the bot (`reviewme history`).

Reads `history.jsonl` (merged and closed PRs over years), NOT the page's window: the
point is to see a trend that started long before the bot. Per quarter (per month when
the history covers less than nine months), on the PRs MERGED in that period: how many,
how long from opening to merge, how long before the first human review, human reviews
and human review threads per PR (and per 100 changed lines, so bigger PRs do not pass for
more scrutiny), the share of hotfix or revert branches, the share of ordinary `fix/`
branches apart (a team that names every bug fix `fix/…` is not shipping that many
emergencies), and bug tickets created per month when `--jira` filled
`history-bugs.jsonl`. A dashed line marks the period the bot starts.

The comparison table splits PRs by OPENING date (a PR opened before the bot was not
written with it in mind), and after the bot, reviewed vs not reviewed.

**Eras** (`[[stats.eras]]`): named, consecutive periods (a prototype bot, a pause, then
ReviewMe). A PR belongs to the era whose `since` is the latest on or before its
creation, "before" when it predates the first one. The charts get one marker per era,
captioned with its name, and the before/after table becomes one row per era; an era
with a `label` gets two (PRs reviewed in it, by a ledger run or that label, and the
others), since a prototype bot leaves no ledger, only its label. An era's `bot_login`
is the person's account such a bot posted under: on that era's labelled PRs, its
reviews and threads (`review_authors`, `thread_authors`) are taken out of the human
counts, and the PR counts as reviewed. Rows scanned before those per-login counts
existed cannot be corrected: the page says so (rescan needed). Without eras,
`bot_since` (or the first live run) is one era named "ReviewMe" and the page reads as
before.

PRs opened by bots (dependency updates) and drafts are left out, as in the control group.
Before/after is not proof: the caveat says so on the page, and points at the control
group on the ROI page, which compares PRs of the same weeks.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime

from ...pr_history import is_fix_branch, is_hotfix_branch, is_revert_branch
from .. import charts as C
from .. import readings as R
from ..data import Context, EraSpan, era_of, hours_between, median, parse_ts
from ..i18n import texts

ID = "history"
TITLE = {"en": "History", "fr": "Historique"}

#: Below this span, quarters are too few to draw a line: months instead.
_QUARTER_MIN_DAYS = 270
_DAYS_PER_MONTH = 30.44

_T = {
    "en": {
        "none": "No history yet. `reviewme history --since 2024-01-01` scans the "
                "repository's merged and closed PRs (one GraphQL query per 100 PRs) into "
                "the ledger; this page then compares the workflow before and after the bot.",
        "caveat": "Before/after is not proof: the team, the product and the release "
                  "pace changed too. The control group on the ROI page compares reviewed "
                  "and unreviewed PRs of the same weeks.",
        "roi_link": "See the ROI page.",
        "start": "ReviewMe starts {d} ({src}).", "src_settings": "[stats] bot_since",
        "src_ledger": "first live run in the ledger",
        "no_start": "No live run in the ledger and no [stats] bot_since: no before/after.",
        "marker": "ReviewMe starts",
        "marker_note": "Dashed line: the period ReviewMe starts in.",
        "eras_note": "Dashed lines: the period each era starts in.",
        "eras": "Eras ([stats.eras]): {list}.", "era_item": "{n} from {d}",
        "era_label": "label {l}", "before_era": "Before",
        "per_era": "Per era (by opening date)", "e_era": "Era", "e_created": "PRs created",
        "e_merged": "PRs merged", "e_revert": "Revert branches",
        "e_hotfix": "Hotfix branches", "e_fix": "fix/ branches",
        "e_with": "{n}, reviewed ({l} or a ledger run)", "e_without": "{n}, not reviewed",
        "e_with_short": "PRs with {l}", "e_without_short": "those without",
        "e_note": "Shares are on the merged PRs of each row. Hotfix = `hotfix` in the "
                  "branch name; fix/ = ordinary fix branches.",
        "bot_login_note": "{era}: reviews and threads by {login} on PRs labelled {label} "
                          "are counted as the bot's, not as human ones.",
        "rescan": "{era}: {n} labelled PR(s) were scanned before per-login counts "
                  "existed, so {login} still counts as a human reviewer there: rescan "
                  "needed (`reviewme history`).",
        "scope": "{n} PRs from {a} to {b}, merged and closed, bots and drafts left out. "
                 "Per {g}, on the PRs merged in it. Hotfix, revert and fix/ are guessed "
                 "from the branch name (titles are not stored): a weak signal. Hotfix = "
                 "`hotfix` in the name; fix/ = ordinary fix branches (fix/…, bugfix/…), "
                 "counted apart.",
        "g_quarter": "quarter", "g_month": "month",
        "compare": "Before and after (by opening date)",
        "c_metric": "Metric", "c_before": "Before", "c_after": "After",
        "c_reviewed": "After, reviewed", "c_unreviewed": "After, not reviewed",
        "m_merged": "PRs merged per month", "m_merge": "Median hours open to merge",
        "m_first": "Median hours to first human review",
        "m_unreviewed": "Merged without a human review",
        "m_reviews": "Human reviews per PR", "m_threads": "Human review threads per PR",
        "m_per100": "Human threads per 100 changed lines",
        "m_hotfix": "Hotfix or revert branches",
        "m_fix": "fix/ branches (ordinary fixes, not hotfixes)",
        "m_bugs": "Bug tickets per month",
        "ch_merged": "PRs merged per month", "ch_merge": "Median hours open to merge",
        "ch_first": "Median hours to first human review",
        "ch_reviews": "Human reviews per PR", "ch_threads": "Human review threads per PR",
        "ch_per100": "Human threads per 100 changed lines",
        "ch_hotfix": "Share of hotfix or revert branches",
        "ch_fix": "Share of fix/ branches (ordinary fixes, not hotfixes)",
        "ch_bugs": "Bug tickets created per month",
    },
    "fr": {
        "none": "Pas encore d'historique. `reviewme history --since 2024-01-01` lit les "
                "PR mergées et fermées du dépôt (une requête GraphQL pour 100 PR) dans le "
                "ledger ; cette page compare ensuite le fonctionnement avant et après le bot.",
        "caveat": "Avant/après n'est pas une preuve : l'équipe, le produit et le rythme "
                  "de livraison ont changé aussi. Le groupe témoin de la page ROI compare "
                  "les PR relues et non relues des mêmes semaines.",
        "roi_link": "Voir la page ROI.",
        "start": "ReviewMe démarre le {d} ({src}).", "src_settings": "[stats] bot_since",
        "src_ledger": "premier passage live du ledger",
        "no_start": "Aucun passage live dans le ledger et pas de [stats] bot_since : pas "
                    "d'avant/après.",
        "marker": "ReviewMe démarre",
        "marker_note": "Ligne pointillée : la période où ReviewMe démarre.",
        "eras_note": "Lignes pointillées : la période où commence chaque ère.",
        "eras": "Ères ([stats.eras]) : {list}.", "era_item": "{n} à partir du {d}",
        "era_label": "label {l}", "before_era": "Avant",
        "per_era": "Par ère (par date d'ouverture)", "e_era": "Ère",
        "e_created": "PR ouvertes", "e_merged": "PR mergées",
        "e_revert": "Branches revert", "e_hotfix": "Branches hotfix",
        "e_fix": "Branches fix/",
        "e_with": "{n}, relues ({l} ou passage du ledger)", "e_without": "{n}, non relues",
        "e_with_short": "les PR avec {l}", "e_without_short": "les autres",
        "e_note": "Les parts portent sur les PR mergées de chaque ligne. Hotfix = "
                  "`hotfix` dans le nom de branche ; fix/ = branches de correction "
                  "ordinaires.",
        "bot_login_note": "{era} : les relectures et fils de {login} sur les PR "
                          "labellisées {label} comptent pour le bot, pas pour un humain.",
        "rescan": "{era} : {n} PR labellisée(s) lue(s) avant les comptes par login, "
                  "{login} y compte donc encore comme relecteur humain : relancer "
                  "`reviewme history`.",
        "scope": "{n} PR du {a} au {b}, mergées et fermées, hors bots et brouillons. Par "
                 "{g}, sur les PR mergées pendant celui-ci. Hotfix, revert et fix/ sont "
                 "devinés d'après le nom de branche (les titres ne sont pas stockés) : un "
                 "signal faible. Hotfix = `hotfix` dans le nom ; fix/ = branches de "
                 "correction ordinaires (fix/…, bugfix/…), comptées à part.",
        "g_quarter": "trimestre", "g_month": "mois",
        "compare": "Avant et après (par date d'ouverture)",
        "c_metric": "Mesure", "c_before": "Avant", "c_after": "Après",
        "c_reviewed": "Après, relues", "c_unreviewed": "Après, non relues",
        "m_merged": "PR mergées par mois", "m_merge": "Heures médianes ouverture → merge",
        "m_first": "Heures médianes avant la première relecture humaine",
        "m_unreviewed": "Mergées sans relecture humaine",
        "m_reviews": "Relectures humaines par PR",
        "m_threads": "Fils de relecture humains par PR",
        "m_per100": "Fils humains pour 100 lignes modifiées",
        "m_hotfix": "Branches hotfix ou revert",
        "m_fix": "Branches fix/ (corrections ordinaires, pas des hotfix)",
        "m_bugs": "Tickets de bug par mois",
        "ch_merged": "PR mergées par mois", "ch_merge": "Heures médianes ouverture → merge",
        "ch_first": "Heures médianes avant la première relecture humaine",
        "ch_reviews": "Relectures humaines par PR",
        "ch_threads": "Fils de relecture humains par PR",
        "ch_per100": "Fils humains pour 100 lignes modifiées",
        "ch_hotfix": "Part de branches hotfix ou revert",
        "ch_fix": "Part de branches fix/ (corrections ordinaires, pas des hotfix)",
        "ch_bugs": "Tickets de bug créés par mois",
    },
}

#: (metric id, format) in table order; the charts reuse the ids.
METRICS = (("merged_per_month", "num"), ("median_merge_h", "hours"),
           ("median_first_review_h", "hours"), ("no_human_review_share", "pct"),
           ("reviews_per_pr", "num"), ("threads_per_pr", "num"),
           ("threads_per_100_lines", "num"), ("hotfix_share", "pct"),
           ("fix_share", "pct"), ("bugs_per_month", "num"))
_LABELS = {"merged_per_month": "m_merged", "median_merge_h": "m_merge",
           "median_first_review_h": "m_first", "no_human_review_share": "m_unreviewed",
           "reviews_per_pr": "m_reviews", "threads_per_pr": "m_threads",
           "threads_per_100_lines": "m_per100", "hotfix_share": "m_hotfix",
           "fix_share": "m_fix", "bugs_per_month": "m_bugs"}
_FMT = {"num": C.num, "hours": C.hours, "pct": C.pct}


def _mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 2) if values else None


def _threads(r: dict) -> int | None:
    t = r.get("human_threads")
    return t if t is not None else r.get("review_threads")


def _size(r: dict) -> int | None:
    a, d = r.get("additions"), r.get("deletions")
    return a + d if a is not None and d is not None else None


def branch_kind(r: dict) -> dict:
    """(revert, hotfix, fix) flags of a history row, from its head branch when it has one.

    Read from `head_ref` rather than the stored flags: rows written before hotfix and
    fix/ were split carry `is_hotfix_branch = true` for every `fix/` branch, and asking
    everyone to re-run a multi-hour scan to correct one regex is not a fix. Rows without
    `head_ref` fall back to what the scan stored.
    """
    head = r.get("head_ref")
    if isinstance(head, str):
        return {"revert": is_revert_branch(head), "hotfix": is_hotfix_branch(head),
                "fix": is_fix_branch(head)}
    return {"revert": bool(r.get("is_revert")), "hotfix": bool(r.get("is_hotfix_branch")),
            "fix": bool(r.get("is_fix_branch"))}


#: Per-login fields of a `history.jsonl` row; a row lacking one predates them.
_PER_LOGIN = ("review_authors", "thread_authors", "review_first_at")


def _bot_login_era(ctx: Context, r: dict) -> EraSpan | None:
    """The PR's era when its `bot_login` correction applies: the era names a login AND
    the PR carries the era's label (unlabelled, that login is the person reviewing)."""
    if not ctx.settings.eras:
        return None
    era = era_of(ctx.eras, parse_ts(r.get("created_at")))
    if era is None or not era.bot_login or era.label not in (r.get("labels") or []):
        return None
    return era


def _corrected(ctx: Context, r: dict) -> dict:
    """`r` with the era's `bot_login` taken out of the human counts, when it applies and
    the row has the per-login counts; `r` itself otherwise (stored totals kept).

    Clamped at 0: `human_reviews` already left out reviews carrying the bot's marker, so
    a login whose reviews were partly signed would otherwise go negative. The first
    human review becomes the earliest review of ANOTHER login (`review_first_at`), None
    when the bot was the only reviewer; rows without that field keep the stored date.
    """
    era = _bot_login_era(ctx, r)
    if era is None or "review_authors" not in r or "thread_authors" not in r:
        return r
    login = era.bot_login
    out = {**r, "bot_reviewed": True}
    if r.get("human_reviews") is not None:
        out["human_reviews"] = max(0, r["human_reviews"]
                                   - int((r["review_authors"] or {}).get(login, 0)))
    if r.get("human_threads") is not None:
        out["human_threads"] = max(0, r["human_threads"]
                                   - int((r["thread_authors"] or {}).get(login, 0)))
    if "review_first_at" in r:
        others = [(dt, ts) for who, ts in (r["review_first_at"] or {}).items()
                  if who != login and (dt := parse_ts(ts)) is not None]
        out["first_human_review_at"] = min(others)[1] if others else None
    return out


def rescan_needed(ctx: Context) -> dict[str, int]:
    """Era name -> labelled PRs of a `bot_login` era scanned before per-login counts."""
    out: dict[str, int] = {}
    for r in ctx.history:
        era = _bot_login_era(ctx, r)
        if era is not None and any(k not in r for k in _PER_LOGIN):
            out[era.name] = out.get(era.name, 0) + 1
    return out


def eligible(ctx: Context) -> list[dict]:
    """History rows a workflow comparison is made on: people's PRs, not drafts, with the
    eras' `bot_login` corrections applied (every metric of the page reads these)."""
    return [_corrected(ctx, r) for r in ctx.history
            if not r.get("author_is_bot") and not r.get("is_draft")]


def _ledger_reviewed(ctx: Context, r: dict) -> bool:
    return (r.get("repo") or "", r.get("pr")) in ctx.ever_reviewed \
        or ("", r.get("pr")) in ctx.ever_reviewed


def reviewed(ctx: Context, r: dict) -> bool:
    """Reviewed by the bot.

    Without eras: as recorded by the scan (a ledger run, or `REVIEW_LABEL` at scan time),
    or a live run in the ledger since. With eras, the scan's flag is not used: it mixes
    in today's `REVIEW_LABEL`, which means nothing for a PR of a prototype era. A PR is
    then reviewed when the ledger has a live run for it, or when its era has a `label`
    and the PR carries it.
    """
    if not ctx.settings.eras:
        return bool(r.get("bot_reviewed")) or _ledger_reviewed(ctx, r)
    era = era_of(ctx.eras, parse_ts(r.get("created_at")))
    return _ledger_reviewed(ctx, r) or bool(
        era is not None and era.label and era.label in (r.get("labels") or []))


def metrics(rows: list[dict], months: float | None = None,
            bugs: list[int] | None = None) -> dict:
    """The comparison numbers on a set of PRs (merged ones only count)."""
    merged = [r for r in rows if r.get("merged_at")]
    first = [h for r in merged if (h := hours_between(r.get("created_at"),
                                                      r.get("first_human_review_at")))
             is not None]
    threads = [t for r in merged if (t := _threads(r)) is not None]
    sized = [(t, s) for r in merged if (t := _threads(r)) is not None
             and (s := _size(r))]
    lines = sum(s for _, s in sized)
    kinds = [branch_kind(r) for r in merged]
    return {
        "merged": len(merged),
        "merged_per_month": round(len(merged) / months, 1) if months else None,
        "median_merge_h": median([h for r in merged if (h := hours_between(
            r.get("created_at"), r.get("merged_at"))) is not None]),
        "median_first_review_h": median(first),
        "no_human_review_share": C.ratio(sum(1 for r in merged
                                             if not r.get("first_human_review_at")),
                                         len(merged)),
        "reviews_per_pr": _mean([r["human_reviews"] for r in merged
                                 if r.get("human_reviews") is not None]),
        "threads_per_pr": _mean(threads),
        "threads_per_100_lines": round(100 * sum(t for t, _ in sized) / lines, 2)
        if lines else None,
        # Emergencies only (revert or hotfix): `fix/` is how many teams name every bug
        # fix, so it gets its own share instead of drowning this one.
        "hotfix_share": C.ratio(sum(1 for k in kinds if k["revert"] or k["hotfix"]),
                                len(merged)),
        "fix_share": C.ratio(sum(1 for k in kinds if k["fix"]), len(merged)),
        # Split apart for the per-era table, which shows each kind on its own.
        "revert_share": C.ratio(sum(1 for k in kinds if k["revert"]), len(merged)),
        "hotfix_only_share": C.ratio(sum(1 for k in kinds if k["hotfix"]), len(merged)),
        "bugs_per_month": _mean([float(b) for b in bugs]) if bugs else None,
    }


def _months(a: datetime | None, b: datetime | None) -> float | None:
    if a is None or b is None or b <= a:
        return None
    return max(1.0, (b - a).days / _DAYS_PER_MONTH)


def before_after(ctx: Context) -> dict:
    """Same metrics before the first era and in the LAST one, the last split by review.

    Without `[[stats.eras]]`, the single era starts at `ctx.bot_start`: before vs after
    the bot. With eras, "after" is the last era only: the ROI page asks whether the
    current setup helps, and an intermediate era (a pause, a prototype) would blur it.
    `available` False without history; `before`/`after` None without a start date.
    """
    rows = eligible(ctx)
    if not rows:
        return {"available": False}
    created = [dt for r in rows if (dt := parse_ts(r.get("created_at"))) is not None]
    first, last = (min(created), max(created)) if created else (None, None)
    first_era = ctx.eras[0].start if ctx.eras else None
    # "After" = the last era that has PRs: an era configured ahead of time (it starts
    # next week, or has not had a PR yet) would leave ROI level 2 with nothing to show.
    # Its own end is the next era's start, so a later empty era does not stretch it.
    last_i = next((i for i in range(len(ctx.eras) - 1, -1, -1)
                   if any(era_of(ctx.eras, parse_ts(r.get("created_at"))) == ctx.eras[i]
                          for r in rows)), len(ctx.eras) - 1)
    start = ctx.eras[last_i].start if ctx.eras else None
    stop = ctx.eras[last_i + 1].start if ctx.eras and last_i + 1 < len(ctx.eras) else None
    out = {"available": True, "prs": len(rows),
           "from": first.date().isoformat() if first else None,
           "to": last.date().isoformat() if last else None,
           "bot_start": start.isoformat() if start else None,
           "bot_start_source": ctx.bot_start_source,
           "before": None, "after": None, "after_reviewed": None,
           "after_unreviewed": None,
           "after_name": ctx.eras[last_i].name if ctx.settings.eras else ""}
    if start is None:
        return out
    first_month, start_month = first_era.strftime("%Y-%m"), start.strftime("%Y-%m")
    before = [r for r in rows if (dt := parse_ts(r.get("created_at"))) and dt < first_era]
    after = [r for r in rows if (dt := parse_ts(r.get("created_at"))) and dt >= start
             and (stop is None or dt < stop)]
    bugs_b = [b for m, b in ctx.history_bugs.items() if m < first_month]
    stop_month = stop.strftime("%Y-%m") if stop else None
    bugs_a = [b for m, b in ctx.history_bugs.items() if m >= start_month
              and (stop_month is None or m < stop_month)]
    end = max(ctx.now, last) if last else ctx.now
    end = min(end, stop) if stop else end      # an era ends where the next one starts
    m_after = _months(start, end)
    rev = [r for r in after if reviewed(ctx, r)]
    unrev = [r for r in after if not reviewed(ctx, r)]
    out.update(before=metrics(before, _months(first, first_era), bugs_b),
               after=metrics(after, m_after, bugs_a),
               after_reviewed=metrics(rev, m_after), after_unreviewed=metrics(unrev, m_after))
    return out


#: Columns of the per-era table: (metric id, header key, format).
ERA_COLUMNS = (("prs", "e_created", "num"), ("merged", "e_merged", "num"),
               ("median_merge_h", "m_merge", "hours"),
               ("median_first_review_h", "m_first", "hours"),
               ("reviews_per_pr", "m_reviews", "num"), ("threads_per_pr", "m_threads", "num"),
               ("revert_share", "e_revert", "pct"), ("hotfix_only_share", "e_hotfix", "pct"),
               ("fix_share", "e_fix", "pct"))


def per_era(ctx: Context) -> list[dict]:
    """One row per era ("before" first), two for an era with a label (reviewed in it /
    not), each with the `ERA_COLUMNS` numbers on the PRs OPENED in it. [] without
    `[[stats.eras]]`: the before/after table covers the implicit one-era case."""
    if not ctx.settings.eras:
        return []
    groups: dict[str | None, list[dict]] = {None: [], **{e.name: [] for e in ctx.eras}}
    for r in eligible(ctx):
        era = era_of(ctx.eras, parse_ts(r.get("created_at")))
        groups[era.name if era else None].append(r)

    def row(era: EraSpan | None, variant: str, rows: list[dict]) -> dict:
        return {"era": era.name if era else None, "since": era.start.date().isoformat()
                if era else None, "label": era.label if era else "", "variant": variant,
                "prs": len(rows), **metrics(rows)}

    out = [row(None, "all", groups[None])]
    for era in ctx.eras:
        rows = groups[era.name]
        if era.label:
            out.append(row(era, "with", [r for r in rows if reviewed(ctx, r)]))
            out.append(row(era, "without", [r for r in rows if not reviewed(ctx, r)]))
        else:
            out.append(row(era, "all", rows))
    return out


def _period(dt: datetime, quarters: bool) -> str:
    return f"{dt.year}-Q{(dt.month - 1) // 3 + 1}" if quarters else f"{dt.year}-{dt.month:02d}"


def _covered(period: str, quarters: bool, now: datetime) -> float:
    """Months of `period` elapsed by `now`: the running quarter counts as a part, so its
    PRs per month do not read as a collapse."""
    y = int(period[:4])
    m = (int(period[-1]) - 1) * 3 + 1 if quarters else int(period[5:7])
    start = datetime(y, m, 1, tzinfo=now.tzinfo)
    length = 3 if quarters else 1
    elapsed = (now - start).days / _DAYS_PER_MONTH if now > start else 0.0
    return max(0.1, min(float(length), elapsed or float(length)))


def _all_periods(a: datetime, b: datetime, quarters: bool) -> list[str]:
    out, y, m = [], a.year, a.month
    while (y, m) <= (b.year, b.month):
        p = _period(datetime(y, m, 1), quarters)
        if not out or out[-1] != p:
            out.append(p)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def compute(ctx: Context) -> dict:
    ba = before_after(ctx)
    if not ba["available"]:
        return ba
    rows = eligible(ctx)
    merged = [(dt, r) for r in rows if (dt := parse_ts(r.get("merged_at"))) is not None]
    stamps = [dt for r in rows if (dt := parse_ts(r.get("created_at"))) is not None]
    stamps += [dt for dt, _ in merged]
    a, b = min(stamps), max(stamps)
    quarters = (b - a).days >= _QUARTER_MIN_DAYS
    periods = _all_periods(a, b, quarters)
    by_p: dict[str, list[dict]] = defaultdict(list)
    for dt, r in merged:
        by_p[_period(dt, quarters)].append(r)
    bugs_p: dict[str, list[int]] = defaultdict(list)
    for month, n in ctx.history_bugs.items():
        y, m = (int(x) for x in month.split("-"))
        bugs_p[_period(datetime(y, m, 1), quarters)].append(n)
    per = {p: metrics(by_p.get(p, []), _covered(p, quarters, ctx.now), bugs_p.get(p))
           for p in periods}
    start_p = _period(ctx.bot_start, quarters) if ctx.bot_start else None
    era_p = [(e.name, _period(e.start, quarters)) for e in ctx.eras]

    def series(metric: str) -> dict:
        pts = [(p, v) for p in periods
               if (v := per[p][metric]) is not None]
        # One marker per era, on the first point at or after its period. Two eras that
        # start in the same period share one line, captioned with both names; a marker
        # on the first point says nothing (no "before" to see) and is left out.
        at: dict[int, list[str]] = {}
        for name, ep in era_p:
            idx = next((i for i, (p, _) in enumerate(pts) if p >= ep), None)
            if idx:
                at.setdefault(idx, []).append(name)
        return {"points": pts, "marker": sorted(at),
                "captions": [" / ".join(at[i]) for i in sorted(at)]}

    return {**ba, "granularity": "quarter" if quarters else "month", "periods": periods,
            "start_period": start_p, "per_era": per_era(ctx),
            "rescan_needed": rescan_needed(ctx),
            # Only the configured eras: the implicit "ReviewMe" one keeps the old note.
            "eras": [{"name": e.name, "since": e.start.date().isoformat(),
                      "label": e.label, "bot_login": e.bot_login} for e in ctx.eras]
            if ctx.settings.eras else [],
            "series": {m: series(m) for m in ("merged_per_month", "median_merge_h",
                                              "median_first_review_h", "reviews_per_pr",
                                              "threads_per_pr", "threads_per_100_lines",
                                              "hotfix_share", "fix_share",
                                              "bugs_per_month")}}


def comparison_table(res: dict, T: dict) -> str:
    cols = ("before", "after", "after_reviewed", "after_unreviewed")
    rows = []
    for metric, kind in METRICS:
        values = [(res[c] or {}).get(metric) for c in cols]
        if all(v is None for v in values):
            continue
        rows.append([T[_LABELS[metric]], *(_FMT[kind](v) for v in values)])
    return C.table([T["c_metric"], T["c_before"], T["c_after"], T["c_reviewed"],
                    T["c_unreviewed"]], rows, right=(1, 2, 3, 4))


def era_table(res: dict, T: dict) -> str:
    rows = []
    for e in res["per_era"]:
        name = e["era"] or T["before_era"]
        if e["variant"] == "with":
            name = T["e_with"].format(n=name, l=e["label"])
        elif e["variant"] == "without":
            name = T["e_without"].format(n=name)
        rows.append([name, *(_FMT[kind](e[m]) for m, _, kind in ERA_COLUMNS)])
    return C.table([T["e_era"], *(T[h] for _, h, _ in ERA_COLUMNS)], rows,
                   right=tuple(range(1, len(ERA_COLUMNS) + 1)))


def _era_reading(res: dict, T: dict, language: str) -> str:
    """The last labelled era, reviewed vs not, when there is one; else "before" against
    the last era: the two comparisons the table exists for."""
    rows = res["per_era"]
    labelled = [e for e in rows if e["variant"] == "with" and e["merged"]]
    if labelled:
        w = labelled[-1]
        wo = next(e for e in rows if e["era"] == w["era"] and e["variant"] == "without")
        return R.compare(T["m_merge"], T["e_with_short"].format(l=w["label"]),
                         w["median_merge_h"], T["e_without_short"],
                         wo["median_merge_h"], C.hours, language)
    last = [e for e in rows if e["era"] is not None][-1]
    return R.compare(T["m_merge"], T["before_era"].lower(), rows[0]["median_merge_h"],
                     last["era"], last["median_merge_h"], C.hours, language)


def render(res: dict, ctx: Context) -> str:
    T = texts(_T, ctx.language)
    if not res["available"]:
        return C.card(C.empty(T["none"]))
    caveat = (f'<p class="note">{C.esc(T["caveat"])} <a href="#roi">'
              f'{C.esc(T["roi_link"])}</a></p>')
    if res["eras"]:
        start = T["eras"].format(list=", ".join(
            T["era_item"].format(n=e["name"], d=e["since"])
            + (f' ({T["era_label"].format(l=e["label"])})' if e["label"] else "")
            for e in res["eras"]))
    elif res["bot_start"]:
        start = T["start"].format(d=res["bot_start"][:10],
                                  src=T["src_" + res["bot_start_source"]])
    else:
        start = T["no_start"]
    scope = T["scope"].format(n=res["prs"], a=res["from"], b=res["to"],
                              g=T["g_" + res["granularity"]])
    out = [f'<p class="note">{C.esc(start)} {C.esc(scope)}</p>', caveat]
    if res["per_era"]:
        notes = [T["e_note"]]
        for e in res["eras"]:
            if e["bot_login"]:
                notes.append(T["bot_login_note"].format(era=e["name"], login=e["bot_login"],
                                                        label=e["label"]))
                if n := res["rescan_needed"].get(e["name"]):
                    notes.append(T["rescan"].format(era=e["name"], n=n,
                                                    login=e["bot_login"]))
        out.append(C.card(era_table(res, T), T["per_era"], " ".join(notes),
                          _era_reading(res, T, ctx.language)))
    elif res["before"] is not None:
        out.append(C.card(comparison_table(res, T), T["compare"], reading=R.compare(
            T["m_merge"], T["c_before"].lower(), res["before"]["median_merge_h"],
            T["c_after"].lower(), res["after"]["median_merge_h"], C.hours, ctx.language)))
    charts = (("merged_per_month", "ch_merged", C.num), ("median_merge_h", "ch_merge", C.hours),
              ("median_first_review_h", "ch_first", C.hours),
              ("reviews_per_pr", "ch_reviews", C.num),
              ("threads_per_pr", "ch_threads", C.num),
              ("threads_per_100_lines", "ch_per100", C.num),
              ("hotfix_share", "ch_hotfix", C.pct), ("fix_share", "ch_fix", C.pct),
              ("bugs_per_month", "ch_bugs", C.num))
    cells = []
    running = _period(ctx.now, res["granularity"] == "quarter")
    for metric, title_key, fmt in charts:
        s = res["series"][metric]
        if not s["points"]:
            continue
        if res["eras"]:
            # Each line says which era starts there: the names are the point of eras.
            note = T["eras_note"] if s["marker"] else ""
            chart = C.line(s["points"], fmt, width=C.HALF, markers=s["marker"],
                           marker_captions=s["captions"], empty_text=T["no_data"])
        else:
            note = T["marker_note"] if s["marker"] else ""
            chart = C.line(s["points"], fmt, width=C.HALF, markers=s["marker"],
                           marker_label=T["marker"], marker_caption=T["marker"],
                           empty_text=T["no_data"])
        cells.append("<div>" + C.card(chart, T[title_key], note,
                                      R.trend(s["points"], fmt, ctx.language,
                                              current=running)) + "</div>")
    out.append('<div class="grid2">' + "".join(cells) + "</div>")
    return "".join(out)
