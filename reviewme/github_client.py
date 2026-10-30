"""GitHub REST client for ReviewMe.

What inline comments and the thread lifecycle require:
- list_review_comments : list review comments (dedup)
- create_review        : post a review with a batch of inline comments (1 write)
- reply_to_comment     : reply in an existing thread (re-anchoring / dev dialogue)
- list_open_prs        : "any open PR" mode (no label filter)
- get_authenticated_login : the token's identity
- list_pr_reviews / list_prs_updated_since : read-only, for `reviewme enrich` (PR cycle
  times and the control group of PRs the bot never reviewed)
- closed_prs_page : read-only GraphQL, for `reviewme history` (the team's PR workflow
  over years, before and after the bot)

Thread resolve/unresolve (GraphQL `resolveReviewThread`) is deliberately ABSENT: GitHub
already collapses outdated comments, the bot replies "no longer appears" in the thread,
and the mutation requires `pull_requests: write` (scope to check on each instance's
token). READING the threads' state (`review_threads`) feeds the ledger.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime

import httpx

from .config import Config

API_BASE = "https://api.github.com"


def _parse_iso(ts: str | None) -> datetime:
    """GitHub `…Z` or ledger `…+00:00` timestamp; unreadable = the oldest possible date."""
    try:
        dt = datetime.fromisoformat(ts or "")
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class GitHubApiError(RuntimeError):
    """GitHub API error carrying the HTTP code and body, for defensive handling
    (e.g. 422 on a review batch)."""

    def __init__(self, status_code: int, body: str, message: str = "") -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(message or f"GitHub API {status_code}: {body[:400]}")


class GitHubClient:
    def __init__(self, config: Config) -> None:
        from .github_auth import build_provider
        self._config = config
        self._repo = config.github_repo
        self._auth = build_provider(config)
        self._client = httpx.Client(headers=config.api_headers, timeout=30.0)

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------------ requests
    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Request with a simple backoff on secondary rate limit (403/429)."""
        # A GitHub App token expires in 1 h: set it again on every request rather than
        # freezing it when the client is built (poll mode runs continuously).
        self._client.headers["Authorization"] = f"Bearer {self._auth.token()}"
        for attempt in range(3):
            resp = self._client.request(method, url, **kwargs)
            if resp.status_code in (403, 429):
                retry_after = resp.headers.get("retry-after")
                reset = resp.headers.get("x-ratelimit-remaining")
                if retry_after or reset == "0":
                    wait = int(retry_after) if retry_after and retry_after.isdigit() else 60
                    if attempt < 2:
                        time.sleep(min(wait, 90))
                        continue
            return resp
        return resp

    # ------------------------------------------------------------------ PR reading
    def get_authenticated_login(self) -> str:
        # A GitHub App has no `/user`: its identity is `<slug>[bot]`.
        if getattr(self._auth, "is_app", False):
            return self._auth.bot_login()
        resp = self._request("GET", f"{API_BASE}/user")
        if resp.status_code == 200:
            return resp.json().get("login", "")
        return ""

    def list_labeled_prs(self) -> list[dict]:
        query = f"repo:{self._repo} is:pr is:open label:{self._config.review_label}"
        resp = self._request("GET", f"{API_BASE}/search/issues", params={"q": query, "per_page": 50})
        resp.raise_for_status()
        return [self.get_pr(item["number"]) for item in resp.json().get("items", [])]

    def list_open_prs(self) -> list[dict]:
        """All open PRs (review_all_prs mode, no label filter)."""
        prs: list[dict] = []
        page = 1
        while True:
            resp = self._request(
                "GET", f"{API_BASE}/repos/{self._repo}/pulls",
                params={"state": "open", "per_page": 100, "page": page},
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            prs.extend(batch)
            page += 1
        return prs

    def get_pr(self, pr_number: int) -> dict:
        resp = self._request("GET", f"{API_BASE}/repos/{self._repo}/pulls/{pr_number}")
        resp.raise_for_status()
        return resp.json()

    def get_pr_diff(self, pr_number: int) -> str:
        resp = self._request(
            "GET", f"{API_BASE}/repos/{self._repo}/pulls/{pr_number}",
            headers={"Accept": "application/vnd.github.diff"},
        )
        resp.raise_for_status()
        return resp.text

    def get_pr_files(self, pr_number: int) -> list[dict]:
        files: list[dict] = []
        page = 1
        while True:
            resp = self._request(
                "GET", f"{API_BASE}/repos/{self._repo}/pulls/{pr_number}/files",
                params={"per_page": 100, "page": page},
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            files.extend(batch)
            page += 1
        return files

    def list_review_comments(self, pr_number: int) -> list[dict]:
        """All of the PR's (inline) review comments, paginated."""
        comments: list[dict] = []
        page = 1
        while True:
            resp = self._request(
                "GET", f"{API_BASE}/repos/{self._repo}/pulls/{pr_number}/comments",
                params={"per_page": 100, "page": page},
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            comments.extend(batch)
            page += 1
        return comments

    def list_review_comments_of_review(self, pr_number: int, review_id: int) -> list[dict]:
        """Comments of ONE review (to link their ids to the posted findings)."""
        comments: list[dict] = []
        page = 1
        while True:
            resp = self._request(
                "GET",
                f"{API_BASE}/repos/{self._repo}/pulls/{pr_number}/reviews/{review_id}/comments",
                params={"per_page": 100, "page": page},
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            comments.extend(batch)
            page += 1
        return comments

    def list_issue_comments(self, pr_number: int) -> list[dict]:
        """The PR's global comments (issue comments), paginated.

        Needed by reviewers in `global` mode: without dedup, they would repost
        their comment on every commit.
        """
        comments: list[dict] = []
        page = 1
        while True:
            resp = self._request(
                "GET", f"{API_BASE}/repos/{self._repo}/issues/{pr_number}/comments",
                params={"per_page": 100, "page": page},
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            comments.extend(batch)
            page += 1
        return comments

    # ------------------------------------------------------------------ fate of findings
    # Read-only, for `reviewme enrich`: what became of the bot's comments.

    def list_comment_reactions(self, comment_id: int) -> list[dict]:
        """Reactions (👍, 👎…) on a review comment."""
        resp = self._request(
            "GET", f"{API_BASE}/repos/{self._repo}/pulls/comments/{comment_id}/reactions",
            params={"per_page": 100},
        )
        if resp.status_code == 404:
            return []                      # comment deleted since
        resp.raise_for_status()
        return resp.json()

    def list_pr_reviews(self, pr_number: int) -> list[dict]:
        """The PR's submitted reviews (approve, request changes, comment), paginated.

        Note: every reply typed in a review thread is a `COMMENTED` review too; the caller
        decides what counts as a review.
        """
        reviews: list[dict] = []
        page = 1
        while True:
            resp = self._request(
                "GET", f"{API_BASE}/repos/{self._repo}/pulls/{pr_number}/reviews",
                params={"per_page": 100, "page": page},
            )
            resp.raise_for_status()
            batch = resp.json()
            reviews.extend(batch)
            # A short page is the last one: no extra request for an empty page. It runs
            # for every PR of the nightly pass, reviewed or not.
            if len(batch) < 100:
                break
            page += 1
        return reviews

    def list_prs_updated_since(self, since_iso: str) -> list[dict]:
        """Every PR (open, closed, merged) updated at or after `since_iso`, newest first.

        The list endpoint has no `since` filter: sort by `updated` descending and stop at
        the first page that reaches past the window, so the cost follows the window, not
        the repository's age. List objects lack `additions` / `deletions`.
        """
        since = _parse_iso(since_iso)
        prs: list[dict] = []
        page = 1
        while True:
            resp = self._request(
                "GET", f"{API_BASE}/repos/{self._repo}/pulls",
                params={"state": "all", "sort": "updated", "direction": "desc",
                        "per_page": 100, "page": page},
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            fresh = [p for p in batch if _parse_iso(p.get("updated_at")) >= since]
            prs.extend(fresh)
            if len(fresh) < len(batch) or len(batch) < 100:
                break
            page += 1
        return prs

    def list_pr_commits(self, pr_number: int) -> list[dict]:
        """The PR's commits, oldest to newest (250 at most, API limit)."""
        commits: list[dict] = []
        page = 1
        while page <= 3:
            resp = self._request(
                "GET", f"{API_BASE}/repos/{self._repo}/pulls/{pr_number}/commits",
                params={"per_page": 100, "page": page},
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            commits.extend(batch)
            page += 1
        return commits

    def get_file_at(self, path: str, ref: str) -> str | None:
        """Raw content of a file at a commit, or None if it does not exist (deleted)."""
        resp = self._request(
            "GET", f"{API_BASE}/repos/{self._repo}/contents/{path}",
            params={"ref": ref},
            headers={"Accept": "application/vnd.github.raw"},
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.text

    def get_permission(self, login: str) -> str:
        """A user's permission on the repo (`admin`, `write`, `read`, `none`)."""
        resp = self._request(
            "GET", f"{API_BASE}/repos/{self._repo}/collaborators/{login}/permission")
        if resp.status_code >= 400:
            return "none"
        return resp.json().get("permission", "none")

    def graphql(self, query: str, variables: dict) -> dict:
        """One GraphQL call: its `data`. Raises GitHubApiError on an HTTP error, or on errors
        that came back without any data (a partial answer is the caller's to judge)."""
        resp = self._request("POST", f"{API_BASE}/graphql",
                             json={"query": query, "variables": variables})
        if resp.status_code >= 400:
            raise GitHubApiError(resp.status_code, resp.text)
        body = resp.json()
        if body.get("errors") and not body.get("data"):
            raise GitHubApiError(resp.status_code, str(body["errors"]))
        return body.get("data") or {}

    def review_threads(self, pr_number: int) -> list[dict]:
        """Review threads (resolved or not) via GraphQL — the REST API does not expose this."""
        owner, name = self._repo.split("/")
        query = """query($o:String!,$n:String!,$pr:Int!,$after:String){repository(owner:$o,name:$n){
          pullRequest(number:$pr){reviewThreads(first:100,after:$after){
          pageInfo{hasNextPage endCursor} nodes{isResolved
          resolvedBy{login} comments(first:1){nodes{databaseId}}}}}}}"""
        nodes: list[dict] = []
        after = None
        for _ in range(20):                # 2,000 threads at most: beyond that, it is no longer a PR
            data = self.graphql(query, {"o": owner, "n": name, "pr": pr_number,
                                        "after": after})
            pr = ((data.get("repository") or {}).get("pullRequest") or {})
            threads = pr.get("reviewThreads") or {}
            nodes.extend(threads.get("nodes") or [])
            info = threads.get("pageInfo") or {}
            if not info.get("hasNextPage"):
                break
            after = info.get("endCursor")
        return nodes

    #: `reviewme history`: closed and merged PRs, newest created first, with what a
    #: workflow comparison needs and nothing written by a person except review bodies,
    #: which are only read to recognise the bot's own reviews (never stored). Cost: about
    #: 2 points per page of 100 (the PR page, then nested `reviews`, `labels` and
    #: `reviewThreads` connections per PR); `rateLimit` comes back with every page for
    #: free. Each thread's first comment author is read so that a bot of an earlier era
    #: that posted under a person's login can be told apart later (`[[stats.eras]]
    #: bot_login`): only the login is kept, never the comment.
    _CLOSED_PRS = """query($o:String!,$n:String!,$first:Int!,$after:String){
      rateLimit{cost remaining resetAt}
      repository(owner:$o,name:$n){pullRequests(states:[MERGED,CLOSED],
        orderBy:{field:CREATED_AT,direction:DESC},first:$first,after:$after){
        pageInfo{hasNextPage endCursor}
        nodes{number createdAt mergedAt closedAt additions deletions changedFiles
          baseRefName headRefName isDraft author{login __typename}
          reviews(first:100){nodes{author{login __typename} state submittedAt body}}
          comments{totalCount} commits{totalCount}
          reviewThreads(first:100){totalCount
            nodes{comments(first:1){nodes{author{login}}}}}
          labels(first:20){nodes{name}}}}}}"""

    def closed_prs_page(self, after: str | None, first: int = 100) -> dict:
        """One page of the repository's merged and closed PRs, newest created first.

        Returns `{"nodes", "has_next", "cursor", "rate_limit"}`. Read-only.
        """
        owner, name = self._repo.split("/")
        data = self.graphql(self._CLOSED_PRS, {"o": owner, "n": name, "first": first,
                                               "after": after})
        prs = (data.get("repository") or {}).get("pullRequests") or {}
        info = prs.get("pageInfo") or {}
        return {"nodes": prs.get("nodes") or [], "has_next": bool(info.get("hasNextPage")),
                "cursor": info.get("endCursor"), "rate_limit": data.get("rateLimit") or {}}

    # ------------------------------------------------------------------ writing
    def post_issue_comment(self, pr_number: int, body: str) -> dict:
        """Global comment (issue comment) — fallback path when inline is impossible."""
        resp = self._request(
            "POST", f"{API_BASE}/repos/{self._repo}/issues/{pr_number}/comments",
            json={"body": body},
        )
        resp.raise_for_status()
        return resp.json()

    def create_review(
        self,
        pr_number: int,
        commit_id: str,
        body: str,
        comments: list[dict],
        event: str = "COMMENT",
    ) -> dict:
        """Post a review with a batch of inline comments in ONE single write.

        `comments`: list of {path, line, side, body}. Raises GitHubApiError on 422
        (atomic batch: to be handled defensively by the caller).
        """
        payload: dict = {"commit_id": commit_id, "event": event, "comments": comments}
        if body:
            payload["body"] = body
        resp = self._request(
            "POST", f"{API_BASE}/repos/{self._repo}/pulls/{pr_number}/reviews", json=payload,
        )
        if resp.status_code >= 400:
            raise GitHubApiError(resp.status_code, resp.text)
        return resp.json()

    def update_issue_comment(self, comment_id: int, body: str) -> dict:
        """Update an existing global comment (`global` mode).

        Update rather than pile up: the `us` reviewer must not leave a new comment on every
        commit.
        """
        resp = self._request(
            "PATCH", f"{API_BASE}/repos/{self._repo}/issues/comments/{comment_id}",
            json={"body": body},
        )
        resp.raise_for_status()
        return resp.json()

    def reply_to_comment(self, pr_number: int, comment_id: int, body: str) -> dict:
        """Reply in an existing thread (comment_id must be the root comment)."""
        resp = self._request(
            "POST",
            f"{API_BASE}/repos/{self._repo}/pulls/{pr_number}/comments/{comment_id}/replies",
            json={"body": body},
        )
        if resp.status_code >= 400:
            raise GitHubApiError(resp.status_code, resp.text)
        return resp.json()

    def check_rate_limit(self) -> dict:
        resp = self._request("GET", f"{API_BASE}/rate_limit")
        resp.raise_for_status()
        return resp.json()["resources"]
