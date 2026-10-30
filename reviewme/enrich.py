"""Fate of posted findings: `reviewme enrich --since 14d`.

The ledger (`ledger.py`) says what the bot said. It does not say whether it was useful.
This module rereads the PRs in the window and records, for each bot comment:

- **effect tracking**: has the commented line disappeared from the file at the PR head?
  We compare fingerprints (`fingerprint_hash`), never text: it works without the ledger
  having kept the line's content;
- **reactions** (every GitHub content: 👍 👎 😄 😕 ❤️ 🎉 🚀 👀), per author, distinguishing
  the PR author from others. Which ones mean "right" or "wrong" is the dashboard's call
  (`[stats.judgment]`), not the collector's: a 🚀 dropped here could never be counted later;
- human **replies**, labeled by keywords (fixed, false positive, intended…);
- the **first human signal** (`first_signal_at`): earliest reaction or reply, so the
  dashboard can say how long a finding waits before anyone reacts;
- **thread resolved** or not, and by whom;
- **nearby human comments** (≤ 10 lines, same file): a human commenting next to the bot
  says something about what the bot saw, or missed.

And, per PR (`prs.jsonl`): state, commits after the first review, human comments —
including those on lines where the bot said nothing (approximate misses) and those on
lines where it had a DROPPED finding (what the filter costs in recall) — and the PR's
**cycle**: opened, first human review, first bot run, merged, size.

**Control group** (`[stats] control_group`, on by default): the repository's PRs updated
in the window that the bot NEVER reviewed get a `prs.jsonl` line too (`reviewed = false`),
with the same cycle fields. Without them, "reviewed PRs merge in 2 days" has nothing to be
compared to, and coverage (reviewed / merged) cannot be computed. Two calls per such PR
(reviews, review comments): their size is left out, it would cost a third.

DETERMINISTIC collection, no LLM. Idempotent: each pass adds one line per comment; for
the same `comment_id`, the most recent one wins.

Read-only on the GitHub side. Texts (replies, nearby comments) are only written if
`ledger.include_text` is enabled, as in the ledger.
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .diff_utils import normalize_line
from .ledger import FINDINGS_FILE, RUNS_FILE, SCHEMA_VERSION, read_all, write_part
from .reconciler import _MARKER_RE, fingerprint_hash, marker_key
from .strings import ALL_SIGNATURES

ENRICH_FILE = "enrich.jsonl"
PRS_FILE = "prs.jsonl"
NEARBY_LINES = 10
MAX_TEXT = 1000


def control_group(project) -> bool:
    """List the PRs the bot never reviewed? `REVIEWME_CONTROL_GROUP` takes precedence over
    `[stats] control_group` in `project.toml` (default yes), like `include_text`."""
    import os
    env = os.environ.get("REVIEWME_CONTROL_GROUP")
    if env is not None:
        return env.strip().lower() in {"1", "true", "yes", "on"}
    return bool(getattr(project, "stats_control_group", True))

#: Order = priority: a reply "no it's intended, fixed anyway" is `intended`.
#: Contest patterns themselves contain a negation ("n'est pas le cas", "not the case"):
#: they are tested as-is. The others only count when there is no negation.
#: Patterns recognize French AND English replies.
_CONTEST = (
    ("false_positive", r"faux positif|false positive|n'est pas le cas|ne s'applique pas|"
                       r"doesn'?t apply|not true|pas vrai|\bhallucin|c'est faux|"
                       r"not the case|does not apply|not applicable"),
    ("intended", r"volontaire|\bvoulu\b|intended|by design|expr[eè]s|on garde|"
                 r"on le laisse|on purpose|\bintentional(ly)?\b|"
                 r"\bdeliberate(ly)?\b|we keep (it|this)"),
)
_PLAIN = (
    ("false_positive", r"\bincorrect\b|\bwrong\b"),
    ("fixed", r"corrig[ée]|\bfix(ed)?\b|c'est fait|\bdone\b|r[ée]solu|bien vu|good catch|"
              r"nice catch|\bresolved\b|\baddressed\b"),
    ("unimportant", r"pas important|\bnit\b|osef|pas prioritaire|plus tard|\blater\b|"
                    r"hors scope|hors sujet|out of scope|pas grave|not important|\bnitpick|"
                    r"low priority|not a priority|no big deal|not a big deal"),
)
_NEGATION = re.compile(r"\b(pas|plus|jamais|not|never|no|non)\b|n'|n’")
_REMEMBER = re.compile(r"^\s*@reviewme\s+remember\s+(.+)", re.IGNORECASE | re.DOTALL)


def label_reply(text: str) -> str:
    """Label of a human reply, by keywords. To be reviewed: this is a rough sort.

    When in doubt, `other`: a wrong label skews the measure, a missing label only leaves
    it incomplete. "pas corrigé", "pas faux", "not fixed" therefore fall into `other`.
    """
    t = (text or "").lower()
    for label, motif in _CONTEST:
        if re.search(motif, t):
            return label
    negated = bool(_NEGATION.search(t))
    for label, motif in _PLAIN:
        if re.search(motif, t):
            # "pas important" / "not important" carry their negation: that is the meaning.
            if negated and label != "unimportant":
                return "other"
            return label
    return "question" if t.strip().endswith("?") else "other"


def parse_since(value: str) -> timedelta:
    """`14d`, `36h` -> timedelta. A bare number counts as days."""
    m = re.fullmatch(r"\s*(\d+)\s*([dh]?)\s*", value or "")
    if not m:
        raise ValueError(f"invalid window: {value!r} (expected: 14d, 36h)")
    n = int(m.group(1))
    return timedelta(hours=n) if m.group(2) == "h" else timedelta(days=n)


def _is_bot(comment: dict) -> bool:
    """A bot comment is recognized by its marker or signature, never by its author: with
    a PAT, the bot comments under a human's identity."""
    body = comment.get("body", "") or ""
    return marker_key(body) is not None or any(sig in body for sig in ALL_SIGNATURES)


