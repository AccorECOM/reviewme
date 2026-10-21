"""GitHub authentication: static PAT or **GitHub App** (dedicated identity).

Why an App: a PAT comments under a human's identity (reviews come "from Joey"), does not
survive that person leaving, and does not allow a credible `REQUEST_CHANGES`. An App has
its own `<slug>[bot]` login, explicit permissions, and a short-lived installation token
(1 h) — hence safer than a long-lived PAT.

App auth chain: private key -> JWT RS256 (10 min) -> exchanged for an installation token
(1 h) -> `Authorization: Bearer <token>`. Since the token is short-lived, it is refreshed
automatically: `poll` mode, which runs continuously, would otherwise hit a 401 after an
hour.

Environment variables:
    GITHUB_APP_ID               numeric App identifier
    GITHUB_APP_PRIVATE_KEY_PATH path to the .pem file (never the key in clear in .env)
    GITHUB_APP_INSTALLATION_ID  optional — otherwise resolved from the target repo
If they are absent, we fall back to `GITHUB_TOKEN` (PAT): nothing changes for existing
setups.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import httpx

API_BASE = "https://api.github.com"
_JWT_TTL_S = 540           # 9 min (GitHub caps at 10)
_REFRESH_MARGIN_S = 300    # renew 5 min before expiry
_TIMEOUT_S = 15.0


class GitHubAuthError(RuntimeError):
    """Auth impossible: incomplete configuration, unreadable key, missing installation."""


class TokenProvider:
    """Provides the current token. Static (PAT) implementation by default."""

    def __init__(self, token: str) -> None:
        self._token = token

    def token(self) -> str:
        return self._token

    @property
    def is_app(self) -> bool:
        return False


@dataclass
class _Installation:
    token: str
    expires_at: float


class AppTokenProvider(TokenProvider):
    """GitHub App installation token, renewed automatically."""

    def __init__(self, app_id: str, private_key_path: str, repo: str,
                 installation_id: str = "") -> None:
        self._app_id = str(app_id)
        self._repo = repo
        self._installation_id = str(installation_id or "")
        self._current: _Installation | None = None

        key_file = Path(private_key_path).expanduser()
        if not key_file.exists():
            raise GitHubAuthError(f"GitHub App private key not found: {key_file}")
        self._private_key = key_file.read_text(encoding="utf-8")

    # ---------------------------------------------------------------- internal
    def _jwt(self) -> str:
        try:
            import jwt  # PyJWT[crypto]
        except ImportError as e:  # pragma: no cover - depends on the install
            raise GitHubAuthError(
                "PyJWT missing for GitHub App auth. Install: uv add 'pyjwt[crypto]' "
                "(or `uv sync --extra github-app`)."
            ) from e

        now = int(time.time())
        payload = {"iat": now - 60, "exp": now + _JWT_TTL_S, "iss": self._app_id}
        try:
            return jwt.encode(payload, self._private_key, algorithm="RS256")
        except Exception as e:
            raise GitHubAuthError(f"cannot sign the JWT (invalid .pem key?): {e}") from e

    def _headers(self, bearer: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {bearer}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28"}

    def _resolve_installation_id(self, app_jwt: str) -> str:
        """The App's installation on the target repo (avoids having to configure it by hand)."""
        if not self._repo or "/" not in self._repo:
            raise GitHubAuthError(
                "GITHUB_APP_INSTALLATION_ID missing and repo unknown: cannot resolve "
                "the installation.")
        resp = httpx.get(f"{API_BASE}/repos/{self._repo}/installation",
                         headers=self._headers(app_jwt), timeout=_TIMEOUT_S)
        if resp.status_code == 404:
            raise GitHubAuthError(
                f"the App is not installed on {self._repo} (or has no access to it).")
        if resp.status_code >= 400:
            raise GitHubAuthError(f"installation resolution: HTTP {resp.status_code}")
        return str(resp.json().get("id", ""))

    def _mint(self) -> _Installation:
        app_jwt = self._jwt()
        if not self._installation_id:
            self._installation_id = self._resolve_installation_id(app_jwt)

        resp = httpx.post(
            f"{API_BASE}/app/installations/{self._installation_id}/access_tokens",
            headers=self._headers(app_jwt), timeout=_TIMEOUT_S)
        if resp.status_code == 401:
            raise GitHubAuthError("JWT rejected: check GITHUB_APP_ID and the private key.")
        if resp.status_code >= 400:
            raise GitHubAuthError(
                f"installation token creation: HTTP {resp.status_code} — {resp.text[:200]}")

        data = resp.json()
        expires = data.get("expires_at", "")
        try:
            from datetime import datetime
            expires_ts = datetime.fromisoformat(expires.replace("Z", "+00:00")).timestamp()
        except (ValueError, AttributeError):
            expires_ts = time.time() + 3600
        return _Installation(token=data["token"], expires_at=expires_ts)

    # ---------------------------------------------------------------- public
    def token(self) -> str:
        if self._current is None or time.time() >= self._current.expires_at - _REFRESH_MARGIN_S:
            self._current = self._mint()
        return self._current.token

    @property
    def is_app(self) -> bool:
        return True

    def bot_login(self) -> str:
        """The bot's login (`<slug>[bot]`), useful to recognize its own comments."""
        resp = httpx.get(f"{API_BASE}/app", headers=self._headers(self._jwt()), timeout=_TIMEOUT_S)
        if resp.status_code >= 400:
            return ""
        slug = resp.json().get("slug", "")
        return f"{slug}[bot]" if slug else ""


def build_provider(config) -> TokenProvider:
    """PAT by default; GitHub App as soon as `GITHUB_APP_ID` and the private key are provided."""
    if config.github_app_id and config.github_app_private_key_path:
        return AppTokenProvider(
            app_id=config.github_app_id,
            private_key_path=config.github_app_private_key_path,
            repo=config.github_repo,
            installation_id=config.github_app_installation_id,
        )
    if not config.github_token:
        raise GitHubAuthError(
            "no authentication: set GITHUB_TOKEN (PAT) or GITHUB_APP_ID + "
            "GITHUB_APP_PRIVATE_KEY_PATH (GitHub App).")
    return TokenProvider(config.github_token)
