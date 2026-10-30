"""Tests for `reviewme history` (`reviewme/pr_history.py`).

What matters: the scan stops at `--since` instead of reading the whole repository, the
bot never counts as a human reviewer (by login, by `[bot]`, or by its signature under a
PAT), the bot-reviewed flag comes from the ledger or the label, and a second pass replaces
the first one's rows instead of doubling them.

Run: `pytest tests/`.
"""
import logging
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reviewme.ledger import FINDINGS_FILE, RUNS_FILE, read_all, write_part
from reviewme.pr_history import HISTORY_FILE, history_row, latest_history, parse_date, scan
from reviewme.strings import ALL_SIGNATURES

LOG = logging.getLogger("test-history")
NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
SINCE = datetime(2025, 1, 1, tzinfo=UTC)


def _pr(n, created, *, merged=None, closed=None, head="feature/x", author="alice",
        reviews=(), labels=(), threads=0, comments=0, commits=1, author_type="User"):
    return {"number": n, "createdAt": created, "mergedAt": merged,
            "closedAt": closed or merged, "additions": 10, "deletions": 5,
            "changedFiles": 2, "baseRefName": "main", "headRefName": head,
            "isDraft": False,
            "author": None if author is None else {"login": author, "__typename": author_type},
            "reviews": {"nodes": list(reviews)}, "comments": {"totalCount": comments},
            "reviewThreads": {"totalCount": threads}, "commits": {"totalCount": commits},
            "labels": {"nodes": [{"name": x} for x in labels]}}


def _review(login, at, state="APPROVED", body="", typename="User"):
    return {"author": {"login": login, "__typename": typename}, "state": state,
            "submittedAt": at, "body": body}


class FakeGH:
    """Two pages, newest created first; the second one crosses `since`."""

    def __init__(self, pages, remaining=4000):
        self.pages, self.calls, self.remaining = pages, [], remaining

    def check_rate_limit(self):
        return {"graphql": {"remaining": self.remaining}}

    def closed_prs_page(self, after, first=100):
        self.calls.append((after, first))
        i = 0 if after is None else int(after)
        return {"nodes": self.pages[i], "has_next": i + 1 < len(self.pages),
                "cursor": str(i + 1),
                "rate_limit": {"cost": 2, "remaining": self.remaining,
                               "resetAt": "2026-10-28T12:05:00Z"}}


def _pages():
    sig = ALL_SIGNATURES[0]
    page1 = [
        _pr(10, "2026-09-01T08:00:00Z", merged="2026-09-03T08:00:00Z", head="hotfix/crash",
            threads=4, comments=2,
            reviews=[_review("alice", "2026-09-01T09:00:00Z", "COMMENTED"),   # author
                     _review("pat-owner", "2026-09-01T09:30:00Z", "COMMENTED",
                             body=f"Review {sig}"),                            # bot via PAT
                     _review("copilot[bot]", "2026-09-01T09:40:00Z", "COMMENTED"),
                     _review("reviewme-app", "2026-09-01T09:45:00Z", "COMMENTED"),
                     _review("bob", "2026-09-02T08:00:00Z"),
                     _review("carol", "2026-09-01T20:00:00Z", "CHANGES_REQUESTED"),
                     _review("dave", None, "PENDING")]),
        _pr(9, "2026-06-01T08:00:00Z", closed="2026-06-02T08:00:00Z", author=None,
            labels=["review-me"]),
        _pr(8, "2026-01-10T08:00:00Z", merged="2026-01-11T08:00:00Z",
            head="revert-7-feature/y", author="dependabot", author_type="Bot"),
    ]
    page2 = [
        _pr(7, "2025-03-01T08:00:00Z", merged="2025-03-05T08:00:00Z", head="bugfix/login"),
        _pr(6, "2024-12-31T23:00:00Z", merged="2025-01-02T08:00:00Z"),   # before since
        _pr(5, "2024-06-01T08:00:00Z", merged="2024-06-02T08:00:00Z"),
    ]
    page3 = [_pr(4, "2024-01-01T08:00:00Z")]                               # never read
    return [page1, page2, page3]


