"""Client REST GitHub pour ReviewMe.

Ce qu'exige l'inline et le cycle de vie des threads :
- list_review_comments : lister les commentaires de review (dédup)
- create_review        : poster une review avec un batch de commentaires inline (1 write)
- reply_to_comment     : répondre dans un thread existant (ré-ancrage / dialogue dev)
- list_open_prs        : mode "toute PR ouverte" (sans filtre label)
- get_authenticated_login : identité du token

Le resolve/unresolve de thread (GraphQL) est volontairement ABSENT
(différé v2 : GitHub replie déjà automatiquement les commentaires outdated, et le
resolve exige un 2e client + potentiellement le scope Contents R/W).
"""
from __future__ import annotations

import time

import httpx

from .config import Config

API_BASE = "https://api.github.com"


class GitHubApiError(RuntimeError):
    """Erreur d'API GitHub portant le code HTTP et le corps, pour un traitement défensif
    (ex. 422 sur un batch de review)."""

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

    # ------------------------------------------------------------------ requêtes
    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Requête avec un backoff simple sur secondary rate limit (403/429)."""
        # Le token d'une GitHub App expire en 1 h : on le (re)pose à chaque requête plutôt
        # que de le figer à la construction du client (le mode poll tourne en continu).
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

    # ------------------------------------------------------------------ lecture PR
    def get_authenticated_login(self) -> str:
        # Une GitHub App n'a pas de `/user` : son identité est `<slug>[bot]`.
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
        """Toutes les PR ouvertes (mode review_all_prs, sans filtre label)."""
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
            headers={**self._config.api_headers, "Accept": "application/vnd.github.diff"},
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
        """Tous les commentaires de review (inline) de la PR, paginés."""
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

    def list_issue_comments(self, pr_number: int) -> list[dict]:
        """Commentaires globaux (issue comments) de la PR, paginés.

        Nécessaire aux reviewers en mode `global` (ADR v3 D2) : sans dédup, ils reposteraient
        leur commentaire à chaque commit.
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

    # ------------------------------------------------------------------ écriture
    def post_issue_comment(self, pr_number: int, body: str) -> dict:
        """Commentaire global (issue comment) — chemin de repli quand l'inline est impossible."""
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
        """Poste une review avec un batch de commentaires inline en UN seul write.

        `comments` : liste de {path, line, side, body}. Lève GitHubApiError sur 422
        (batch atomique : à traiter défensivement par l'appelant).
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
        """Met à jour un commentaire global existant (mode `global`, ADR v3 D2).

        Mettre à jour plutôt qu'empiler : le reviewer `us` ne doit pas laisser un nouveau
        commentaire à chaque commit.
        """
        resp = self._request(
            "PATCH", f"{API_BASE}/repos/{self._repo}/issues/comments/{comment_id}",
            json={"body": body},
        )
        resp.raise_for_status()
        return resp.json()

    def reply_to_comment(self, pr_number: int, comment_id: int, body: str) -> dict:
        """Répond dans un thread existant (le comment_id doit être le commentaire racine)."""
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
