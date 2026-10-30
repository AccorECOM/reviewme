"""What every dashboard section reads: the ledger in the window, and how to name things.

One `Context` per render. Sections never read files themselves: they get the rows
already filtered on the window and split by variant, so two pages cannot disagree on
what "the last 90 days" means.

- **Variant**: operational pages read `live` only. The `baseline` (control pipeline, always
  in dry-run) would double the counts and its cost is an experiment's, not the team's; only
  `tuning` compares the two.
- **People**: a GitHub login is a public identifier, but a dashboard shared outside the
  team may not want it. `name_of` is the ONLY way a section prints a person: with
  `names = "masked"`, `dev_1`, `dev_2`… numbered by first appearance in the window, stable
  within one render (not across renders: a mask that never changes is just a nickname).
- **Periods**: ISO weeks, or sprints counted from an anchor (`[stats] sprint_anchor`).
- **PR cycles** (`pr_cycles`): one row per PR of `prs.jsonl`, reviewed or not (control
  group), with the dates a cycle-time comparison needs. Computed once per render.
- **History** (`history`, `history_bugs`): the rows of `reviewme history`, NOT windowed —
  they exist to span years — with `bot_start`, the day the bot started: `[stats]
  bot_since` when set, else the first live run (outside dry-run) of the whole ledger —
  and `eras`, the named periods of `[[stats.eras]]`. Without them, one implicit era
  named "ReviewMe" from `bot_start`: the before/after pages are then the one-era case
  of the per-era ones, not a second code path. With them, `bot_start` is the LAST era's
  start (the "after" of the ROI page).
- **Triage** (`triage`, `triage_patterns`): the optional `reviewme triage` rows in the
  window, the latest per comment, and the latest pass's recurring-miss patterns. Empty
  when the command never ran: sections then fall back to the keyword labels.
- **Rule files** (`Context.rule_file`): the prompt file that names a rule, looked up in
  the CURRENT project folder. Rule ids come from the model's output, after the settings
  snapshot was taken: a lookup at render time needs no schema and follows renames.
"""
from __future__ import annotations

import json
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import NamedTuple

from ..enrich import ENRICH_FILE, PRS_FILE
from ..export import load
from ..ledger import FINDINGS_FILE, RUNS_FILE
from ..pr_history import latest_bug_months, latest_history
from ..quality import compute, latest
from ..triage import TRIAGE_FILE
from .settings import StatsSettings

UNKNOWN = "?"


class EraSpan(NamedTuple):
    """An era as the sections use it: its start as an aware datetime (a ledger start
    has a time of day, a `[[stats.eras]]` one is UTC midnight)."""
    name: str
    start: datetime
    label: str = ""
    bot_login: str = ""


def era_of(eras: list[EraSpan], created: datetime | None) -> EraSpan | None:
    """The era a PR belongs to: the latest one started on or before its creation.
    None = "before" (earlier than the first era, or no date)."""
    if created is None:
        return None
    current = None
    for era in eras:                       # sorted by start
        if era.start <= created:
            current = era
    return current


# --------------------------------------------------------------------------- periods

def sprint_number(d: date, anchor: date, anchor_number: int, length_days: int) -> int:
    """Sprint containing `d`. Before the anchor, numbers keep going down (0, -1…)."""
    return anchor_number + (d - anchor).days // length_days


def sprint_bounds(number: int, anchor: date, anchor_number: int,
                  length_days: int) -> tuple[date, date]:
    start = anchor + timedelta(days=(number - anchor_number) * length_days)
    return start, start + timedelta(days=length_days - 1)


def parse_ts(ts: str | None) -> datetime | None:
    try:
        dt = datetime.fromisoformat(ts or "")
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


# --------------------------------------------------------------------------- context

def _run_key(r: dict) -> str:
    """A run = one engine pass on a PR (all its reviewers share the `run_id`).

    Rows written before `run_id` existed fall back to (PR, commit, time): a replayed build
    then counts as a second run, which is what it was.
    """
    return r.get("run_id") or f"{r.get('pr')}:{r.get('head_sha')}:{r.get('ts')}"


def hours_between(start: str | None, end: str | None) -> float | None:
    """Hours from `start` to `end`, None when either is unknown or the order is wrong (a
    clock skew must not produce a negative delay that drags a median down)."""
    a, b = parse_ts(start), parse_ts(end)
    if a is None or b is None or b < a:
        return None
    return (b - a).total_seconds() / 3600


def median(values: list[float]) -> float | None:
    return round(statistics.median(values), 1) if values else None


def first_signal(e: dict | None) -> str | None:
    """Earliest human signal on a finding: `first_signal_at`, else (rows enriched before the
    field existed) the earliest reply or timed reaction."""
    e = e or {}
    if e.get("first_signal_at"):
        return e["first_signal_at"]
    stamps = [(parse_ts(x.get("ts")), x.get("ts"))
              for x in e.get("reactions", []) + e.get("replies", [])]
    stamps = [s for s in stamps if s[0] is not None]
    return min(stamps)[1] if stamps else None