def _ledger(root: Path) -> Path:
    base = {"variant": "live", "dry_run": False, "repo": "o/r"}
    write_part(root, RUNS_FILE, [
        {**base, "ts": "2026-08-31T10:00:00+00:00", "pr": 10, "reviewer": "tech"},
        {**base, "ts": "2026-08-31T10:00:00+00:00", "pr": 7, "dry_run": True},
        {**base, "ts": "2026-08-31T10:00:00+00:00", "pr": 8, "variant": "baseline"},
        {**base, "ts": "2026-08-31T10:00:00+00:00", "pr": 6, "repo": "other/repo"}])
    write_part(root, FINDINGS_FILE, [
        {**base, "pr": 10, "fate": "posted", "comment_id": 1},
        {**base, "pr": 10, "fate": "deduped", "comment_id": 1},
        {**base, "pr": 10, "fate": "posted", "comment_id": 2},
        {**base, "pr": 10, "fate": "posted", "comment_id": 3, "in_summary": True},
        {**base, "pr": 10, "fate": "dropped_low", "comment_id": None}])
    return root


def _scan(root, gh, now=NOW, **kw):
    return scan(gh, root, SINCE, LOG, repo="o/r", bot_login="reviewme-app",
                review_label="review-me", clock=lambda: now, **kw)


def test_two_pages_stop_on_since():
    with tempfile.TemporaryDirectory() as tmp:
        root = _ledger(Path(tmp) / "ledger")
        gh = FakeGH(_pages())
        r = _scan(root, gh)
        rows = {x["pr"]: x for x in latest_history(root)}
    assert gh.calls == [(None, 100), ("1", 100)]           # page 3 never requested
    assert r == {"prs": 4, "pages": 2, "aborted": "", "merged": 3, "bot_reviewed": 2}
    assert sorted(rows) == [7, 8, 9, 10]


def test_row_fields_and_bot_detection():
    with tempfile.TemporaryDirectory() as tmp:
        root = _ledger(Path(tmp) / "ledger")
        _scan(root, FakeGH(_pages()))
        rows = {x["pr"]: x for x in latest_history(root)}
    a = rows[10]
    # alice (author), the PAT review with the signature, copilot[bot], BOT_LOGIN and the
    # pending review are out: bob and carol remain, carol first.
    assert a["human_reviews"] == 2 and a["first_human_review_at"] == "2026-09-01T20:00:00Z"
    assert (a["review_threads"], a["bot_threads"], a["human_threads"]) == (4, 2, 2)
    assert (a["issue_comments"], a["commits"], a["additions"], a["changed_files"]) == (
        2, 1, 10, 2)
    assert a["state"] == "merged" and a["bot_reviewed"] is True
    assert a["is_hotfix_branch"] is True and a["is_revert"] is False
    assert a["is_fix_branch"] is False                     # hotfix/ is not an ordinary fix
    assert a["schema_version"] == 1 and a["ts"] == NOW.isoformat(timespec="seconds")
    b = rows[9]
    assert b["author"] == "?" and b["state"] == "closed"
    assert b["bot_reviewed"] is True                       # by the review label
    assert b["labels"] == ["review-me"]
    c = rows[8]
    assert c["author_is_bot"] is True and c["is_revert"] is True
    assert c["bot_reviewed"] is False                      # baseline only
    d = rows[7]
    assert d["bot_reviewed"] is False                      # dry-run only
    assert d["is_hotfix_branch"] is False and d["is_fix_branch"] is True   # bugfix/


