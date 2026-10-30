"""The team's PR workflow over years: `reviewme history --since 2024-01-01`.

`prs.jsonl` only covers the enrichment window (two weeks, three months): enough to compare
reviewed and unreviewed PRs side by side, not to say whether the team's PRs merge faster
or get fewer hotfixes since the bot arrived. This one-off, re-runnable scan reads every
merged or closed PR of the repository since a date and writes one `history.jsonl` row per
PR, with the dates, the size and the human effort around it.

- **One GraphQL query per 100 PRs**, newest created first, stopping at the first PR older
  than `--since`: the cost follows the period asked for, not the repository's age. A
  2-year scan of 4,000 PRs is about 40 queries and 80 points of the 5,000-point hourly
  GraphQL budget. Progress is logged at every page; when the remaining budget gets low,
  the scan waits for the reset if it is near, else stops and keeps what it read. A page
  that times out (GitHub's 502/504 or the client's own timeout) is retried with half
  as many PRs, down to 10.
- **Idempotent**: each pass writes one part (`history/<timestamp>-<id>.jsonl`); readers
  keep the most recent row per (`repo`, `pr`). Re-running after a month adds the new PRs
  and refreshes the others.
- **Human reviews** exclude the PR author (a reply typed in a review thread is itself a
  `COMMENTED` review), other bots (`[bot]` logins, GitHub App authors), `BOT_LOGIN`, and
  reviews whose body carries the bot's marker or signature (with a PAT, the bot reviews
  under a person's name). Bodies are read for that test only, never written.
- **Bot threads**: `reviewThreads.totalCount` counts the bot's threads too. The ledger
  knows how many the bot opened on each PR (its posted inline findings), so the row also
  carries `bot_threads` and `human_threads`, the difference.
- **Per-login counts** (`review_authors`, `thread_authors`, `review_first_at`): review
  submissions, review threads (by the author of their first comment) and the earliest
  review date per login. A bot of an earlier
  era may have posted under a person's account, with no marker and no ledger: only
  these counts let the dashboard take its share out of the "human" totals later
  (`[[stats.eras]] bot_login`), without a rescan each time the config learns a login.
- **Jira** (`--jira`, optional): the ticket type behind each PR and the bug tickets
  created per month (`history-bugs.jsonl`); see `context/jira_stats.py`.
- **Hotfix, fix and revert** are guessed from the head branch name: titles are not
  stored (privacy), and GitHub's own revert button names the branch `revert-<n>-…`. A
  hotfix (`hotfix` anywhere in the name) and an ordinary fix (`fix/…`, `bugfix/…`,
  `fix-…`) are two different flags: on a repository where `fix/` is the normal prefix
  for any bug fix, counting both as hotfixes made a third of the PRs "hotfixes" and hid
  the signal the share is for (emergencies). Still a weak proxy, documented as such.

Privacy: no title, no body, no comment text. Logins (author, reviewers through their
counts only) as in `prs.jsonl`. Read-only on GitHub; never on the review's critical path.
"""
from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .ledger import FINDINGS_FILE, RUNS_FILE, SCHEMA_VERSION, read_all, write_part
from .reconciler import marker_key
from .strings import ALL_SIGNATURES

HISTORY_FILE = "history.jsonl"
HISTORY_BUGS_FILE = "history-bugs.jsonl"

_REVERT_RE = re.compile(r"revert", re.IGNORECASE)
_HOTFIX_RE = re.compile(r"hotfix", re.IGNORECASE)
#: `fix/` anywhere (`bugfix/`, `team/fix/`), or a name that starts with the word `fix`
#: (`fix-123`, `fix_login`). The negative lookahead keeps `fixture/…` and `fixtures-…`
#: out: those are test data branches, not fixes.
_FIX_RE = re.compile(r"fix/|^fix(?![a-z])", re.IGNORECASE)


def is_hotfix_branch(head: str | None) -> bool:
    """`hotfix` anywhere in the branch name, any case: an emergency fix."""
    return bool(_HOTFIX_RE.search(head or ""))


def is_fix_branch(head: str | None) -> bool:
    """An ordinary fix branch (`fix/…`, `bugfix/…`, `fix-…`), never a hotfix one."""
    return bool(_FIX_RE.search(head or "")) and not is_hotfix_branch(head)