def cost_of(rows) -> float:
    return sum(r.get("cost_usd", 0) or 0 for r in rows)


def _stats_authors(path: Path | None) -> dict[int, str]:
    """PR -> author from `stats.json`: the fallback when `enrich` never ran on a PR."""
    if path is None or not path.is_file():
        return {}
    try:
        runs = json.loads(path.read_text(encoding="utf-8")).get("runs", [])
    except (OSError, ValueError, AttributeError):
        return {}
    return {r["pr"]: r["auteur"] for r in runs if r.get("pr") is not None and r.get("auteur")}


@dataclass
class Context:
    settings: StatsSettings
    language: str
    findings: list[dict]                   # live, in window
    runs: list[dict]                       # live reviewer runs, in window
    baseline_findings: list[dict]
    baseline_runs: list[dict]
    enrich: dict                           # comment_id -> last enrichment line
    prs: dict                              # pr -> last `prs.jsonl` line
    quality: dict                          # `quality.compute` on the window
    stats_authors: dict = field(default_factory=dict)
    history: list = field(default_factory=list)          # `history.jsonl`, latest per PR
    history_bugs: dict = field(default_factory=dict)     # month -> bug tickets created
    bot_start: datetime | None = None
    bot_start_source: str = ""                  # "settings" | "ledger" | "eras" | ""
    eras: list = field(default_factory=list)             # EraSpan, sorted; [] = no start
    ever_reviewed: set = field(default_factory=set)      # (repo, pr) with a live run, ever
    triage: dict = field(default_factory=dict)           # comment_id -> latest triage row
    triage_patterns: dict | None = None                  # latest `kind = patterns` row
    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    _names: dict = field(default_factory=dict)
    _cache: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.settings.names == "masked":
            self._number_people()

    # ---- people

    def _number_people(self) -> None:
        """Number everyone in order of first appearance: authors through their runs, then
        reactions, replies and thread resolutions through the enrichment lines."""
        events: list[tuple[str, str]] = []
        for r in self.runs:
            events.append((r.get("ts", ""), self.author_login(r.get("pr"))))
        for e in self.enrich.values():
            for x in e.get("reactions", []):
                events.append((e.get("ts", ""), x.get("user")))
            for x in e.get("replies", []):
                events.append((x.get("ts") or e.get("ts", ""), x.get("user")))
            if e.get("resolved_by"):
                events.append((e.get("ts", ""), e["resolved_by"]))
        for p in self.prs.values():
            events.append((p.get("ts", ""), p.get("author")))
        for _, login in sorted(events, key=lambda ev: ev[0] or ""):
            self.name_of(login)

    def name_of(self, login: str | None) -> str:
        """How a person is printed. Never print a login any other way."""
        if not login or login == UNKNOWN:
            return UNKNOWN
        if self.settings.names != "masked":
            return login
        if login not in self._names:
            self._names[login] = f"dev_{len(self._names) + 1}"
        return self._names[login]

    def author_login(self, pr) -> str:
        """`runs.jsonl` has no author: `prs.jsonl` (enrich), then `stats.json`, else `?`."""
        p = self.prs.get(pr) or {}
        return p.get("author") or self.stats_authors.get(pr) or UNKNOWN

    def author_of(self, pr) -> str:
        return self.name_of(self.author_login(pr))

    # ---- runs

    def run_groups(self, runs: list[dict] | None = None) -> dict[str, list[dict]]:
        """Reviewer rows grouped by engine run, in time order."""
        out: dict[str, list[dict]] = defaultdict(list)
        for r in sorted(self.runs if runs is None else runs, key=lambda r: r.get("ts", "")):
            out[_run_key(r)].append(r)
        return dict(out)

    @staticmethod
    def run_key(r: dict) -> str:
        return _run_key(r)

    def silent_runs(self) -> dict[str, list[dict]]:
        """Runs that posted nothing new: every finding deduped, dropped, or none at all.

        The "re-review tax": a push that only re-finds what is already commented still
        pays the full review. In dry-run, `posted` means "would have posted": same rule.
        """
        posting = {_run_key(f) for f in self.findings if f.get("fate") == "posted"}
        return {k: rows for k, rows in self.run_groups().items() if k not in posting}

    # ---- periods

    def period_of(self, ts: str | None) -> str | None:
        dt = parse_ts(ts)
        if dt is None:
            return None
        if self.settings.period == "sprint":
            s = self.settings
            n = sprint_number(dt.date(), s.sprint_anchor, s.sprint_anchor_number,
                              s.sprint_length_days)
            return f"Sprint {n}"
        iso = dt.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"

    def current_period(self) -> str | None:
        """Label of the period `now` falls in: the one still running."""
        return self.period_of(self.now.isoformat())

    @staticmethod
    def period_order(label: str) -> tuple:
        """Sort key: `Sprint 10` after `Sprint 9`, `2026-W02` after `2025-W52`."""
        if label.startswith("Sprint "):
            try:
                return (int(label.split(" ", 1)[1]),)
            except ValueError:
                return (0,)
        return (label,)

    def periods(self) -> list[str]:
        """Every period with at least one live run, in order."""
        seen = {self.period_of(r.get("ts")) for r in self.runs}
        seen.discard(None)
        return sorted(seen, key=self.period_order)

    def config_markers(self, periods: list[str]) -> list[int]:
        """Indexes of the periods whose settings differ from the previous period's.

        A period's settings = the `config_hash` of its LAST run: a change mid-period shows on
        that period, and the next one when it was the very last run.
        """
        last: dict[str, str] = {}
        for r in sorted(self.runs, key=lambda r: r.get("ts", "")):
            p = self.period_of(r.get("ts"))
            if p and r.get("config_hash"):
                last[p] = r["config_hash"]
        out, previous = [], None
        for i, p in enumerate(periods):
            h = last.get(p)
            if h is None:
                continue
            if previous is not None and h != previous:
                out.append(i)
            previous = h
        return out

    # ---- settings files

    @property
    def project_dir(self) -> Path | None:
        d = self.settings.project_dir
        return Path(d) if d is not None and Path(d).is_dir() else None

    def rule_file(self, rule: str | None) -> str | None:
        """Relative path of the first prompt file naming `rule` as a whole word, or None.

        `rule` is a rule id or the `reviewer:rule_id` key of `quality.contested_rules`. The
        reviewer's own folder is searched first (its persona is where its rules live), then
        `common/`, `guidelines/`, then the other reviewers. A hyphen counts as part of the
        word: `null-check` does not match `null-check-strict`.
        """
        base = self.project_dir
        if base is None or not rule:
            return None
        reviewer, _, rule_id = rule.rpartition(":")
        if not rule_id or rule_id == "-":
            return None
        if "md_files" not in self._cache:
            texts: list[tuple[str, str]] = []
            for m in ("common/*.md", "guidelines/*.md", "reviewers/*/*.md"):
                for p in sorted(base.glob(m)):
                    try:
                        texts.append((p.relative_to(base).as_posix(),
                                      p.read_text(encoding="utf-8", errors="replace")))
                    except OSError:
                        continue
            self._cache["md_files"] = texts
        mine = f"reviewers/{reviewer}/"
        files = self._cache["md_files"]
        ordered = [f for f in files if reviewer and f[0].startswith(mine)] + \
                  [f for f in files if not (reviewer and f[0].startswith(mine))]
        word = re.compile(rf"(?<![\w-]){re.escape(rule_id)}(?![\w-])")
        return next((rel for rel, text in ordered if word.search(text)), None)

    # ---- findings

    @property
    def judged(self) -> list[dict]:
        """Posted inline findings with their verdict (`followed`, `approved`…)."""
        return self.quality["_judged"]

    def acted_before_merge(self, j: dict) -> bool:
        """A finding acted upon while the PR was still open.

        The line disappearing is observed at a PR head commit, and a PR head never moves
        after the merge: a `fingerprint_gone_at` on a merged PR is by construction before the
        merge. A "fixed" reply only counts if it was written before `merged_at`.
        """
        pr = self.prs.get(j.get("pr")) or {}
        merged = pr.get("merged_at")
        if not merged or not j.get("followed"):
            return False
        e = self.enrich.get(j.get("comment_id")) or {}
        if e.get("fingerprint_gone_at"):
            return True
        return any(r.get("label") == "fixed" and (r.get("ts") or "") <= merged
                   for r in e.get("replies", []))


