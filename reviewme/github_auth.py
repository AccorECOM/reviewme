"""Authentification GitHub : PAT statique ou **GitHub App** (identité dédiée).

Pourquoi une App : un PAT commente sous l'identité d'un humain (les reviews arrivent « de
Joey »), ne survit pas au départ de cette personne, et ne permet pas un `REQUEST_CHANGES`
crédible. Une App a son propre login `<slug>[bot]`, ses permissions explicites, et un
token d'installation à durée de vie courte (1 h) — donc plus sûr qu'un PAT longue durée.

Chaîne d'auth d'une App : clé privée -> JWT RS256 (10 min) -> échange contre un token
d'installation (1 h) -> `Authorization: Bearer <token>`. Le token étant court, il est
rafraîchi automatiquement : le mode `poll`, qui tourne en continu, le traverserait sinon
avec un 401 au bout d'une heure.

Variables d'environnement :
    GITHUB_APP_ID               identifiant numérique de l'App
    GITHUB_APP_PRIVATE_KEY_PATH chemin du fichier .pem (jamais la clé en clair dans le .env)
    GITHUB_APP_INSTALLATION_ID  optionnel — sinon résolu depuis le repo cible
Si elles sont absentes, on retombe sur `GITHUB_TOKEN` (PAT) : rien ne change pour
l'existant.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import httpx

API_BASE = "https://api.github.com"
_JWT_TTL_S = 540           # 9 min (GitHub plafonne à 10)
_REFRESH_MARGIN_S = 300    # on renouvelle 5 min avant expiration
_TIMEOUT_S = 15.0


class GitHubAuthError(RuntimeError):
    """Auth impossible : configuration incomplète, clé illisible, installation absente."""


class TokenProvider:
    """Fournit le token courant. Implémentation statique (PAT) par défaut."""

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
    """Token d'installation d'une GitHub App, renouvelé automatiquement."""

    def __init__(self, app_id: str, private_key_path: str, repo: str,
                 installation_id: str = "") -> None:
        self._app_id = str(app_id)
        self._repo = repo
        self._installation_id = str(installation_id or "")
        self._current: _Installation | None = None

        key_file = Path(private_key_path).expanduser()
        if not key_file.exists():
            raise GitHubAuthError(f"clé privée de la GitHub App introuvable : {key_file}")
        self._private_key = key_file.read_text(encoding="utf-8")

    # ---------------------------------------------------------------- interne
    def _jwt(self) -> str:
        try:
            import jwt  # PyJWT[crypto]
        except ImportError as e:  # pragma: no cover - dépend de l'install
            raise GitHubAuthError(
                "PyJWT manquant pour l'auth GitHub App. Installer : uv add 'pyjwt[crypto]' "
                "(ou `uv sync --extra github-app`)."
            ) from e

        now = int(time.time())
        payload = {"iat": now - 60, "exp": now + _JWT_TTL_S, "iss": self._app_id}
        try:
            return jwt.encode(payload, self._private_key, algorithm="RS256")
        except Exception as e:
            raise GitHubAuthError(f"signature du JWT impossible (clé .pem invalide ?) : {e}") from e

    def _headers(self, bearer: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {bearer}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28"}

    def _resolve_installation_id(self, app_jwt: str) -> str:
        """Installation de l'App sur le repo cible (évite d'avoir à la configurer à la main)."""
        if not self._repo or "/" not in self._repo:
            raise GitHubAuthError(
                "GITHUB_APP_INSTALLATION_ID absent et repo inconnu : impossible de résoudre "
                "l'installation.")
        resp = httpx.get(f"{API_BASE}/repos/{self._repo}/installation",
                         headers=self._headers(app_jwt), timeout=_TIMEOUT_S)
        if resp.status_code == 404:
            raise GitHubAuthError(
                f"l'App n'est pas installée sur {self._repo} (ou n'y a pas accès).")
        if resp.status_code >= 400:
            raise GitHubAuthError(f"résolution de l'installation : HTTP {resp.status_code}")
        return str(resp.json().get("id", ""))

    def _mint(self) -> _Installation:
        app_jwt = self._jwt()
        if not self._installation_id:
            self._installation_id = self._resolve_installation_id(app_jwt)

        resp = httpx.post(
            f"{API_BASE}/app/installations/{self._installation_id}/access_tokens",
            headers=self._headers(app_jwt), timeout=_TIMEOUT_S)
        if resp.status_code == 401:
            raise GitHubAuthError("JWT refusé : vérifier GITHUB_APP_ID et la clé privée.")
        if resp.status_code >= 400:
            raise GitHubAuthError(
                f"création du token d'installation : HTTP {resp.status_code} — {resp.text[:200]}")

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
        """Login du bot (`<slug>[bot]`), utile pour reconnaître ses propres commentaires."""
        resp = httpx.get(f"{API_BASE}/app", headers=self._headers(self._jwt()), timeout=_TIMEOUT_S)
        if resp.status_code >= 400:
            return ""
        slug = resp.json().get("slug", "")
        return f"{slug}[bot]" if slug else ""


def build_provider(config) -> TokenProvider:
    """PAT par défaut ; GitHub App dès que `GITHUB_APP_ID` et la clé privée sont fournis."""
    if config.github_app_id and config.github_app_private_key_path:
        return AppTokenProvider(
            app_id=config.github_app_id,
            private_key_path=config.github_app_private_key_path,
            repo=config.github_repo,
            installation_id=config.github_app_installation_id,
        )
    if not config.github_token:
        raise GitHubAuthError(
            "aucune authentification : définir GITHUB_TOKEN (PAT) ou GITHUB_APP_ID + "
            "GITHUB_APP_PRIVATE_KEY_PATH (GitHub App).")
    return TokenProvider(config.github_token)