def is_revert_branch(head: str | None) -> bool:
    return bool(_REVERT_RE.search(head or ""))

#: Below this many GraphQL points left, wait for the reset (or stop): a scan must never
#: leave the review itself without budget.
MIN_REMAINING = 200
#: Longest wait for a reset before giving up; the rest can be read on the next run.
MAX_WAIT_S = 15 * 60
#: GitHub answers a heavy GraphQL page with a 502 when it times out: halve the page.
_TIMEOUT_STATUSES = (502, 504)
#: Smallest page tried before giving up on a cursor: below that, the cost per PR is no
#: longer what times out (a single huge PR, GitHub down), and halving again only delays
#: the error.
MIN_PAGE = 10


class HistoryScanError(RuntimeError):
    """The scan could not read its first page: nothing to keep, the CLI prints it."""


def _timed_out(e: Exception) -> bool:
    """A page GitHub could not serve in time, on either side of the wire.

    The server-side timeout comes back as a 502/504 with a status; the client-side one
    (`httpx.ReadTimeout`, and every `httpx.TimeoutException`) has no status at all, yet
    it means the same thing on a heavy page (100 PRs with their reviews): smaller pages
    are the cure in both cases. Seen on a 3,600-PR repository, where page 34 died on a
    `ReadTimeout` instead of being retried with 50.
    """
    return isinstance(e, httpx.TimeoutException) \
        or getattr(e, "status_code", None) in _TIMEOUT_STATUSES


def parse_date(value: str, now: datetime | None = None) -> datetime:
    """`2024-01-01` (UTC midnight) or a window like `730d`, counted back from `now`."""
    from .enrich import parse_since
    now = now or datetime.now(UTC)
    try:
        d = datetime.fromisoformat(value.strip())
    except ValueError:
        try:
            return now - parse_since(value)
        except ValueError:
            raise ValueError(f"invalid --since: {value!r} (expected YYYY-MM-DD or 730d)") \
                from None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _parse(ts: str | None) -> datetime | None:
    try:
        dt = datetime.fromisoformat(ts or "")
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _is_machine(actor: dict | None) -> bool:
    actor = actor or {}
    return actor.get("__typename") == "Bot" or str(actor.get("login", "")).endswith("[bot]")


def _is_bot_body(body: str | None) -> bool:
    body = body or ""
    return marker_key(body) is not None or any(sig in body for sig in ALL_SIGNATURES)


def _ledger_view(root: Path, repo: str) -> tuple[set[int], dict[int, int]]:
    """(PRs the bot reviewed, bot threads per PR) from the whole ledger, for `repo`.

    Reviewed = a live run outside dry-run: a dry-run posted nothing, so it cannot have
    changed the team's workflow. Rows without `repo` (older ledgers) count for any repo.
    """
    def mine(r: dict) -> bool:
        return (r.get("variant", "live") == "live" and not r.get("dry_run")
                and r.get("repo") in (None, "", repo))

    reviewed = {r.get("pr") for r in read_all(root, RUNS_FILE) if mine(r)}
    threads: dict[int, set] = {}
    for f in read_all(root, FINDINGS_FILE):
        if mine(f) and f.get("fate") == "posted" and not f.get("in_summary") \
                and f.get("comment_id") is not None:
            threads.setdefault(f.get("pr"), set()).add(f["comment_id"])
    return reviewed, {pr: len(c) for pr, c in threads.items()}