def _is_machine(comment: dict) -> bool:
    """Comment from ANOTHER tool (Copilot, Sonar, Dependabot…): neither ours nor a human.
    It must not count as a miss."""
    user = comment.get("user") or {}
    return user.get("type") == "Bot" or str(user.get("login", "")).endswith("[bot]")


def _clean(text: str) -> str:
    return _MARKER_RE.sub("", text or "").strip()[:MAX_TEXT]


def _parse(ts: str | None) -> datetime | None:
    try:
        dt = datetime.fromisoformat(ts or "")
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def earliest(*stamps: str | None) -> str | None:
    """Earliest of ISO timestamps, compared as dates and returned as written.

    GitHub writes `…Z`, the ledger `…+00:00`: a string `min` would mostly work and then
    silently fail on the first mixed pair.
    """
    known = [(dt, s) for s in stamps if (dt := _parse(s)) is not None]
    return min(known)[1] if known else None


#: The file no longer exists at this commit (deleted or renamed): distinct from "line missing".
FILE_GONE = "file_gone"


def _fingerprints_of(content: str, path: str) -> set[str]:
    return {fingerprint_hash(path, line) for line in content.splitlines()
            if normalize_line(line)}


class _Files:
    """Cache of a file's fingerprints at a commit (one API call per pair)."""

    def __init__(self, gh):
        self.gh, self.cache = gh, {}

    def fingerprints(self, path: str, ref: str) -> set[str] | str | None:
        """Line fingerprints, `FILE_GONE` if the file no longer exists, None if unknown."""
        key = (path, ref)
        if key not in self.cache:
            try:
                content = self.gh.get_file_at(path, ref)
                self.cache[key] = FILE_GONE if content is None else _fingerprints_of(content, path)
            except Exception:
                self.cache[key] = None     # unknown: conclude nothing
        return self.cache[key]


