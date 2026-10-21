"""PER-PROJECT configuration and reviewer specs (ADR v3, D1/D2/D4).

The unit of configuration is the **project**, not the reviewer: a
`config/projects/<repo>/` folder carries its guidelines AND its complete reviewers
(persona included). Two teams on two products write genuinely different personas — so
there is deliberately NO inheritance between projects (see ADR v3 D1). Only the output
contract stays in the core (`config/output-contract.md`, D1bis): it is an interface with
the parser, not editorial content.

Expected layout:

    config/
      output-contract.md                 <- core, not overridable
      projects/
        <repo>/
          common/*.md                    <- guidelines shared by the project's reviewers
          reviewers/                     <- THE source of truth: one folder = one reviewer
            tech/{reviewer.toml, system.md}
            us/{reviewer.toml, system.md}

Simple mode: without `PROJECT`, a virtual project with a single reviewer is built, which
reuses the skeleton from `config/templates/`. No instance to create to get started.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .config import CONFIG_DIR, Config
from .strings import normalize as normalize_language

#: Default location of projects (in the core repo).
PROJECTS_DIR = CONFIG_DIR / "projects"
TEMPLATES_DIR = CONFIG_DIR / "templates"


def projects_dir(config=None) -> Path:
    """Where projects live (ADR v3 D12).

    `REVIEWME_CONFIG_HOME` points to an EXTERNAL configuration repo (one repo per organisation):
    the core then stays untouched and `git pull`-able, the project config lives in its own
    separately versioned repo. Without this variable, we fall back to the repo's `config/projects/`.
    It is deliberately a path, not a submodule: a submodule brings back exactly the
    friction of a fork (init, update, detached HEAD) we are trying to avoid.
    """
    home = (getattr(config, "config_home", "") or os.environ.get("REVIEWME_CONFIG_HOME", "")).strip()
    if home:
        return Path(home).expanduser() / "projects"
    return PROJECTS_DIR
#: Core output contract. Looked up at the root of `config/`, then at the old location.
_CONTRACT_FILE = CONFIG_DIR / "output-contract.md"
#: Review and verification method: core, injected into every reviewer (not
#: overridable, like the contract).
_METHOD_FILE = CONFIG_DIR / "review-method.md"
_VERIFY_FILE = CONFIG_DIR / "verify-method.md"
DEFAULT_MAX_TURNS = 30
DEFAULT_VERIFY_MAX_TURNS = 15

OUTPUT_MODES = ("inline", "global", "mixed")

#: Contexts a reviewer can require via `requires`. Each one maps to a provider in
#: `reviewme/context/`. An unknown value is REJECTED at load time: without this check,
#: a typo (`requires = ["jira"]`) would make the reviewer inert forever, without any
#: message.
KNOWN_CONTEXTS = {
    "jira_ticket": "Ticket + acceptance criteria, via the Jira API (context/jira.py)",
}


class ProjectConfigError(RuntimeError):
    """Missing or invalid project config — we prefer to fail early and clearly."""


@dataclass(frozen=True)
class ReviewerSpec:
    """A reviewer as declared by a project."""
    id: str
    project: str
    system_prompt: str                     # content of system.md (the persona, project-specific)
    output_mode: str = "inline"            # inline | global | mixed  (ADR v3 D2)
    when_paths: tuple[str, ...] = ()       # globs; empty = always triggers (D4 level 2)
    requires: tuple[str, ...] = ()         # required contexts, e.g. ("jira_ticket",)
    priority: int = 100                    # execution order: lower = higher priority (D8)
    max_budget_usd: float | None = None    # None = global budget from the Config
    model: str = ""                        # LLM model of THIS reviewer. Empty = CLAUDE_MODEL,
                                           # otherwise the CLI default.
    mcp_config: str = ""                   # MCP config file, confined to the reviewer's
                                           # folder (library docs, tooling…)
    precheck: str = ""                     # optional deterministic script (D7)
    common: str = ""                       # project's shared guidelines (language, tone, format)
    directory: Path | None = None          # reviewer folder (precheck resolution)
    enabled: bool = True                   # False = present but disabled
    context_read: tuple[str, ...] = ()     # convention files/folders to read IN the
                                           # reviewed repo (D13) — never copied here
    plugins_marketplaces: tuple[str, ...] = ()   # Claude Code marketplaces to declare
    plugins_install: tuple[str, ...] = ()        # plugins to install before the review
    max_turns: int = DEFAULT_MAX_TURNS     # review turns; 30 cuts off a large diff
    verify: bool = True                    # findings verification pass
    verify_model: str = ""                 # verifier model. Empty = the reviewer's
    verify_max_turns: int = DEFAULT_VERIFY_MAX_TURNS
    silent_when_clean: bool = False        # `global` mode: post nothing when there is nothing to say
    context_auto: bool = True              # read CLAUDE.md / AGENTS.md / .claude/rules automatically
    language: str = "en"                   # language of the comments (`[review] language`)
    findings: bool = True                  # False = ADVICE reviewer (e.g. `advisor`):
                                           # never posts a comment, only its
                                           # "Conseil pour la relecture" (`advice`)

    def budget(self, config: Config) -> float:
        return self.max_budget_usd if self.max_budget_usd is not None else config.max_budget_usd


@dataclass(frozen=True)
class ProjectConfig:
    name: str
    directory: Path | None
    reviewers: tuple[ReviewerSpec, ...] = field(default_factory=tuple)
    simple: bool = False                   # True = simple mode's virtual project
    language: str = "en"                   # language of comments and posted texts
    ledger_include_text: bool = False      # `[ledger] include_text` from project.toml: does
                                           # the ledger keep the text of the comments?

    def reviewer(self, reviewer_id: str) -> ReviewerSpec | None:
        return next((r for r in self.reviewers if r.id == reviewer_id), None)


# --------------------------------------------------------------------------- loading

def load_output_contract() -> str:
    """The core JSON contract (D1bis). Not overridable by a project."""
    return _CONTRACT_FILE.read_text(encoding="utf-8") if _CONTRACT_FILE.exists() else ""


def load_review_method() -> str:
    """The core two-phase method. Not overridable by a project."""
    return _METHOD_FILE.read_text(encoding="utf-8") if _METHOD_FILE.exists() else ""


def load_verify_method() -> str:
    """The verification pass guidelines (core)."""
    return _VERIFY_FILE.read_text(encoding="utf-8") if _VERIFY_FILE.exists() else ""


def _read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as e:
        raise ProjectConfigError(f"{path}: invalid TOML ({e})") from e


def _concat_markdown(directory: Path) -> str:
    if not directory.is_dir():
        return ""
    parts = [f"### {md.stem}\n{md.read_text(encoding='utf-8')}"
             for md in sorted(directory.glob("*.md"))]
    return "\n\n".join(parts)


def _load_common(project_dir: Path) -> str:
    """Guidelines SHARED by a project's reviewers (language, tone, comment format).

    This is NOT the place to copy the project's code conventions: they already live in
    the repo under review (`AGENTS.md`, `CONTRIBUTING.md`, internal docs) and the agent
    reads them at the source with Read/Grep — a copy here would silently diverge from the
    repo at the first change. Only put here what exists nowhere in the repo.
    """
    for folder in ("common", "guidelines"):        # `guidelines/`: old name, still accepted
        content = _concat_markdown(project_dir / folder)
        if content:
            return content
    return ""


def _load_reviewer(project_name: str, project_dir: Path, reviewer_id: str,
                   common: str) -> ReviewerSpec:
    rdir = project_dir / "reviewers" / reviewer_id
    system_file = rdir / "system.md"
    if not system_file.exists():
        raise ProjectConfigError(f"reviewer '{reviewer_id}': `system.md` missing in {rdir}")

    data = _read_toml(rdir / "reviewer.toml") if (rdir / "reviewer.toml").exists() else {}
    mode = str(data.get("output_mode", "inline")).lower()
    if mode not in OUTPUT_MODES:
        raise ProjectConfigError(
            f"reviewer '{reviewer_id}': unknown output_mode '{mode}' (expected: {', '.join(OUTPUT_MODES)})")

    requires = tuple(data.get("requires", ()) or ())
    inconnus = [r for r in requires if r not in KNOWN_CONTEXTS]
    if inconnus:
        raise ProjectConfigError(
            f"reviewer '{reviewer_id}': unknown context in `requires`: "
            f"{', '.join(inconnus)}. Possible values: {', '.join(sorted(KNOWN_CONTEXTS))}")

    if data.get("silent_when_clean") and mode != "global":
        raise ProjectConfigError(
            f"reviewer '{reviewer_id}': `silent_when_clean` only applies to output_mode "
            f"\"global\" (here \"{mode}\"): inline, a clean PR already gets nothing")
    when = data.get("when", {}) or {}
    context = data.get("context", {}) or {}
    verification = data.get("verification", {}) or {}
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
        max_turns=_positive_int(data.get("max_turns", DEFAULT_MAX_TURNS), "max_turns",
                                reviewer_id),
        verify=bool(verification.get("enabled", True)),
        verify_model=str(verification.get("model", "")),
        verify_max_turns=_positive_int(
            verification.get("max_turns", DEFAULT_VERIFY_MAX_TURNS),
            "verification.max_turns", reviewer_id),
        silent_when_clean=bool(data.get("silent_when_clean", False)),
        context_auto=bool(context.get("auto", True)),
        findings=bool(data.get("findings", True)),
    )


def _positive_int(value, key: str, reviewer_id: str) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        raise ProjectConfigError(f"reviewer '{reviewer_id}': `{key}` must be an integer > 0")
    return n


#: What a project can adjust on a core reviewer, without copying it.
CORE_OVERRIDES = ("read", "paths", "instructions", "model", "max_turns", "priority", "enabled")


def _adapt_core(spec: ReviewerSpec, overrides: dict, project_name: str) -> ReviewerSpec:
    """Applies `[reviewers.<id>]` from project.toml to a core reviewer.

    - `read`: ADDED to its `context.read` (the project's docs, wherever they are stored);
    - `paths`: replaces its `when.paths`;
    - `instructions`: text appended to the end of its persona (project knowledge);
    - `model`, `max_turns`, `priority`, `enabled`: replace the core values.
    The rest of the reviewer (method, output, verification) stays the core's.
    """
    import dataclasses
    if not isinstance(overrides, dict):
        raise ProjectConfigError(f"project '{project_name}': `[reviewers.{spec.id}]` must "
                                 f"be a table")
    inconnues = sorted(set(overrides) - set(CORE_OVERRIDES))
    if inconnues:
        raise ProjectConfigError(
            f"project '{project_name}': `[reviewers.{spec.id}]`: unknown key(s) "
            f"{inconnues} (possible: {', '.join(CORE_OVERRIDES)})")
    changes: dict = {}
    if "read" in overrides:
        changes["context_read"] = spec.context_read + tuple(overrides["read"] or ())
    if "paths" in overrides:
        changes["when_paths"] = tuple(overrides["paths"] or ())
    if overrides.get("instructions"):
        changes["system_prompt"] = (f"{spec.system_prompt}\n\n# Specifics of this project\n"
                                    f"{str(overrides['instructions']).strip()}")
    if "model" in overrides:
        changes["model"] = str(overrides["model"])
    if "max_turns" in overrides:
        changes["max_turns"] = _positive_int(overrides["max_turns"], "max_turns", spec.id)
    if "priority" in overrides:
        changes["priority"] = int(overrides["priority"])
    if "enabled" in overrides:
        changes["enabled"] = bool(overrides["enabled"])
    return dataclasses.replace(spec, **changes) if changes else spec


def load_project(name: str, base_dir: Path | None = None) -> ProjectConfig:
    """Loads `<projects>/<name>/`. Raises ProjectConfigError if the config is invalid."""
    root = base_dir or projects_dir()
    pdir = root / name
    if not pdir.is_dir():
        available = sorted(p.name for p in root.iterdir()
                           if p.is_dir() and not p.name.startswith(".")) if root.is_dir() else []
        raise ProjectConfigError(
            f"project '{name}' not found in {root}"
            + (f" (available: {', '.join(available)})" if available else ""))

    # PROJECT settings (not a reviewer's), optional.
    settings = _read_toml(pdir / "project.toml") if (pdir / "project.toml").is_file() else {}
    ledger = settings.get("ledger", {}) or {}

    # Two sources of reviewers:
    # - the FOLDERS of `reviewers/`: the project's own reviewers. No list to keep up to date
    #   elsewhere: forgetting a folder there would make it silently inactive;
    # - `[reviewers] core = [...]` from project.toml: CORE reviewers, used as is from
    #   `config/templates/reviewers/`, without copying. They follow the engine's changes.
    #   A local folder with the same name takes precedence (the project wanted its own version).
    # To disable: `enabled = false` in the local reviewer.toml, or remove the id from the
    # `core` list.
    core_ids = (settings.get("reviewers", {}) or {}).get("core", []) or []
    if not isinstance(core_ids, list):
        raise ProjectConfigError(f"project '{name}': `[reviewers] core` must be a list")
    disponibles = sorted(d.name for d in (TEMPLATES_DIR / "reviewers").iterdir()
                         if d.is_dir()) if (TEMPLATES_DIR / "reviewers").is_dir() else []
    inconnus = [r for r in core_ids if r not in disponibles]
    if inconnus:
        raise ProjectConfigError(
            f"project '{name}': unknown core reviewer(s) {inconnus} "
            f"(available: {', '.join(disponibles)})")

    rroot = pdir / "reviewers"
    local_ids = sorted(d.name for d in rroot.iterdir()
                       if d.is_dir() and not d.name.startswith(".")) if rroot.is_dir() else []
    if not local_ids and not core_ids:
        # A completely empty project folder = an uninitialised git submodule nine times out
        # of ten. Say so, otherwise the message sends people looking in the wrong place.
        if not any(pdir.iterdir()):
            raise ProjectConfigError(
                f"project '{name}': empty folder ({pdir}). If it is a git submodule, "
                f"initialise it: `git submodule update --init --recursive`")
        raise ProjectConfigError(
            f"project '{name}': no reviewer (a sub-folder of `reviewers/`, e.g. "
            f"`reviewers/tech/`, or `[reviewers] core = [\"tech\"]` in project.toml)")

    common = _load_common(pdir)
    specs = [_load_reviewer(name, pdir, rid, common) for rid in local_ids]
    reglages = settings.get("reviewers", {}) or {}
    specs += [_adapt_core(_load_reviewer(name, TEMPLATES_DIR, rid, common),
                          reglages.get(rid) or {}, name)
              for rid in core_ids if rid not in local_ids]
    active = tuple(s for s in specs if s.enabled)
    if not active:
        raise ProjectConfigError(
            f"project '{name}': every reviewer is disabled (`enabled = false`)")

    try:
        language = normalize_language(
            (settings.get("review", {}) or {}).get("language")
            or os.environ.get("REVIEWME_LANGUAGE"))
    except ValueError as e:
        raise ProjectConfigError(f"project '{name}': `[review] language`: {e}") from e
    import dataclasses
    active = tuple(dataclasses.replace(s, language=language) for s in active)

    return ProjectConfig(name=name, directory=pdir, reviewers=active,
                         ledger_include_text=bool(ledger.get("include_text", False)),
                         language=language)


def _simple_project(config: Config) -> ProjectConfig:
    """Virtual project of the **simple mode**: a single reviewer, no instance to create.

    It reuses the skeleton from `config/templates/` — the same persona one would get
    with `init-project`. No separate prompt set to maintain in duplicate.
    """
    tech = TEMPLATES_DIR / "reviewers" / "tech"
    system = tech / "system.md"
    spec = ReviewerSpec(
        id=config.reviewer_id or "tech",
        project="(simple mode)",
        system_prompt=system.read_text(encoding="utf-8") if system.exists() else "",
        output_mode="inline",
        common=_concat_markdown(TEMPLATES_DIR / "common"),
        directory=tech,
        language=normalize_language(os.environ.get("REVIEWME_LANGUAGE")),
    )
    return ProjectConfig(name="(simple mode)", directory=None, reviewers=(spec,), simple=True,
                         language=spec.language)


def resolve_project(config: Config) -> ProjectConfig:
    """The active project: `config.project` if set, otherwise the simple mode one."""
    return load_project(config.project, projects_dir(config)) if config.project else _simple_project(config)


# --------------------------------------------------------------------------- selection

def _matches(path: str, patterns: tuple[str, ...]) -> bool:
    p = PurePosixPath(path)
    return any(p.full_match(pat) for pat in patterns)


def select_reviewers(project: ProjectConfig, changed_paths: list[str],
                     available_context: set[str] | None = None) -> tuple[list[ReviewerSpec], list[tuple[ReviewerSpec, str]]]:
    """Applies per-PR triggering (ADR v3 D4, level 2).

    Returns (kept, [(skipped, reason)]). Without this filter, EVERY PR would pay for all
    the project's reviewers — it is the main cost lever of the fan-out.
    """
    available = available_context or set()
    kept: list[ReviewerSpec] = []
    skipped: list[tuple[ReviewerSpec, str]] = []

    for spec in sorted(project.reviewers, key=lambda s: (s.priority, s.id)):
        missing = [r for r in spec.requires if r not in available]
        if missing:
            skipped.append((spec, f"missing context: {', '.join(missing)}"))
            continue
        if spec.when_paths and not any(_matches(p, spec.when_paths) for p in changed_paths):
            skipped.append((spec, "no file of the diff matches when.paths"))
            continue
        kept.append(spec)

    return kept, skipped