def history_row(node: dict, *, repo: str, now: str, bot_login: str = "",
                review_label: str = "", reviewed: set | None = None,
                bot_threads: dict | None = None) -> dict:
    """`history.jsonl` row of one GraphQL PR node."""
    author_obj = node.get("author") or {}
    author = author_obj.get("login") or "?"          # null author = deleted account
    reviews = [r for r in ((node.get("reviews") or {}).get("nodes") or [])
               if r.get("submittedAt") and r.get("state") != "PENDING"
               and r.get("author") and not _is_machine(r.get("author"))
               and (r["author"].get("login") or "") not in ("", author, bot_login)
               and not _is_bot_body(r.get("body"))]
    stamps = sorted((dt, r["submittedAt"]) for r in reviews
                    if (dt := _parse(r["submittedAt"])) is not None)
    labels = [x.get("name") for x in ((node.get("labels") or {}).get("nodes") or [])
              if x.get("name")]
    number = node.get("number")
    head = node.get("headRefName") or ""
    threads = (node.get("reviewThreads") or {}).get("totalCount")
    # Every submitted review except the author's own replies and `[bot]` accounts: the
    # marker / signature / BOT_LOGIN filters above are deliberately NOT applied, since
    # the point is to count a bot that looked like a person.
    # `review_first_at` too: with only counts, a prototype bot reviewing within minutes
    # would still be the PR's "first human review" once its counts are taken out.
    review_authors: dict[str, int] = {}
    review_first: dict[str, tuple[datetime, str]] = {}
    for r in ((node.get("reviews") or {}).get("nodes") or []):
        login = (r.get("author") or {}).get("login") or ""
        if r.get("submittedAt") and r.get("state") != "PENDING" and login \
                and login != author and not _is_machine(r.get("author")):
            review_authors[login] = review_authors.get(login, 0) + 1
            dt = _parse(r["submittedAt"])
            if dt is not None and (login not in review_first or dt < review_first[login][0]):
                review_first[login] = (dt, r["submittedAt"])
    thread_authors: dict[str, int] = {}
    for t in ((node.get("reviewThreads") or {}).get("nodes") or []):
        first = ((t.get("comments") or {}).get("nodes") or [{}])[0] or {}
        login = (first.get("author") or {}).get("login") or "?"   # deleted account
        thread_authors[login] = thread_authors.get(login, 0) + 1
    bot_t = (bot_threads or {}).get(number, 0)
    return {
        "schema_version": SCHEMA_VERSION, "ts": now, "repo": repo, "pr": number,
        "author": author, "author_is_bot": _is_machine(author_obj),
        "created_at": node.get("createdAt"), "merged_at": node.get("mergedAt"),
        "closed_at": node.get("closedAt"),
        "state": "merged" if node.get("mergedAt") else "closed",
        "is_draft": bool(node.get("isDraft")),
        "base_ref": node.get("baseRefName"), "head_ref": head,
        "additions": node.get("additions"), "deletions": node.get("deletions"),
        "changed_files": node.get("changedFiles"),
        "commits": (node.get("commits") or {}).get("totalCount"),
        "human_reviews": len(reviews),
        "first_human_review_at": stamps[0][1] if stamps else None,
        "review_threads": threads,
        "bot_threads": bot_t,
        "human_threads": max(0, threads - bot_t) if threads is not None else None,
        "review_authors": dict(sorted(review_authors.items())),
        "thread_authors": dict(sorted(thread_authors.items())),
        "review_first_at": {k: v[1] for k, v in sorted(review_first.items())},
        "issue_comments": (node.get("comments") or {}).get("totalCount"),
        "labels": labels,
        "is_revert": is_revert_branch(head),
        "is_hotfix_branch": is_hotfix_branch(head),
        "is_fix_branch": is_fix_branch(head),
        "bot_reviewed": number in (reviewed or set())
        or bool(review_label and review_label in labels),
    }


