"""Configuration ReviewMe (pilotée par .env — le format éprouvé sur 3 mois).

La configuration passe par l'environnement (`.env`) ; le comportement de review passe
multi-équipes est différé en v2). Les secrets viennent UNIQUEMENT de l'environnement.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
REVIEWS_DIR = DATA_DIR / "reviews"
STATE_FILE = DATA_DIR / "state.json"
STATE_LOCK = DATA_DIR / "state.lock"
LOG_FILE = DATA_DIR / "reviews.log"
CONFIG_DIR = ROOT_DIR / "config"


@dataclass(frozen=True)
class Config:
    github_token: str
    github_repo: str
    review_label: str = "review-me"
    poll_interval: int = 300
    repo_path: str = "."
    max_budget_usd: float = 1.00
    claude_agent: str = ""  # vide = persona du reviewer ; sinon nom d'un agent Claude Code
    claude_bin: str = ""    # binaire de la CLI de review. Vide = `claude` du PATH. Permet de
                            # pointer un wrapper maison (passerelle interne, quotas, logs).
    # --- ajouts v2 (inline + garde-fous) ---
    confidence_threshold: int = 80        # findings < seuil non postés (rubrique officielle)
    bot_login: str = ""                   # login du bot si identité dédiée (sinon vide)
    # --- ajouts v3 (reviewers multiples, ADR v3) ---
    # Auth GitHub App (identité dédiée) : si renseignée, elle prime sur le PAT.
    github_app_id: str = ""
    github_app_private_key_path: str = ""
    github_app_installation_id: str = ""   # vide = résolu depuis le repo cible
    config_home: str = ""                 # dépôt de config EXTERNE (REVIEWME_CONFIG_HOME) :
                                          # ses projets priment sur config/projects/ du core
    project: str = ""                     # nom du dossier <projets>/<nom>. Vide = mode
                                          # rétro-compatible v0.2 (reviewer unique `tech`).
    max_comments_per_pr: int = 10         # plafond D6 : tous reviewers confondus, par PR
    reviewer_id: str = "tech"             # identité du reviewer dans le marqueur de dédup.
                                          # STABLE dans le temps pour un projet : le renommer
                                          # ferait reposter tous ses commentaires en double.
    review_all_prs: bool = False          # False = filtre par label ; True = toute PR ouverte
    dry_run: bool = False                 # True = ne poste rien, log seulement

    @property
    def repo_owner(self) -> str:
        return self.github_repo.split("/")[0]

    @property
    def repo_name(self) -> str:
        return self.github_repo.split("/")[1]

    @property
    def uses_github_app(self) -> bool:
        return bool(self.github_app_id and self.github_app_private_key_path)

    @property
    def api_headers(self) -> dict[str, str]:
        """En-têtes de base. L'`Authorization` est réécrite à chaque requête par le client
        (le token d'une GitHub App expire au bout d'une heure)."""
        return {
            "Authorization": f"Bearer {self.github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }


def _env_bool(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


#: Variables JAMAIS lues depuis le `.env` d'une instance : elles décident de ce qui est
#: exécuté ou de l'endroit où l'on s'authentifie. Une config peut venir d'un dépôt tiers.
INSTANCE_ENV_DENYLIST = frozenset({
    "CLAUDE_BIN",                   # binaire lancé comme moteur de review -> RCE
    "CLAUDE_AGENT",
    "REPO_PATH",                    # dépôt exploré par l'agent
    "REVIEWME_CONFIG_HOME",         # redirection de la config elle-même
    "ANTHROPIC_BASE_URL",           # redirection du trafic modèle
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "GITHUB_TOKEN",
    "GITHUB_APP_PRIVATE_KEY_PATH",
    "PATH",
})


def _load_instance_env(path: Path) -> None:
    """Charge le `.env` d'une instance, en écartant les variables sensibles.

    On ne fait PAS confiance à ce fichier : il peut arriver par une Pull Request.
    """
    if not path.is_file():
        return
    values = dotenv_values(path)
    for key, value in values.items():
        if value is None or key in INSTANCE_ENV_DENYLIST:
            continue
        os.environ.setdefault(key, value)   # ne surcharge jamais l'environnement du job


def load_config(*, require_repo: bool = True) -> Config:
    # Le .env de l'INSTANCE prime sur celui du moteur : une instance porte son repo cible,
    # son token et ses réglages. `load_dotenv` n'écrase jamais une variable déjà posée,
    # donc l'ordre fait la priorité.
    #
    # SÉCURITÉ : ce fichier peut venir d'un dépôt que l'on est en train de REVIEWER (config
    # dans `.reviewme/`), donc d'une PR écrite par un tiers. Les variables qui décident
    # QUOI EXÉCUTER en sont exclues : sans ce filtre, une PR ajoutant `.reviewme/.env` avec
    # `CLAUDE_BIN=/bin/sh` obtient l'exécution de code arbitraire dans un job qui porte les
    # secrets de la CI. Ces variables ne se règlent que dans l'environnement du job.
    home = os.environ.get("REVIEWME_CONFIG_HOME", "").strip()
    if home:
        _load_instance_env(Path(home).expanduser() / ".env")
    load_dotenv(ROOT_DIR / ".env")

    token = os.environ.get("GITHUB_TOKEN", "")
    app_id = os.environ.get("GITHUB_APP_ID", "")
    app_key = os.environ.get("GITHUB_APP_PRIVATE_KEY_PATH", "")
    if not token and not (app_id and app_key):
        raise RuntimeError(
            "Aucune authentification GitHub : définir GITHUB_TOKEN (PAT), ou "
            "GITHUB_APP_ID + GITHUB_APP_PRIVATE_KEY_PATH (GitHub App).")

    repo = os.environ.get("GITHUB_REPO", "")
    if require_repo and not repo:
        raise RuntimeError("GITHUB_REPO non défini (env ou .env)")

    return Config(
        github_token=token,
        github_repo=repo,
        review_label=os.environ.get("REVIEW_LABEL", "review-me"),
        poll_interval=int(os.environ.get("POLL_INTERVAL", "300")),
        repo_path=os.environ.get("REPO_PATH", "."),
        max_budget_usd=float(os.environ.get("MAX_BUDGET_USD", "1.00")),
        claude_agent=os.environ.get("CLAUDE_AGENT", ""),
        claude_bin=os.environ.get("CLAUDE_BIN", ""),
        confidence_threshold=int(os.environ.get("CONFIDENCE_THRESHOLD", "80")),
        bot_login=os.environ.get("BOT_LOGIN", ""),
        github_app_id=app_id,
        github_app_private_key_path=app_key,
        github_app_installation_id=os.environ.get("GITHUB_APP_INSTALLATION_ID", ""),
        config_home=os.environ.get("REVIEWME_CONFIG_HOME", ""),
        project=os.environ.get("PROJECT", ""),
        max_comments_per_pr=int(os.environ.get("MAX_COMMENTS_PER_PR", "10")),
        reviewer_id=os.environ.get("REVIEWER_ID", "tech"),
        review_all_prs=_env_bool("REVIEW_ALL_PRS", False),
        dry_run=_env_bool("DRY_RUN", False),
    )