# --------------------------------------------------------------------------- PR cycles

def pr_cycles(ctx: Context) -> list[dict]:
    """One row per PR of `prs.jsonl` in the window, reviewed or not, oldest PR first.

    What a cycle-time comparison (and the ROI page) needs, computed once per render:
    `reviewed` (False = control group), the four dates, the human effort around the PR
    and its size. Rows enriched before these fields existed simply carry None; a
    reviewed PR without `first_bot_run_at` falls back to its first live run in the window.
    """
    if "pr_cycles" in ctx._cache:
        return ctx._cache["pr_cycles"]
    first_run: dict = {}
    for r in sorted(ctx.runs, key=lambda r: r.get("ts", "")):
        first_run.setdefault(r.get("pr"), r.get("ts"))
    out = []
    for pr, p in sorted(ctx.prs.items(), key=lambda kv: (kv[0] is None, kv[0] or 0)):
        reviewed = p.get("reviewed", True)          # older lines: only reviewed PRs had one
        add, dele = p.get("additions"), p.get("deletions")
        first_bot = p.get("first_bot_run_at") or (first_run.get(pr) if reviewed else None)
        out.append({
            "pr": pr, "author": ctx.author_of(pr), "reviewed": reviewed,
            "state": p.get("state"),
            "created_at": p.get("created_at"),
            "first_human_review_at": p.get("first_human_review_at"),
            "first_bot_run_at": first_bot,
            "merged_at": p.get("merged_at"),
            "human_reviews": p.get("human_reviews"),
            "human_comments": p.get("human_comments"),
            "size": add + dele if add is not None and dele is not None else None,
            "hours_to_first_human_review": hours_between(p.get("created_at"),
                                                         p.get("first_human_review_at")),
            "hours_to_merge": hours_between(p.get("created_at"), p.get("merged_at")),
        })
    ctx._cache["pr_cycles"] = out
    return out