@pytest.mark.parametrize("head,hotfix,fix", [
    ("hotfix/crash", True, False),
    ("HOTFIX-1234", True, False),
    ("release/hotfix-login", True, False),
    ("fix/login", False, True),
    ("bugfix/login", False, True),
    ("team/fix/login", False, True),
    ("fix-1234", False, True),
    ("Fix_login", False, True),
    ("fix", False, True),
    ("hotfix/fix/login", True, False),          # hotfix wins: never counted twice
    ("fixture/data", False, False),
    ("feature/prefix", False, False),
    ("feature/x", False, False),
    ("", False, False),
])
def test_hotfix_and_fix_branches_are_told_apart(head, hotfix, fix):
    from reviewme.pr_history import is_fix_branch, is_hotfix_branch
    assert (is_hotfix_branch(head), is_fix_branch(head)) == (hotfix, fix)


def test_no_text_is_stored():
    sig = ALL_SIGNATURES[0]
    row = history_row(_pr(1, "2026-01-01T00:00:00Z", reviews=[
        _review("bob", "2026-01-01T01:00:00Z", body=f"SECRET-BODY {sig}")]),
        repo="o/r", now="n")
    assert "SECRET" not in str(row) and "title" not in row and "body" not in row
    assert row["human_reviews"] == 0                       # signed: the bot under a PAT


def test_rerun_replaces_rows():
    with tempfile.TemporaryDirectory() as tmp:
        root = _ledger(Path(tmp) / "ledger")
        _scan(root, FakeGH(_pages()))
        pages = _pages()
        pages[0][0]["reviews"]["nodes"].append(_review("erin", "2026-09-01T10:00:00Z"))
        _scan(root, FakeGH(pages), now=datetime(2026, 10, 29, tzinfo=UTC))
        raw = read_all(root, HISTORY_FILE)
        rows = {x["pr"]: x for x in latest_history(root)}
    assert len(raw) == 8 and len(rows) == 4                # 2 parts, latest wins
    assert rows[10]["first_human_review_at"] == "2026-09-01T10:00:00Z"
    assert rows[10]["ts"].startswith("2026-10-29")


def test_low_budget_waits_for_a_near_reset_then_stops_on_a_far_one():
    with tempfile.TemporaryDirectory() as tmp:
        root = _ledger(Path(tmp) / "ledger")
        waits = []
        r = _scan(root, FakeGH(_pages(), remaining=50), sleep=waits.append)
        assert r["pages"] == 2 and waits and 290 <= waits[0] <= 310   # reset in 5 min
        far = datetime(2026, 10, 28, 11, 0, tzinfo=UTC)               # reset in 65 min
        r = _scan(root, FakeGH(_pages(), remaining=50), now=far, sleep=waits.append)
    assert r["pages"] == 1 and "budget low" in r["message"] and r["prs"] == 3


def test_timeout_halves_the_page():
    from reviewme.github_client import GitHubApiError

    class Slow(FakeGH):
        def closed_prs_page(self, after, first=100):
            if first == 100:
                self.calls.append((after, first))
                raise GitHubApiError(502, "timeout")
            return super().closed_prs_page(after, first)

    with tempfile.TemporaryDirectory() as tmp:
        gh = Slow(_pages())
        r = _scan(_ledger(Path(tmp) / "ledger"), gh)
    assert gh.calls[:2] == [(None, 100), (None, 50)] and r["prs"] == 4


def test_client_timeout_halves_the_page_and_keeps_the_cursor():
    import httpx

    class Flaky(FakeGH):
        """`httpx.ReadTimeout` (no status code) once, on the second page."""
        failed = False

        def closed_prs_page(self, after, first=100):
            if after == "1" and not self.failed:
                self.failed = True
                self.calls.append((after, first))
                raise httpx.ReadTimeout("read timed out")
            return super().closed_prs_page(after, first)

    with tempfile.TemporaryDirectory() as tmp:
        gh = Flaky(_pages())
        r = _scan(_ledger(Path(tmp) / "ledger"), gh)
    # Same cursor retried with half the page, and the scan reads everything.
    assert gh.calls == [(None, 100), ("1", 100), ("1", 50)]
    assert r["prs"] == 4 and r["aborted"] == ""