class _Permissions:
    """Write permission per login, one request per person and per pass."""

    def __init__(self, gh):
        self.gh, self.cache = gh, {}

    def can_write(self, login: str) -> bool:
        if login not in self.cache:
            try:
                self.cache[login] = self.gh.get_permission(login) in ("admin", "maintain",
                                                                      "write")
            except Exception:
                self.cache[login] = False
        return self.cache[login]


def _login(obj: dict) -> str:
    return (obj.get("user") or {}).get("login", "") or ""


def _cycle(gh, pr: dict, comments: list[dict], *, bot_login: str,
           logger: logging.Logger) -> dict:
    """Cycle fields shared by reviewed and unreviewed PRs (`prs.jsonl`).

    `first_human_review_at` / `human_reviews`: submitted reviews by a person other than the
    PR author. The bot is recognized by its login when dedicated, and otherwise by its
    review carrying a bot comment (with a PAT it reviews under a human's name). The author
    is left out because a reply typed in a review thread is itself a `COMMENTED` review:
    the author answering the bot is not a review.
    """
    number, author = pr.get("number"), _login(pr)
    bot_reviews = {c.get("pull_request_review_id") for c in comments if _is_bot(c)}
    try:
        reviews = [r for r in gh.list_pr_reviews(number)
                   if r.get("submitted_at") and r.get("state") != "PENDING"
                   and not _is_machine(r) and _login(r) not in ("", author, bot_login)
                   and r.get("id") not in bot_reviews]
        first = earliest(*(r.get("submitted_at") for r in reviews))
        count: int | None = len(reviews)
    except Exception as e:
        logger.info("PR #%s: reviews unreadable (%s)", number, type(e).__name__)
        first, count = None, None
    return {"created_at": pr.get("created_at"), "first_human_review_at": first,
            "human_reviews": count}


def _human_split(comments: list[dict]) -> tuple[list[dict], list[dict]]:
    """(human comments, of which thread roots): neither the bot nor another tool."""
    humans = [c for c in comments if not _is_bot(c) and not _is_machine(c)]
    return humans, [c for c in humans if c.get("in_reply_to_id") is None]