def coverage(ctx: Context) -> dict:
    """Share of the PRs merged in the window that the bot reviewed, overall and per author.

    Only readable when an `enrich` pass recorded the control group: without the unreviewed
    PRs, every merged PR in `prs.jsonl` is a reviewed one and the ratio would say 100 %.
    """
    if not any(p.get("control_group") for p in ctx.prs.values()):
        return {"available": False}
    start = ctx.now - ctx.settings.window_delta
    merged = [c for c in pr_cycles(ctx)
              if (dt := parse_ts(c["merged_at"])) is not None and start <= dt <= ctx.now]
    by_dev: dict[str, dict] = defaultdict(lambda: {"merged": 0, "reviewed": 0})
    for c in merged:
        d = by_dev[c["author"]]
        d["merged"] += 1
        d["reviewed"] += 1 if c["reviewed"] else 0
    reviewed = sum(1 for c in merged if c["reviewed"])
    return {"available": True, "merged": len(merged), "reviewed": reviewed,
            "rate": round(reviewed / len(merged), 4) if merged else None,
            "by_dev": {k: {**v, "rate": round(v["reviewed"] / v["merged"], 4)}
                       for k, v in sorted(by_dev.items())}}


def load_context(ledger_root: Path, settings: StatsSettings, language: str = "en", *,
                 stats_file: Path | None = None, now: datetime | None = None) -> Context:
    # The window is applied here rather than by `export.load`, against `now`: a render is
    # then reproducible (tests, a window anchored on a given day).
    now = now or datetime.now(UTC)
    limit = (now - settings.window_delta).isoformat(timespec="seconds")
    raw = load(ledger_root)
    data = {name: [r for r in rows if r.get("ts", "") >= limit] for name, rows in raw.items()}
    findings, runs = data[FINDINGS_FILE], data[RUNS_FILE]

    def live(r: dict) -> bool:
        return r.get("variant", "live") == "live"

    # Whole ledger, not the window: the bot started once, whatever the page's window.
    # A dry-run posted nothing, so it changed nobody's workflow.
    posting = [r for r in raw[RUNS_FILE] if live(r) and not r.get("dry_run")]
    if settings.bot_since is not None:
        start = datetime(settings.bot_since.year, settings.bot_since.month,
                         settings.bot_since.day, tzinfo=UTC)
        source = "settings"
    else:
        stamps = [dt for r in posting if (dt := parse_ts(r.get("ts"))) is not None]
        start, source = (min(stamps), "ledger") if stamps else (None, "")
    if settings.eras:
        eras = [EraSpan(e.name, datetime(e.since.year, e.since.month, e.since.day,
                                         tzinfo=UTC), e.label, e.bot_login)
                for e in settings.eras]
        start, source = eras[-1].start, "eras"
    else:
        eras = [EraSpan("ReviewMe", start)] if start is not None else []

    return Context(
        settings=settings, language=language,
        findings=[f for f in findings if live(f)],
        runs=[r for r in runs if live(r)],
        baseline_findings=[f for f in findings if not live(f)],
        baseline_runs=[r for r in runs if not live(r)],
        enrich=latest(data[ENRICH_FILE], "comment_id"),
        prs=latest(data[PRS_FILE], "pr"),
        quality=compute(findings, runs, data[ENRICH_FILE], data[PRS_FILE]),
        stats_authors=_stats_authors(stats_file),
        history=latest_history(Path(ledger_root)),
        history_bugs=latest_bug_months(Path(ledger_root)),
        bot_start=start, bot_start_source=source, eras=eras,
        ever_reviewed={(r.get("repo") or "", r.get("pr")) for r in posting},
        triage=latest([r for r in data[TRIAGE_FILE] if r.get("kind", "finding") == "finding"],
                      "comment_id"),
        triage_patterns=next((r for r in reversed(data[TRIAGE_FILE])
                              if r.get("kind") == "patterns"), None),
        now=now,
    )
