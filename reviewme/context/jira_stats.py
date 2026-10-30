"""Jira for `reviewme history --jira`: the ticket type behind each PR, and bugs per month.

Optional, like every context provider: without `JIRA_BASE_URL`, `JIRA_EMAIL` and
`JIRA_API_TOKEN` (the variables `jira.py` already uses) the scan runs without it and says
so. Read-only, generic: nothing here knows a project, a workflow or a custom field.

- **Ticket per PR**: the key is read from the head branch only (`jira.extract_ticket_key`
  with `JIRA_KEY_PREFIX`), since titles are not stored. Keys are looked up 50 at a time
  with one JQL search (`key in (…)`) and cached; a batch Jira refuses (one key that does
  not exist, or that this account cannot see, fails the whole query) falls back to one
  `GET /issue/{key}` per key of that batch, so a 4,000-PR scan with ~3,000 tickets costs
  ~60 calls, not 3,000.
- **Bug type**: `Bug`, `Bogue` and `Defect` (any case), plus the names in
  `JIRA_BUG_TYPES` (comma-separated, default `Bug`). That variable is also the
  `issuetype in (…)` list of the monthly count: JQL rejects a type name the instance does
  not have, so only the instance's own names go there.
- **Bugs per month**: `project in (<keys>) AND issuetype in (…) AND created >= since`,
  paginated, `fields=created` only. Projects = `JIRA_KEY_PREFIX`, else every project
  whose keys appear in the scanned branches. Every month of the period gets a row, zeros
  included: a missing month would read as "no data" and lift the average.

Jira Cloud's `/rest/api/3/search/jql` (token pagination) is tried first; a 404 falls back
to `/rest/api/2/search` (Server / Data Center, offset pagination).
"""
from __future__ import annotations

import logging
import os
from collections import Counter
from collections.abc import Iterator
from datetime import datetime

import httpx

from .jira import extract_ticket_key

BUG_TYPES = frozenset({"bug", "bogue", "defect"})
_BATCH = 50
_PAGE = 100
_TIMEOUT_S = 20.0