def scan(gh, root: Path, since: datetime, logger: logging.Logger, *, repo: str,
         bot_login: str = "", review_label: str = "", jira=None,
         sleep: Callable[[float], None] = time.sleep,
         clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> dict:
    """Read the merged and closed PRs created since `since`, write `history.jsonl` (and,
    with a `jira` client, link each PR to its ticket and write `history-bugs.jsonl`).
    Returns counters; `aborted` / `message` say why it stopped early, if it did."""
    now = clock().isoformat(timespec="seconds")
    reviewed, bot_threads = _ledger_view(root, repo)
    try:
        left = gh.check_rate_limit().get("graphql", {}).get("remaining")
        logger.info("history: %s GraphQL points left before the scan", left)
    except Exception as e:                       # informative only
        logger.info("history: rate limit unreadable (%s)", type(e).__name__)

    rows: list[dict] = []
    cursor, first, pages, aborted = None, 100, 0, ""
    while True:
        try:
            page = gh.closed_prs_page(cursor, first)
        except Exception as e:
            timed_out = _timed_out(e)
            if timed_out and first > MIN_PAGE:
                # Same cursor, smaller page: nothing read is lost, and the smaller size
                # is kept for the rest of the scan (heavy PRs tend to come in runs).
                first = max(MIN_PAGE, first // 2)
                logger.info("history: GitHub timed out (%s), retrying page %d with "
                            "pages of %d", type(e).__name__, pages + 1, first)
                continue
            reason = (f"GitHub timed out even with pages of {first} PRs "
                      f"({type(e).__name__}: {e or 'no detail'}); run again later, "
                      f"the PRs already scanned are kept" if timed_out
                      else f"{type(e).__name__}: {e}")
            if not rows:
                if timed_out:
                    raise HistoryScanError(reason) from e
                raise
            aborted = reason
            logger.warning("history: page %d unreadable (%s), keeping %d PR(s) read",
                           pages + 1, aborted, len(rows))
            break
        pages += 1
        nodes = page["nodes"]
        fresh = [n for n in nodes
                 if (dt := _parse(n.get("createdAt"))) is not None and dt >= since]
        rows.extend(history_row(n, repo=repo, now=now, bot_login=bot_login,
                                review_label=review_label, reviewed=reviewed,
                                bot_threads=bot_threads) for n in fresh)
        rl = page.get("rate_limit") or {}
        oldest = nodes[-1].get("createdAt") if nodes else "-"
        logger.info("history: page %d, %d PR(s) so far, oldest created %s "
                    "(cost %s, %s points left)", pages, len(rows), oldest,
                    rl.get("cost", "?"), rl.get("remaining", "?"))
        if len(fresh) < len(nodes) or not page["has_next"]:
            break
        cursor = page["cursor"]
        remaining = rl.get("remaining")
        if isinstance(remaining, int) and remaining < MIN_REMAINING:
            reset = _parse(rl.get("resetAt"))
            wait = (reset - clock()).total_seconds() + 5 if reset else MAX_WAIT_S + 1
            if wait > MAX_WAIT_S:
                aborted = (f"GraphQL budget low ({remaining} points left, reset at "
                           f"{rl.get('resetAt')}): run again after the reset")
                logger.warning("history: %s", aborted)
                break
            logger.info("history: %d points left, waiting %ds for the reset", remaining,
                        int(wait))
            sleep(max(0.0, wait))

    out = {"prs": len(rows), "pages": pages, "aborted": aborted,
           "merged": sum(1 for r in rows if r["state"] == "merged"),
           "bot_reviewed": sum(1 for r in rows if r["bot_reviewed"])}
    if jira is not None:
        from .context.jira_stats import bug_months, link_tickets
        out["jira"] = link_tickets(rows, jira, logger)
        bugs = [{"schema_version": SCHEMA_VERSION, "ts": now, **m}
                for m in bug_months(jira, rows, since, clock(), logger)]
        write_part(root, HISTORY_BUGS_FILE, bugs)
        out["bug_months"] = len(bugs)
        out["jira_calls"] = getattr(jira, "calls", None)
    write_part(root, HISTORY_FILE, rows)
    logger.info("history: %d PR(s) written over %d page(s) (%s)", len(rows), pages, root)
    if aborted:
        out["message"] = aborted
    return out


def latest_history(root: Path) -> list[dict]:
    """The most recent row per (`repo`, `pr`), oldest PR first.

    "Most recent" by the row's `ts`, then by part order: two passes in the same second
    get part names that sort at random.
    """
    last: dict[tuple, dict] = {}
    for r in sorted(read_all(root, HISTORY_FILE), key=lambda r: r.get("ts") or ""):
        last[(r.get("repo"), r.get("pr"))] = r
    return sorted(last.values(), key=lambda r: r.get("created_at") or "")


def latest_bug_months(root: Path) -> dict[str, int]:
    """`{month: bugs}`: the most recent count per (projects, month), summed over the
    project sets (a scan with another `JIRA_KEY_PREFIX` adds its own rows)."""
    last: dict[tuple, dict] = {}
    for r in sorted(read_all(root, HISTORY_BUGS_FILE), key=lambda r: r.get("ts") or ""):
        last[(r.get("projects"), r.get("month"))] = r
    out: dict[str, int] = {}
    for (_, month), r in sorted(last.items(), key=lambda kv: str(kv[0][1])):
        if month:
            out[month] = out.get(month, 0) + int(r.get("bugs") or 0)
    return out


def months_between(start: datetime, end: datetime) -> list[str]:
    """`YYYY-MM` of every month from `start` to `end`, both included."""
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out