def test_timeouts_down_to_the_floor_give_a_clear_error():
    import httpx

    from reviewme.pr_history import HistoryScanError

    class Dead(FakeGH):
        def closed_prs_page(self, after, first=100):
            self.calls.append((after, first))
            if after is None:
                raise httpx.ReadTimeout("read timed out")
            return super().closed_prs_page(after, first)

    with tempfile.TemporaryDirectory() as tmp:
        gh = Dead(_pages())
        with pytest.raises(HistoryScanError, match="even with pages of 10 PRs"):
            _scan(_ledger(Path(tmp) / "ledger"), gh)
    assert [f for _, f in gh.calls] == [100, 50, 25, 12, 10]

    class DeadLater(FakeGH):
        def closed_prs_page(self, after, first=100):
            self.calls.append((after, first))
            if after is not None:
                raise httpx.ConnectTimeout("connect timed out")
            return super().closed_prs_page(after, first)

    with tempfile.TemporaryDirectory() as tmp:
        root = _ledger(Path(tmp) / "ledger")
        r = _scan(root, DeadLater(_pages()))
        kept = latest_history(root)
    # Page 1 already read: kept, and the reason says what to do.
    assert r["prs"] == 3 and len(kept) == 3
    assert "even with pages of 10 PRs" in r["message"] and "run again later" in r["message"]


def test_parse_date():
    assert parse_date("2024-01-01") == datetime(2024, 1, 1, tzinfo=UTC)
    assert parse_date("10d", NOW) == datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
    with pytest.raises(ValueError):
        parse_date("last year")


