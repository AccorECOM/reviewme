"""GitHub REST client for ReviewMe.

What inline comments and the thread lifecycle require:
- list_review_comments : list review comments (dedup)
- create_review        : post a review with a batch of inline comments (1 write)
- reply_to_comment     : reply in an existing thread (re-anchoring / dev dialogue)
- list_open_prs        : "any open PR" mode (no label filter)
- get_authenticated_login : the token's identity

Thread resolve/unresolve (GraphQL `resolveReviewThread`) is deliberately ABSENT: GitHub
already collapses outdated comments, the bot replies "no longer appears" in the thread,
and the mutation requires `pull_requests: write` (scope to check on each instance's
token). READING the threads' state (`review_threads`) feeds the ledger.
"""
from __future__ import annotations

import time

import httpx

from .config import Config

API_BASE = "https://api.github.com"


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
            resp = self._request("POST", f"{API_BASE}/graphql", json={
                "query": query,
                "variables": {"o": owner, "n": name, "pr": pr_number, "after": after}})
            resp.raise_for_status()
            data = resp.json()
            pr = (((data.get("data") or {}).get("repository") or {}).get("pullRequest") or {})
            threads = pr.get("reviewThreads") or {}
            nodes.extend(threads.get("nodes") or [])
            info = threads.get("pageInfo") or {}
            if not info.get("hasNextPage"):
                break
            after = info.get("endCursor")
        return nodes

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