def configured() -> bool:
    return all(os.environ.get(k) for k in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"))


def bug_type_names() -> list[str]:
    raw = os.environ.get("JIRA_BUG_TYPES", "Bug")
    return [x.strip() for x in raw.split(",") if x.strip()] or ["Bug"]


def is_bug_type(name: str | None) -> bool:
    n = (name or "").strip().lower()
    return bool(n) and (n in BUG_TYPES or n in {x.lower() for x in bug_type_names()})


class JiraClient:
    """The two reads the history needs. `http` is injectable for tests."""

    def __init__(self, base_url: str, email: str, token: str, http=None) -> None:
        self.base = base_url.rstrip("/")
        self.http = http or httpx.Client(auth=(email, token), timeout=_TIMEOUT_S,
                                         headers={"Accept": "application/json"})
        self.calls = 0

    @classmethod
    def from_env(cls) -> JiraClient | None:
        if not configured():
            return None
        return cls(os.environ["JIRA_BASE_URL"], os.environ["JIRA_EMAIL"],
                   os.environ["JIRA_API_TOKEN"])

    def close(self) -> None:
        close = getattr(self.http, "close", None)
        if close:
            close()

    def _get(self, path: str, params: dict) -> httpx.Response:
        self.calls += 1
        return self.http.get(f"{self.base}{path}", params=params)

    def get_issue(self, key: str, fields: str = "issuetype,created") -> dict | None:
        """`fields` of one issue, None when missing or not visible."""
        resp = self._get(f"/rest/api/3/issue/{key}", {"fields": fields})
        if resp.status_code in (403, 404):
            return None
        resp.raise_for_status()
        return resp.json()

    def search(self, jql: str, fields: str) -> Iterator[dict]:
        """Every issue matching `jql`. Raises httpx.HTTPStatusError when Jira refuses it."""
        token = None
        while True:
            params = {"jql": jql, "fields": fields, "maxResults": _PAGE}
            if token:
                params["nextPageToken"] = token
            resp = self._get("/rest/api/3/search/jql", params)
            if resp.status_code == 404 and token is None:
                yield from self._search_v2(jql, fields)
                return
            resp.raise_for_status()
            body = resp.json()
            yield from body.get("issues") or []
            token = body.get("nextPageToken")
            if not token or body.get("isLast"):
                return

    def _search_v2(self, jql: str, fields: str) -> Iterator[dict]:
        start = 0
        while True:
            resp = self._get("/rest/api/2/search", {"jql": jql, "fields": fields,
                                                     "maxResults": _PAGE, "startAt": start})
            resp.raise_for_status()
            body = resp.json()
            issues = body.get("issues") or []
            yield from issues
            start += len(issues)
            if not issues or start >= int(body.get("total") or 0):
                return


def _fields(issue: dict | None) -> dict:
    f = (issue or {}).get("fields") or {}
    return {"type": (f.get("issuetype") or {}).get("name"), "created": f.get("created")}


def lookup(client, keys: list[str], logger: logging.Logger) -> dict[str, dict]:
    """`{key: {type, created}}` for the keys Jira knows; unknown keys are left out."""
    out: dict[str, dict] = {}
    for i in range(0, len(keys), _BATCH):
        batch = keys[i:i + _BATCH]
        try:
            for issue in client.search(f"key in ({', '.join(batch)})", "issuetype,created"):
                if issue.get("key"):
                    out[issue["key"]] = _fields(issue)
            continue
        except Exception as e:                   # one bad key fails the whole JQL
            logger.info("jira: batch of %d key(s) refused (%s), one call per key",
                        len(batch), type(e).__name__)
        for key in batch:
            try:
                issue = client.get_issue(key)
            except Exception as e:
                logger.info("jira: %s unreadable (%s)", key, type(e).__name__)
                continue
            if issue is not None:
                out[key] = _fields(issue)
    return out


def link_tickets(rows: list[dict], client, logger: logging.Logger) -> dict:
    """Add `jira_key`, `jira_issue_type`, `jira_created_at`, `is_bug_ticket` to the
    `history.jsonl` rows (in place). Returns counters."""
    prefix = os.environ.get("JIRA_KEY_PREFIX", "")
    for r in rows:
        r["jira_key"] = extract_ticket_key({"head": {"ref": r.get("head_ref") or ""}}, prefix)
    keys = sorted({r["jira_key"] for r in rows if r["jira_key"]})
    found = lookup(client, keys, logger)
    for r in rows:
        info = found.get(r["jira_key"] or "", {})
        r["jira_issue_type"] = info.get("type")
        r["jira_created_at"] = info.get("created")
        r["is_bug_ticket"] = is_bug_type(info.get("type")) if info else None
    linked = sum(1 for r in rows if r["jira_issue_type"])
    logger.info("jira: %d PR(s) with a ticket key, %d distinct key(s), %d found",
                sum(1 for r in rows if r["jira_key"]), len(keys), len(found))
    return {"keys": len(keys), "found": len(found), "linked_prs": linked,
            "bug_prs": sum(1 for r in rows if r["is_bug_ticket"])}


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def bug_months(client, rows: list[dict], since: datetime, now: datetime,
               logger: logging.Logger) -> list[dict]:
    """`[{projects, month, bugs}]`, one per month from `since` to `now`, zeros included.
    Empty (with a log line) when no project can be named."""
    from ..pr_history import months_between
    prefix = os.environ.get("JIRA_KEY_PREFIX", "").strip()
    if prefix:
        projects = [prefix.upper()]
    else:
        projects = sorted({k.split("-")[0] for r in rows if (k := r.get("jira_key"))})
    if not projects:
        logger.info("jira: no project to count bugs in (set JIRA_KEY_PREFIX)")
        return []
    types = bug_type_names()
    jql = (f"project in ({', '.join(_quote(p) for p in projects)}) AND issuetype in "
           f"({', '.join(_quote(t) for t in types)}) AND created >= "
           f"{_quote(since.strftime('%Y-%m-%d'))} ORDER BY created ASC")
    try:
        counts = Counter(str(_fields(i)["created"] or "")[:7]
                         for i in client.search(jql, "created"))
    except Exception as e:
        logger.warning("jira: bug count refused (%s); check JIRA_BUG_TYPES against the "
                       "instance's issue type names", type(e).__name__)
        return []
    label = ",".join(projects)
    return [{"projects": label, "month": m, "bugs": counts.get(m, 0)}
            for m in months_between(since, now)]
