"""Configuration par PROJET et specs de reviewers (ADR v3, D1/D2/D4).

L'unité de configuration est le **projet**, pas le reviewer : un dossier
`config/projects/<repo>/` porte ses guidelines ET ses reviewers complets (persona incluse).
Deux teams sur deux produits écrivent des personas réellement différentes — il n'y a donc
volontairement AUCUN héritage entre projets (cf. ADR v3 D1). Seul le contrat de sortie
reste au core (`config/output-contract.md`, D1bis) : c'est une interface avec le parseur,
pas du contenu éditorial.

Arborescence attendue :

    config/
      output-contract.md                 <- core, non surchargeable
      projects/
        <repo>/
          common/*.md                    <- consignes communes aux reviewers du projet
          reviewers/                     <- LA source de vérité : un dossier = un reviewer
            tech/{reviewer.toml, system.md}
            us/{reviewer.toml, system.md}

Mode simple : sans `PROJECT`, on fabrique un projet virtuel à un seul reviewer, qui
réutilise le squelette de `config/templates/`. Aucune instance à créer pour démarrer.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .config import CONFIG_DIR, Config

#: Emplacement par défaut des projets (dans le repo du core).
PROJECTS_DIR = CONFIG_DIR / "projects"
TEMPLATES_DIR = CONFIG_DIR / "templates"


def projects_dir(config=None) -> Path:
    """Où vivent les projets (ADR v3 D12).

    `REVIEWME_CONFIG_HOME` pointe un dépôt de configuration EXTERNE (un dépôt par organisation) :
    le core reste alors intact et `git pull`-able, la config de projets vit dans son propre
    dépôt versionné à part. Sans cette variable, on retombe sur `config/projects/` du repo.
    C'est délibérément un chemin, pas un submodule : un submodule ramène exactement les
    frictions d'un fork (init, update, HEAD détachée) qu'on cherche à éviter.
    """
    home = (getattr(config, "config_home", "") or os.environ.get("REVIEWME_CONFIG_HOME", "")).strip()
    if home:
        return Path(home).expanduser() / "projects"
    return PROJECTS_DIR
#: Contrat de sortie du core. Cherché à la racine de `config/`, puis à l'ancien emplacement.
_CONTRACT_FILE = CONFIG_DIR / "output-contract.md"

OUTPUT_MODES = ("inline", "global", "mixed")

#: Contextes qu'un reviewer peut exiger via `requires`. Chacun correspond à un fournisseur
#: dans `reviewme/context/`. Une valeur inconnue est REFUSÉE au chargement : sans ce
#: contrôle, une faute de frappe (`requires = ["jira"]`) rendrait le reviewer inerte pour
#: toujours, sans le moindre message.
KNOWN_CONTEXTS = {
    "jira_ticket": "Ticket + critères d'acceptation, via l'API Jira (context/jira.py)",
}


class ProjectConfigError(RuntimeError):
    """Config de projet absente ou invalide — on préfère échouer tôt et clairement."""


@dataclass(frozen=True)
class ReviewerSpec:
    """Un reviewer tel que déclaré par un projet."""
    id: str
    project: str
    system_prompt: str                     # contenu de system.md (la persona, propre au projet)
    output_mode: str = "inline"            # inline | global | mixed  (ADR v3 D2)
    when_paths: tuple[str, ...] = ()       # globs ; vide = se déclenche toujours (D4 niveau 2)
    requires: tuple[str, ...] = ()         # contextes obligatoires, ex. ("jira_ticket",)
    priority: int = 100                    # ordre d'exécution : petit = prioritaire (D8)
    max_budget_usd: float | None = None    # None = budget global de la Config
    model: str = ""                        # modèle LLM de CE reviewer. Vide = CLAUDE_MODEL,
                                           # sinon le défaut de la CLI.
    mcp_config: str = ""                   # fichier de config MCP, confiné au dossier du
                                           # reviewer (doc de bibliothèques, outillage…)
    precheck: str = ""                     # script déterministe optionnel (D7)
    common: str = ""                       # consignes communes du projet (langue, ton, format)
    directory: Path | None = None          # dossier du reviewer (résolution du precheck)
    enabled: bool = True                   # False = présent mais mis en sommeil
    context_read: tuple[str, ...] = ()     # fichiers/dossiers de conventions à lire DANS le
                                           # dépôt reviewé (D13) — jamais recopiés ici
    plugins_marketplaces: tuple[str, ...] = ()   # marketplaces Claude Code à déclarer
    plugins_install: tuple[str, ...] = ()        # plugins à installer avant la review

    def budget(self, config: Config) -> float:
        return self.max_budget_usd if self.max_budget_usd is not None else config.max_budget_usd


@dataclass(frozen=True)
class ProjectConfig:
    name: str
    directory: Path | None
    reviewers: tuple[ReviewerSpec, ...] = field(default_factory=tuple)
    simple: bool = False                   # True = projet virtuel du mode simple

    def reviewer(self, reviewer_id: str) -> ReviewerSpec | None:
        return next((r for r in self.reviewers if r.id == reviewer_id), None)


# --------------------------------------------------------------------------- chargement

def load_output_contract() -> str:
    """Le contrat JSON du core (D1bis). Non surchargeable par un projet."""
    return _CONTRACT_FILE.read_text(encoding="utf-8") if _CONTRACT_FILE.exists() else ""


def _read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as e:
        raise ProjectConfigError(f"{path} : TOML invalide ({e})") from e


def _concat_markdown(directory: Path) -> str:
    if not directory.is_dir():
        return ""
    parts = [f"### {md.stem}\n{md.read_text(encoding='utf-8')}"
             for md in sorted(directory.glob("*.md"))]
    return "\n\n".join(parts)


def _load_common(project_dir: Path) -> str:
    """Consignes COMMUNES aux reviewers d'un projet (langue, ton, format des remarques).

    Ce n'est PAS l'endroit où recopier les conventions de code du projet : celles-ci vivent
    déjà dans le dépôt à reviewer (`AGENTS.md`, `CONTRIBUTING.md`, docs internes) et l'agent
    les lit à la source avec Read/Grep — une copie ici divergerait silencieusement du repo
    dès la première évolution. On ne met ici que ce qui n'existe nulle part dans le repo.
    """
    for folder in ("common", "guidelines"):        # `guidelines/` : ancien nom, encore accepté
        content = _concat_markdown(project_dir / folder)
        if content:
            return content
    return ""


def _load_reviewer(project_name: str, project_dir: Path, reviewer_id: str,
                   common: str) -> ReviewerSpec:
    rdir = project_dir / "reviewers" / reviewer_id
    system_file = rdir / "system.md"
    if not system_file.exists():
        raise ProjectConfigError(f"reviewer '{reviewer_id}' : `system.md` manquant dans {rdir}")

    data = _read_toml(rdir / "reviewer.toml") if (rdir / "reviewer.toml").exists() else {}
    mode = str(data.get("output_mode", "inline")).lower()
    if mode not in OUTPUT_MODES:
        raise ProjectConfigError(
            f"reviewer '{reviewer_id}' : output_mode '{mode}' inconnu (attendu : {', '.join(OUTPUT_MODES)})")

    requires = tuple(data.get("requires", ()) or ())
    inconnus = [r for r in requires if r not in KNOWN_CONTEXTS]
    if inconnus:
        raise ProjectConfigError(
            f"reviewer '{reviewer_id}' : contexte inconnu dans `requires` : "
            f"{', '.join(inconnus)}. Valeurs possibles : {', '.join(sorted(KNOWN_CONTEXTS))}")

    when = data.get("when", {}) or {}
    context = data.get("context", {}) or {}
    plugins = data.get("plugins", {}) or {}
    budget = data.get("max_budget_usd")

    return ReviewerSpec(
        id=reviewer_id,
        project=project_name,
        system_prompt=system_file.read_text(encoding="utf-8"),
        output_mode=mode,
        when_paths=tuple(when.get("paths", ()) or ()),
        requires=requires,
        priority=int(data.get("priority", 100)),
        max_budget_usd=float(budget) if budget is not None else None,
        model=str(data.get("model", "")),
        mcp_config=str((data.get("mcp", {}) or {}).get("config", "")),
        precheck=str(data.get("precheck", "")),
        common=common,
        directory=rdir,
        enabled=bool(data.get("enabled", True)),
        context_read=tuple(context.get("read", ()) or ()),
        plugins_marketplaces=tuple(plugins.get("marketplaces", ()) or ()),
        plugins_install=tuple(plugins.get("install", ()) or ()),
    )


def load_project(name: str, base_dir: Path | None = None) -> ProjectConfig:
    """Charge `<projets>/<name>/`. Lève ProjectConfigError si la config est invalide."""
    root = base_dir or projects_dir()
    pdir = root / name
    if not pdir.is_dir():
        available = sorted(p.name for p in root.iterdir()
                           if p.is_dir() and not p.name.startswith(".")) if root.is_dir() else []
        raise ProjectConfigError(
            f"projet '{name}' introuvable dans {root}"
            + (f" (disponibles : {', '.join(available)})" if available else ""))

    # Les reviewers sont les DOSSIERS de `reviewers/` — pas une liste à tenir à jour ailleurs.
    # Un manifeste séparé serait une seconde source de vérité : y oublier un dossier le rendrait
    # silencieusement inactif. Pour mettre un reviewer en sommeil sans le supprimer :
    # `enabled = false` dans son `reviewer.toml`.
    rroot = pdir / "reviewers"
    if not rroot.is_dir():
        # Un dossier de projet totalement vide = submodule git non initialisé neuf fois sur
        # dix. Le dire, sinon le message parle d'un `reviewers/` manquant et envoie chercher
        # au mauvais endroit.
        if not any(pdir.iterdir()):
            raise ProjectConfigError(
                f"projet '{name}' : dossier vide ({pdir}). S'il s'agit d'un submodule git, "
                f"l'initialiser : `git submodule update --init --recursive`")
        raise ProjectConfigError(f"projet '{name}' : dossier `reviewers/` manquant dans {pdir}")

    ids = sorted(d.name for d in rroot.iterdir() if d.is_dir() and not d.name.startswith("."))
    if not ids:
        raise ProjectConfigError(
            f"projet '{name}' : aucun reviewer dans {rroot} "
            f"(un sous-dossier par reviewer, ex. `reviewers/tech/`)")

    common = _load_common(pdir)
    specs = tuple(_load_reviewer(name, pdir, rid, common) for rid in ids)
    active = tuple(s for s in specs if s.enabled)
    if not active:
        raise ProjectConfigError(
            f"projet '{name}' : tous les reviewers sont désactivés (`enabled = false`)")

    return ProjectConfig(name=name, directory=pdir, reviewers=active)


def _simple_project(config: Config) -> ProjectConfig:
    """Projet virtuel du **mode simple** : un seul reviewer, aucune instance à créer.

    Il réutilise le squelette de `config/templates/` — la même persona que celle qu'on
    obtiendrait avec `init-project`. Pas de jeu de prompts séparé à maintenir en double.
    """
    tech = TEMPLATES_DIR / "reviewers" / "tech"
    system = tech / "system.md"
    spec = ReviewerSpec(
        id=config.reviewer_id or "tech",
        project="(mode simple)",
        system_prompt=system.read_text(encoding="utf-8") if system.exists() else "",
        output_mode="inline",
        common=_concat_markdown(TEMPLATES_DIR / "common"),
        directory=tech,
    )
    return ProjectConfig(name="(mode simple)", directory=None, reviewers=(spec,), simple=True)


def resolve_project(config: Config) -> ProjectConfig:
    """Le projet actif : `config.project` s'il est défini, sinon celui du mode simple."""
    return load_project(config.project, projects_dir(config)) if config.project else _simple_project(config)