def _last_by_comment(rows: list[dict]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for r in rows:
        if r.get("comment_id") is not None:
            out[r["comment_id"]] = r
    return out


def enrich_pr(gh, pr_number: int, rows: list[dict], previous: dict[int, dict], *,
              with_text: bool, logger: logging.Logger, now: str | None = None,
              files: _Files | None = None, perms: _Permissions | None = None,
              first_head: str | None = None, bot_login: str = "",
              first_run_at: str | None = None,
              control_group: bool = False) -> tuple[list[dict], dict]:
    """Enrich ONE PR. `rows` = its ledger rows (live variant), `previous` = the last known
    enrichment line per comment_id. Returns (lines, PR line).

    `first_head` = commit of the FIRST known run on the PR, even outside the window.
    `first_run_at` = time of that run (`first_bot_run_at`); defaults to the oldest of `rows`.
    `bot_login` = the bot's identity when dedicated: its own reactions do not count.
    `control_group` = whether this pass also recorded the unreviewed PRs: it tells the
    dashboard whether a coverage ratio can be read from `prs.jsonl`.
    """
    now = now or datetime.now(UTC).isoformat(timespec="seconds")
    files = files or _Files(gh)
    perms = perms or _Permissions(gh)
    pr = gh.get_pr(pr_number)
    head = pr["head"]["sha"]
    author = (pr.get("user") or {}).get("login", "")
    comments = gh.list_review_comments(pr_number)

    try:
        threads = {((t.get("comments") or {}).get("nodes") or [{}])[0].get("databaseId"): t
                   for t in gh.review_threads(pr_number)}
    except Exception as e:
        logger.info("PR #%s: review threads unreadable via GraphQL (%s)", pr_number,
                    type(e).__name__)
        threads = {}

    humans, human_roots = _human_split(comments)
    replies_by_parent: dict[int, list[dict]] = {}
    for c in humans:
        if c.get("in_reply_to_id") is not None:
            replies_by_parent.setdefault(c["in_reply_to_id"], []).append(c)

    def role(login: str) -> str:
        return "pr_author" if login and login == author else "other"

    out: list[dict] = []
    for cid, row in _last_by_comment(rows).items():
        if row.get("in_summary"):
            continue                       # global comment: no line to track
        prev = previous.get(cid, {})
        line = {"schema_version": SCHEMA_VERSION, "ts": now, "comment_id": cid,
                "pr": pr_number, "reviewer": row.get("reviewer"),
                "fingerprint": row.get("fingerprint"), "rule_id": row.get("rule_id"),
                "head_sha": head}

        # Effect tracking: the fingerprint no longer exists anywhere in the file at the head,
        # AND the file still exists. A deleted or renamed file proves nothing.
        gone = prev.get("fingerprint_gone_at")
        file_gone = prev.get("file_gone_at")
        if not gone and row.get("fingerprint"):
            present = files.fingerprints(row["path"], head)
            if present == FILE_GONE:
                file_gone = file_gone or head
            elif present is not None and row["fingerprint"] not in present:
                gone = head
        line["fingerprint_gone_at"] = gone
        line["file_gone_at"] = file_gone

        # Every content is kept: the vocabulary (`[stats.judgment]`) decides what a 🚀
        # means, and can change after the fact; a reaction not collected is lost for good.
        # Only machines (other bots, and the bot itself when it has its own identity) are
        # left out: their reactions say nothing about the finding.
        try:
            line["reactions"] = [
                {"content": r.get("content"), "user": (r.get("user") or {}).get("login", ""),
                 "role": role((r.get("user") or {}).get("login", "")),
                 "ts": r.get("created_at")}
                for r in gh.list_comment_reactions(cid)
                if r.get("content") and not _is_machine(r)
                and (r.get("user") or {}).get("login") != bot_login]
        except Exception:
            line["reactions"] = prev.get("reactions", [])     # keep the last reading

        replies = []
        for c in sorted(replies_by_parent.get(cid, []), key=lambda c: c.get("created_at", "")):
            login = (c.get("user") or {}).get("login", "")
            body = c.get("body", "") or ""
            rep = {"user": login, "role": role(login), "ts": c.get("created_at"),
                   "label": label_reply(body)}
            rep["contests"] = rep["label"] in ("false_positive", "intended")
            remember = _REMEMBER.match(body)
            if remember:
                # Rule given explicitly by a human, only kept from an author with write
                # access. Its text follows the common rule (`include_text`).
                rep["remember"] = {"trusted": perms.can_write(login)}
                if with_text:
                    rep["remember"]["rule"] = remember.group(1).strip()[:MAX_TEXT]
            if with_text:
                rep["text"] = _clean(body)
            replies.append(rep)
        line["replies"] = replies
        # The previous reading counts too: a reaction removed since was still a signal,
        # and its time is the one that says how fast people answer.
        line["first_signal_at"] = earliest(
            prev.get("first_signal_at"), *(r.get("ts") for r in line["reactions"]),
            *(r.get("ts") for r in replies))

        thread = threads.get(cid)
        line["thread_resolved"] = bool(thread and thread.get("isResolved"))
        line["resolved_by"] = ((thread or {}).get("resolvedBy") or {}).get("login")

        nearby = []
        for c in human_roots:
            ln = c.get("line") or c.get("original_line")
            if c.get("path") == row.get("path") and ln and abs(ln - row.get("line", 0)) <= NEARBY_LINES:
                item = {"comment_id": c.get("id"), "user": (c.get("user") or {}).get("login"),
                        "line": ln}
                if with_text:
                    item["text"] = _clean(c.get("body", ""))
                nearby.append(item)
        line["human_comments_nearby"] = nearby
        out.append(line)

    # --- Per PR: approximate misses and the filter's cost in recall ---
    # Line numbers only make sense at a given commit. A still-current comment (`line`) is
    # compared to the LAST run; an outdated comment is compared, via `original_line`, to
    # the run made on the commit where it was written.
    last_run = max(rows, key=lambda r: r.get("ts", ""))["run_id"] if rows else None
    rows_by_head: dict[str, list[dict]] = {}
    for r in rows:
        rows_by_head.setdefault(r.get("head_sha"), []).append(r)

    def near(c: dict, fates: tuple[str, ...]) -> bool:
        if c.get("line"):
            ln, candidates = c["line"], [r for r in rows if r.get("run_id") == last_run]
        else:
            ln = c.get("original_line") or 0
            candidates = rows_by_head.get(c.get("original_commit_id"), [])
        return any(r.get("path") == c.get("path") and r.get("fate") in fates
                   and abs((r.get("line") or 0) - ln) <= NEARBY_LINES for r in candidates)

    if first_head is None:
        first_head = min(rows, key=lambda r: r.get("ts", ""))["head_sha"] if rows else head
    try:
        shas = [c.get("sha") for c in gh.list_pr_commits(pr_number)]
        after = len(shas) - shas.index(first_head) - 1 if first_head in shas else None
    except Exception:
        after = None
    spoken = ("posted", "deduped", "out_of_diff", "capped")
    dropped = ("dropped_low", "dropped_unverified")
    pr_line = {
        "schema_version": SCHEMA_VERSION, "ts": now, "pr": pr_number, "reviewed": True,
        "control_group": control_group,
        "state": "merged" if pr.get("merged_at") else pr.get("state"),
        "merged_at": pr.get("merged_at"), "author": author, "head_sha": head,
        **_cycle(gh, pr, comments, bot_login=bot_login, logger=logger),
        "first_bot_run_at": first_run_at or (
            earliest(*(r.get("ts") for r in rows)) if rows else None),
        "additions": pr.get("additions"), "deletions": pr.get("deletions"),
        "changed_files": pr.get("changed_files"),
        "commits_after_first_review": after,
        "human_comments": len(human_roots),
        "human_replies": len(humans) - len(human_roots),
        "human_comments_on_silent_lines": sum(
            1 for c in human_roots if not near(c, spoken + dropped)),
        "human_comments_on_dropped_findings": sum(
            1 for c in human_roots if near(c, dropped) and not near(c, spoken)),
    }
    if with_text:
        pr_line["human_comments_text"] = [
            {"comment_id": c.get("id"), "user": (c.get("user") or {}).get("login"),
             "path": c.get("path"), "line": c.get("line") or c.get("original_line"),
             "near_bot": near(c, spoken), "near_dropped": near(c, dropped),
             "text": _clean(c.get("body", ""))} for c in human_roots]
    return out, pr_line


def unreviewed_pr_line(gh, pr: dict, *, now: str, bot_login: str,
                       logger: logging.Logger) -> dict:
    """`prs.jsonl` line of a PR the bot never reviewed (control group).

    Built from the LIST object (dates, author, state) plus two calls: reviews and review
    comments. No `get_pr`, hence no size: `additions` / `deletions` stay None.
    """
    number = pr["number"]
    comments = gh.list_review_comments(number)
    humans, roots = _human_split(comments)
    return {
        "schema_version": SCHEMA_VERSION, "ts": now, "pr": number, "reviewed": False,
        "control_group": True,
        "state": "merged" if pr.get("merged_at") else pr.get("state"),
        "merged_at": pr.get("merged_at"), "author": _login(pr),
        "head_sha": (pr.get("head") or {}).get("sha"),
        **_cycle(gh, pr, comments, bot_login=bot_login, logger=logger),
        "first_bot_run_at": None,
        "additions": None, "deletions": None, "changed_files": None,
        "human_comments": len(roots), "human_replies": len(humans) - len(roots),
    }


def enrich(gh, root: Path, since: timedelta, logger: logging.Logger, *, with_text: bool,
           only_pr: int | None = None, bot_login: str = "",
           control_group: bool = False) -> dict:
    """Enrich every ledger PR seen in the window, then (`control_group`) record the
    repository's PRs updated in the window that have no run at all. Returns counters."""
    now = datetime.now(UTC).isoformat(timespec="seconds")
    limite = (datetime.now(UTC) - since).isoformat(timespec="seconds")
    all_live = [r for r in read_all(root, FINDINGS_FILE)
                if r.get("variant", "live") == "live" and not r.get("dry_run")]
    all_runs = read_all(root, RUNS_FILE)
    live_runs = [r for r in all_runs
                 if r.get("variant", "live") == "live" and not r.get("dry_run")]
    # First head and first run per PR, over the WHOLE ledger: a PR reviewed before the
    # window still started its cycle then. Runs count as much as findings: a run that
    # found nothing is still a review, and the only trace of it is in `runs.jsonl`.
    first_head: dict[int, str] = {}
    first_run_at: dict[int, str] = {}
    for r in sorted(all_live + live_runs, key=lambda r: r.get("ts", "")):
        first_head.setdefault(r["pr"], r.get("head_sha"))
        first_run_at.setdefault(r["pr"], r.get("ts"))
    rows = [r for r in all_live if r.get("ts", "") >= limite]
    if only_pr is not None:
        rows = [r for r in rows if r.get("pr") == only_pr]
    previous = _last_by_comment(read_all(root, ENRICH_FILE))
    by_pr: dict[int, list[dict]] = {}
    for r in rows:
        by_pr.setdefault(r["pr"], []).append(r)
    # A PR whose runs in the window found nothing still gets its `prs.jsonl` line:
    # otherwise it would look unreviewed and skew coverage.
    for r in live_runs:
        if r.get("ts", "") >= limite and only_pr in (None, r.get("pr")):
            by_pr.setdefault(r["pr"], [])

    files, perms = _Files(gh), _Permissions(gh)
    lines: list[dict] = []
    prs: list[dict] = []
    for pr_number, pr_rows in sorted(by_pr.items()):
        try:
            out, pr_line = enrich_pr(gh, pr_number, pr_rows, previous, with_text=with_text,
                                     logger=logger, now=now, files=files, perms=perms,
                                     first_head=first_head.get(pr_number),
                                     bot_login=bot_login,
                                     first_run_at=first_run_at.get(pr_number),
                                     control_group=control_group and only_pr is None)
        except Exception as e:
            logger.warning("PR #%s: enrichment failed (%s: %s)", pr_number,
                           type(e).__name__, e)
            continue
        lines.extend(out)
        prs.append(pr_line)

    unreviewed = 0
    if control_group and only_pr is None:
        # "No run" = no run of ANY variant or mode: a PR seen only in dry-run or by the
        # baseline is not a clean control either, so it is left out of both groups.
        seen = {r.get("pr") for r in read_all(root, FINDINGS_FILE) + all_runs}
        try:
            candidates = gh.list_prs_updated_since(limite)
        except Exception as e:
            logger.warning("control group: PR list unreadable (%s: %s)", type(e).__name__, e)
            candidates = []
        for pr in candidates:
            # PRs opened by tools (dependency updates…) are not what the bot is compared
            # on: a human cycle against a robot one would flatter or sink it at random.
            if pr.get("number") in seen or _is_machine(pr):
                continue
            try:
                prs.append(unreviewed_pr_line(gh, pr, now=now, bot_login=bot_login,
                                              logger=logger))
                unreviewed += 1
            except Exception as e:
                logger.warning("PR #%s: control-group line failed (%s: %s)", pr.get("number"),
                               type(e).__name__, e)

    write_part(root, ENRICH_FILE, lines)
    write_part(root, PRS_FILE, prs)
    logger.info("enrich: %d PR(s) reviewed, %d unreviewed (control group%s), "
                "%d comment(s) tracked (%s)", len(prs) - unreviewed, unreviewed,
                "" if control_group and only_pr is None else ": off", len(lines), root)
    return {"prs": len(prs) - unreviewed, "unreviewed": unreviewed, "comments": len(lines)}