def test_graphql_page_parsing():
    """`closed_prs_page` reads the GraphQL envelope; errors without data raise."""
    import types

    from reviewme.github_client import GitHubApiError, GitHubClient

    class Resp:
        def __init__(self, status, body):
            self.status_code, self._body, self.text = status, body, str(body)

        def json(self):
            return self._body

    gh = GitHubClient.__new__(GitHubClient)
    gh._repo = "o/r"
    answers = [Resp(200, {"data": {"rateLimit": {"cost": 1, "remaining": 4999},
                                   "repository": {"pullRequests": {
                                       "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                                       "nodes": [{"number": 1}]}}}}),
               Resp(200, {"errors": [{"message": "bad"}]})]
    sent = []

    def request(self, method, url, **kw):
        sent.append(kw["json"]["variables"])
        return answers.pop(0)

    gh._request = types.MethodType(request, gh)
    page = gh.closed_prs_page(None, 100)
    assert page == {"nodes": [{"number": 1}], "has_next": True, "cursor": "c1",
                    "rate_limit": {"cost": 1, "remaining": 4999}}
    assert sent[0] == {"o": "o", "n": "r", "first": 100, "after": None}
    with pytest.raises(GitHubApiError):
        gh.closed_prs_page("c1")


def test_per_login_review_and_thread_counts_from_a_graphql_page():
    """`review_authors` / `thread_authors` from a fake GraphQL answer, end to end."""
    import types

    from reviewme.github_client import GitHubClient

    class Resp:
        status_code, text = 200, ""

        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    def thread(login):
        return {"comments": {"nodes": [{"author": None if login is None
                                        else {"login": login}}]}}

    node = _pr(1, "2026-01-01T00:00:00Z", merged="2026-01-02T00:00:00Z", author="alice",
               reviews=[_review("maria", "2026-01-01T01:00:00Z", "COMMENTED"),
                        _review("maria", "2026-01-01T02:00:00Z", "COMMENTED"),
                        _review("bob", "2026-01-01T03:00:00Z"),
                        _review("alice", "2026-01-01T04:00:00Z", "COMMENTED"),  # author
                        _review("copilot[bot]", "2026-01-01T05:00:00Z", "COMMENTED"),
                        _review("bob", None, "PENDING")])
    node["reviewThreads"] = {"totalCount": 4, "nodes": [thread("maria"), thread("maria"),
                                                         thread("bob"), thread(None)]}
    gh = GitHubClient.__new__(GitHubClient)
    gh._repo = "o/r"
    sent = []

    def request(self, method, url, **kw):
        sent.append(kw["json"]["query"])
        return Resp({"data": {"rateLimit": {}, "repository": {"pullRequests": {
            "pageInfo": {"hasNextPage": False}, "nodes": [node]}}}})

    gh._request = types.MethodType(request, gh)
    (got,) = gh.closed_prs_page(None)["nodes"]
    assert "reviewThreads(first:100){totalCount" in sent[0]
    assert "comments(first:1){nodes{author{login}}}" in sent[0]
    row = history_row(got, repo="o/r", now="n")
    # The old bot under maria's account is a person to the scan: counted, never filtered.
    assert row["review_authors"] == {"bob": 1, "maria": 2}
    assert row["thread_authors"] == {"?": 1, "bob": 1, "maria": 2}
    assert row["review_first_at"] == {"bob": "2026-01-01T03:00:00Z",
                                      "maria": "2026-01-01T01:00:00Z"}
    assert row["human_reviews"] == 3 and row["review_threads"] == 4     # totals kept


# --------------------------------------------------------------- Jira

class FakeJira:
    """Knows PROJ-1 (Bug), PROJ-2 (Story), APP-7 (Bogue); PROJ-404 does not exist, which
    makes Jira refuse any JQL naming it."""

    ISSUES: ClassVar[dict] = {"PROJ-1": ("Bug", "2025-02-01T10:00:00.000+0000"),
              "PROJ-2": ("Story", "2025-03-01T10:00:00.000+0000"),
              "APP-7": ("Bogue", "2026-01-01T10:00:00.000+0000")}
    BUGS: ClassVar[list] = ["2025-01-15T10:00:00.000+0000", "2025-01-20T10:00:00.000+0000",
            "2025-03-02T10:00:00.000+0000"]

    def __init__(self):
        self.searches, self.gets, self.calls = [], [], 0

    def _issue(self, key):
        t, c = self.ISSUES[key]
        return {"key": key, "fields": {"issuetype": {"name": t}, "created": c}}

    def search(self, jql, fields):
        self.searches.append(jql)
        self.calls += 1
        if jql.startswith("key in"):
            keys = jql[len("key in ("):-1].split(", ")
            if any(k not in self.ISSUES for k in keys):
                raise RuntimeError("400 does not exist")
            return [self._issue(k) for k in keys]
        return [{"key": f"B-{i}", "fields": {"created": c}} for i, c in enumerate(self.BUGS)]

    def get_issue(self, key, fields="issuetype,created"):
        self.gets.append(key)
        self.calls += 1
        return self._issue(key) if key in self.ISSUES else None


def _jira_pages():
    pages = _pages()
    pages[0][0]["headRefName"] = "hotfix/PROJ-1-crash"     # PR 10
    pages[0][1]["headRefName"] = "feature/APP_7-login"     # PR 9
    pages[0][2]["headRefName"] = "feature/PROJ-404"        # PR 8: unknown ticket
    pages[1][0]["headRefName"] = "feature/PROJ-0000-tmpl"  # PR 7: placeholder, no key
    return pages


def test_jira_keys_types_and_bug_months(monkeypatch):
    from reviewme.pr_history import HISTORY_BUGS_FILE, latest_bug_months
    monkeypatch.delenv("JIRA_KEY_PREFIX", raising=False)
    monkeypatch.delenv("JIRA_BUG_TYPES", raising=False)
    jira = FakeJira()
    with tempfile.TemporaryDirectory() as tmp:
        root = _ledger(Path(tmp) / "ledger")
        r = _scan(root, FakeGH(_jira_pages()), jira=jira)
        rows = {x["pr"]: x for x in latest_history(root)}
        bugs = read_all(root, HISTORY_BUGS_FILE)
        months = latest_bug_months(root)
    assert (rows[10]["jira_key"], rows[10]["jira_issue_type"], rows[10]["is_bug_ticket"]) \
        == ("PROJ-1", "Bug", True)
    assert rows[10]["jira_created_at"].startswith("2025-02-01")
    assert (rows[9]["jira_key"], rows[9]["is_bug_ticket"]) == ("APP-7", True)   # Bogue
    assert (rows[8]["jira_key"], rows[8]["jira_issue_type"], rows[8]["is_bug_ticket"]) \
        == ("PROJ-404", None, None)
    assert rows[7]["jira_key"] is None
    # One refused batch, then one GET per key of that batch; the bug count is one search.
    assert jira.gets == ["APP-7", "PROJ-1", "PROJ-404"]
    assert r["jira"] == {"keys": 3, "found": 2, "linked_prs": 2, "bug_prs": 2}
    assert r["jira_calls"] == 5
    count_jql = jira.searches[-1]
    assert count_jql.startswith('project in ("APP", "PROJ") AND issuetype in ("Bug")')
    assert 'created >= "2025-01-01"' in count_jql
    # Every month from since to now, zeros included.
    assert len(bugs) == 22 and r["bug_months"] == 22
    assert months["2025-01"] == 2 and months["2025-02"] == 0 and months["2025-03"] == 1
    assert months["2026-10"] == 0


def test_jira_prefix_and_extra_bug_types(monkeypatch):
    from reviewme.context.jira_stats import is_bug_type
    monkeypatch.setenv("JIRA_KEY_PREFIX", "PROJ")
    monkeypatch.setenv("JIRA_BUG_TYPES", "Incident, Bug")
    jira = FakeJira()
    with tempfile.TemporaryDirectory() as tmp:
        root = _ledger(Path(tmp) / "ledger")
        _scan(root, FakeGH(_jira_pages()), jira=jira)
        rows = {x["pr"]: x for x in latest_history(root)}
    assert rows[9]["jira_key"] is None                     # APP-7 filtered by the prefix
    assert jira.searches[-1].startswith('project in ("PROJ") AND issuetype in '
                                        '("Incident", "Bug")')
    assert is_bug_type("incident") and is_bug_type("DEFECT") and not is_bug_type("Story")
    assert not is_bug_type(None)


def test_jira_client_pagination_and_server_fallback():
    from reviewme.context.jira_stats import JiraClient

    class Resp:
        def __init__(self, status, body=None):
            self.status_code, self._body = status, body or {}

        def json(self):
            return self._body

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(self.status_code)

    class Http:
        def __init__(self, answers):
            self.answers, self.seen = answers, []

        def get(self, url, params):
            self.seen.append((url, dict(params)))
            return self.answers.pop(0)

    cloud = Http([Resp(200, {"issues": [{"key": "A-1"}], "nextPageToken": "t2"}),
                  Resp(200, {"issues": [{"key": "A-2"}], "isLast": True})])
    c = JiraClient("https://jira.example.com/", "e", "t", http=cloud)
    assert [i["key"] for i in c.search("project = A", "created")] == ["A-1", "A-2"]
    assert cloud.seen[1][1]["nextPageToken"] == "t2" and c.calls == 2
    assert cloud.seen[0][0] == "https://jira.example.com/rest/api/3/search/jql"

    server = Http([Resp(404), Resp(200, {"issues": [{"key": "A-1"}], "total": 2}),
                   Resp(200, {"issues": [{"key": "A-2"}], "total": 2})])
    c = JiraClient("https://jira.example.com", "e", "t", http=server)
    assert [i["key"] for i in c.search("project = A", "created")] == ["A-1", "A-2"]
    assert server.seen[2][0].endswith("/rest/api/2/search")
    assert server.seen[2][1]["startAt"] == 1

    missing = Http([Resp(404)])
    assert JiraClient("https://j", "e", "t", http=missing).get_issue("A-9") is None


def test_jira_requires_its_variables(monkeypatch):
    from reviewme.context.jira_stats import JiraClient
    for k in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    assert JiraClient.from_env() is None
    monkeypatch.setenv("JIRA_BASE_URL", "https://j")
    monkeypatch.setenv("JIRA_EMAIL", "e")
    monkeypatch.setenv("JIRA_API_TOKEN", "t")
    assert JiraClient.from_env() is not None