# --------------------------------------------------------------------------- sélection

def _matches(path: str, patterns: tuple[str, ...]) -> bool:
    p = PurePosixPath(path)
    return any(p.full_match(pat) for pat in patterns)


def select_reviewers(project: ProjectConfig, changed_paths: list[str],
                     available_context: set[str] | None = None) -> tuple[list[ReviewerSpec], list[tuple[ReviewerSpec, str]]]:
    """Applique le déclenchement par PR (ADR v3 D4, niveau 2).

    Renvoie (retenus, [(écarté, raison)]). Sans ce filtre, CHAQUE PR paierait tous les
    reviewers du projet — c'est le principal levier de coût du fan-out.
    """
    available = available_context or set()
    kept: list[ReviewerSpec] = []
    skipped: list[tuple[ReviewerSpec, str]] = []

    for spec in sorted(project.reviewers, key=lambda s: (s.priority, s.id)):
        missing = [r for r in spec.requires if r not in available]
        if missing:
            skipped.append((spec, f"contexte manquant : {', '.join(missing)}"))
            continue
        if spec.when_paths and not any(_matches(p, spec.when_paths) for p in changed_paths):
            skipped.append((spec, "aucun fichier du diff ne correspond à when.paths"))
            continue
        kept.append(spec)

    return kept, skipped
