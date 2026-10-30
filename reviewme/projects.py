"""PER-PROJECT configuration and reviewer specs.

The unit of configuration is the **project**, not the reviewer: a
`config/projects/<repo>/` folder carries its guidelines AND its complete reviewers
(persona included). Two teams on two products write genuinely different personas — so
there is deliberately NO inheritance between projects. Only the output
contract stays in the core (`config/output-contract.md`): it is an interface with
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
    """Where projects live.

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
_TRIAGE_FILE = CONFIG_DIR / "triage-method.md"
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


#: Dashboard sections (`[stats] sections`), in their default menu order.
STATS_SECTIONS = ("summary", "quality", "usage", "cost", "roi", "reliability", "tuning",
                  "history", "hall_of_fame")
#: Enabled when `[stats] sections` is absent: all of them. `history` says how to fill it
#: (`reviewme history`) until its file exists; `roi` shows its measured levels and says
#: why the estimate is hidden until `[stats.roi]` exists.
STATS_DEFAULT_SECTIONS = STATS_SECTIONS
STATS_NAMES = ("login", "masked")
STATS_PERIODS = ("week", "sprint")
#: GitHub reaction contents -> judgment of the finding. A project with other habits (👀
#: for "seen, wrong") remaps them; the dashboard only reads this vocabulary.
STATS_DEFAULT_JUDGMENT = (("useful", ("+1", "hooray")), ("wrong", ("-1",)),
                          ("off_topic", ("confused",)), ("saved_me", ("rocket",)))
#: `[stats.roi]`: the assumptions of the ROI page's estimate. `hourly_cost` is required
#: (no default could be right for every team); the others default to these values.
#: `usd_rate` converts the API cost (always in USD) into `currency`.
STATS_ROI_DEFAULTS = (("usd_rate", 1.0), ("minutes_per_finding", 12.0),
                      ("minutes_per_blocker", 240.0))
STATS_ROI_NUMERIC = ("hourly_cost", "usd_rate", "minutes_per_finding", "minutes_per_blocker",
                     "human_first_review_hours")
STATS_ROI_DEFAULT_CURRENCY = "EUR"
_STATS_KEYS = {"sections", "names", "window", "period", "sprint_anchor",
               "sprint_anchor_number", "sprint_length_days", "judgment", "roi",
               "control_group", "bot_since", "triage", "eras", "debrief"}
#: `[[stats.eras]]`: named periods of the history and ROI pages.
STATS_ERA_KEYS = {"name", "since", "label", "bot_login"}
#: `[stats.triage]`: the optional LLM classification of contested findings (`reviewme
#: triage`). Off the review path entirely: these only size and name that one call.
STATS_TRIAGE_KEYS = {"enabled", "model", "max_budget_usd"}
STATS_TRIAGE_DEFAULT_BUDGET = 0.50


@dataclass(frozen=True)
class ReviewerSpec:
    """A reviewer as declared by a project."""
    id: str
    project: str
    system_prompt: str                     # content of system.md (the persona, project-specific)
    output_mode: str = "inline"            # inline | global | mixed
    when_paths: tuple[str, ...] = ()       # globs; empty = always triggers
    requires: tuple[str, ...] = ()         # required contexts, e.g. ("jira_ticket",)
    priority: int = 100                    # execution order: lower = higher priority
    max_budget_usd: float | None = None    # None = global budget from the Config
    model: str = ""                        # LLM model of THIS reviewer. Empty = CLAUDE_MODEL,
                                           # otherwise the CLI default.
    mcp_config: str = ""                   # MCP config file, confined to the reviewer's
                                           # folder (library docs, tooling…)
    precheck: str = ""                     # optional deterministic script
    common: str = ""                       # project's shared guidelines (language, tone, format)
    directory: Path | None = None          # reviewer folder (precheck resolution)
    enabled: bool = True                   # False = present but disabled
    context_read: tuple[str, ...] = ()     # convention files/folders to read IN the
                                           # reviewed repo — never copied here
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
    show_model: bool = False               # `[review] show_model`: model line on comments
    show_score: bool = False               # `[review] show_score`: score on each finding
    ledger_include_text: bool = False      # `[ledger] include_text` from project.toml: does
                                           # the ledger keep the text of the comments?
    # `[stats]` from project.toml: what the dashboard shows and how. Tuples, not lists or
    # dicts, so the config stays hashable like the rest of this frozen object.
    stats_sections: tuple[str, ...] = STATS_DEFAULT_SECTIONS
    stats_names: str = "login"             # "masked" = dev_1, dev_2… instead of logins
    stats_window: str = "90d"              # `enrich.parse_since` syntax
    stats_period: str = "week"             # "week" | "sprint"
    stats_sprint_anchor: str = "2026-01-05"   # first day of sprint `stats_sprint_anchor_number`
    stats_sprint_anchor_number: int = 1
    stats_sprint_length_days: int = 14
    stats_judgment: tuple[tuple[str, tuple[str, ...]], ...] = STATS_DEFAULT_JUDGMENT
    stats_roi: tuple[tuple[str, float], ...] = ()   # `[stats.roi]` numbers, defaults filled
    stats_roi_currency: str = ""           # `[stats.roi] currency` ("" = no table)
    stats_control_group: bool = True       # `reviewme enrich` also records unreviewed PRs
    stats_bot_since: str = ""              # YYYY-MM-DD the bot started ("" = first live run)
    # `[[stats.eras]]` as (name, YYYY-MM-DD, label, bot_login), sorted by date; () = none.
    stats_eras: tuple[tuple[str, str, str, str], ...] = ()
    stats_debrief: bool = True             # the summary's generated "Debrief" card
    stats_triage_enabled: bool = True      # `[stats.triage] enabled`
    stats_triage_model: str = ""           # "" = CLAUDE_MODEL, else the CLI default
    stats_triage_max_budget_usd: float = STATS_TRIAGE_DEFAULT_BUDGET   # per `triage` pass

    def reviewer(self, reviewer_id: str) -> ReviewerSpec | None:
        return next((r for r in self.reviewers if r.id == reviewer_id), None)


# --------------------------------------------------------------------------- loading

def load_output_contract() -> str:
    """The core JSON contract. Not overridable by a project."""
    return _CONTRACT_FILE.read_text(encoding="utf-8") if _CONTRACT_FILE.exists() else ""


def load_review_method() -> str:
    """The core two-phase method. Not overridable by a project."""
    return _METHOD_FILE.read_text(encoding="utf-8") if _METHOD_FILE.exists() else ""


def load_verify_method() -> str:
    """The verification pass guidelines (core)."""
    return _VERIFY_FILE.read_text(encoding="utf-8") if _VERIFY_FILE.exists() else ""


def load_triage_method() -> str:
    """The triage pass guidelines (core): taxonomy and JSON schema the parser expects."""
    return _TRIAGE_FILE.read_text(encoding="utf-8") if _TRIAGE_FILE.exists() else ""


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


def _project_dir(name: str, base_dir: Path | None) -> Path:
    root = base_dir or projects_dir()
    pdir = root / name
    if not pdir.is_dir():
        available = sorted(p.name for p in root.iterdir()
                           if p.is_dir() and not p.name.startswith(".")) if root.is_dir() else []
        raise ProjectConfigError(
            f"project '{name}' not found in {root}"
            + (f" (available: {', '.join(available)})" if available else ""))
    return pdir


def _project_settings(name: str, settings: dict) -> dict:
    """The project-wide fields of `ProjectConfig` (language, ledger, `[stats]`): what
    does not depend on any reviewer, so `load_project_settings` can read them alone."""
    try:
        language = normalize_language(
            (settings.get("review", {}) or {}).get("language")
            or os.environ.get("REVIEWME_LANGUAGE"))
    except ValueError as e:
        raise ProjectConfigError(f"project '{name}': `[review] language`: {e}") from e
    review = settings.get("review", {}) or {}
    ledger = settings.get("ledger", {}) or {}
    return {"language": language,
            "show_model": _review_flag(review, "show_model", name),
            "show_score": _review_flag(review, "show_score", name),
            "ledger_include_text": bool(ledger.get("include_text", False)),
            **_load_stats(settings.get("stats"), name)}


def _review_flag(review: dict, key: str, project_name: str) -> bool:
    """`[review] <key>` as a strict boolean, else `REVIEWME_<KEY>`, else off.

    Strict because a quoted `"false"` is truthy in Python: the comments would carry a line
    the project turned off, with nothing in the logs to say why.
    """
    if key in review:
        value = review[key]
        if not isinstance(value, bool):
            raise ProjectConfigError(f"project '{project_name}': `[review] {key}` must be "
                                     f"true or false, not {value!r}")
        return value
    return _env_flag(f"REVIEWME_{key.upper()}")


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def load_project_settings(name: str, base_dir: Path | None = None) -> ProjectConfig:
    """`<projects>/<name>/project.toml` WITHOUT its reviewers: `reviewers` is empty.

    For the dashboard only. A folder that only holds the stats of a repository (its
    `[stats]` and `bot_since`, no reviewer yet, or a reviewer folder broken by a typo)
    is not a project a review can run with, and `load_project` keeps refusing it; but its
    `[stats]` are still the right settings for the page, and silently dropping them for
    the defaults shows wrong eras and a wrong start date. `[stats]` stays strict here.
    """
    pdir = _project_dir(name, base_dir)
    settings = _read_toml(pdir / "project.toml") if (pdir / "project.toml").is_file() else {}
    return ProjectConfig(name=name, directory=pdir, **_project_settings(name, settings))


def load_project(name: str, base_dir: Path | None = None) -> ProjectConfig:
    """Loads `<projects>/<name>/`. Raises ProjectConfigError if the config is invalid."""
    pdir = _project_dir(name, base_dir)

    # PROJECT settings (not a reviewer's), optional.
    settings = _read_toml(pdir / "project.toml") if (pdir / "project.toml").is_file() else {}

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

    fields = _project_settings(name, settings)
    import dataclasses
    active = tuple(dataclasses.replace(s, language=fields["language"]) for s in active)

    return ProjectConfig(name=name, directory=pdir, reviewers=active, **fields)


def _load_roi(roi, where: str) -> dict:
    """`[stats.roi]` -> (numbers with defaults filled, currency). Strict: an estimate
    built on a typo (`minutes_per_findings`) would print a wrong number with confidence."""
    if not isinstance(roi, dict):
        raise ProjectConfigError(f"{where} must be a table")
    known = {*STATS_ROI_NUMERIC, "currency"}
    unknown = sorted(set(roi) - known)
    if unknown:
        raise ProjectConfigError(f"{where}: unknown key(s) {unknown} "
                                 f"(possible: {', '.join(sorted(known))})")
    bad = sorted(k for k in STATS_ROI_NUMERIC if k in roi
                 and (isinstance(roi[k], bool) or not isinstance(roi[k], int | float)
                      or roi[k] <= 0))
    if bad:
        raise ProjectConfigError(f"{where}: {bad} must be numbers > 0")
    if "hourly_cost" not in roi:
        raise ProjectConfigError(f"{where}: `hourly_cost` is required (the cost of one "
                                 f"hour of a developer, in `currency`)")
    currency = roi.get("currency", STATS_ROI_DEFAULT_CURRENCY)
    if not isinstance(currency, str) or not currency.strip() or len(currency.strip()) > 8:
        raise ProjectConfigError(f"{where} `currency` must be a short text (EUR, USD…)")
    values = {**dict(STATS_ROI_DEFAULTS),
              **{k: float(roi[k]) for k in STATS_ROI_NUMERIC if k in roi}}
    return {"stats_roi": tuple(sorted(values.items())),
            "stats_roi_currency": currency.strip()}


def _load_triage(table, where: str) -> dict:
    """`[stats.triage]` -> `ProjectConfig` fields. Strict like the rest of `[stats]`: a
    `max_budget = 5` typo would otherwise run with the default and nobody would know."""
    if not isinstance(table, dict):
        raise ProjectConfigError(f"{where} must be a table")
    unknown = sorted(set(table) - STATS_TRIAGE_KEYS)
    if unknown:
        raise ProjectConfigError(f"{where}: unknown key(s) {unknown} "
                                 f"(possible: {', '.join(sorted(STATS_TRIAGE_KEYS))})")
    out: dict = {}
    if "enabled" in table:
        if not isinstance(table["enabled"], bool):
            raise ProjectConfigError(f"{where} `enabled` must be true or false")
        out["stats_triage_enabled"] = table["enabled"]
    if "model" in table:
        model = table["model"]
        # Passed as one argv item to the CLI: no shell, but a newline or a space has no
        # business in a model id and would only produce a confusing CLI error later.
        if not isinstance(model, str) or any(c.isspace() for c in model.strip()) \
                or len(model.strip()) > 100:
            raise ProjectConfigError(f"{where} `model` must be a model id (no spaces)")
        out["stats_triage_model"] = model.strip()
    if "max_budget_usd" in table:
        budget = table["max_budget_usd"]
        if isinstance(budget, bool) or not isinstance(budget, int | float) or budget <= 0:
            raise ProjectConfigError(f"{where} `max_budget_usd` must be a number > 0")
        out["stats_triage_max_budget_usd"] = float(budget)
    return out


def _load_eras(eras, where: str) -> dict:
    """`[[stats.eras]]` -> `stats_eras`, sorted by `since`.

    Eras are consecutive: each one runs until the next one's `since`, so the order they
    are written in does not matter (sorted here), but two eras starting the same day
    would leave one of them empty without a word: refused. A `label` names the GitHub
    label that marked the bot-reviewed PRs of that era (a prototype bot, before
    ReviewMe's own label): a typo there only empties a table row, a typo in `since`
    moves every PR, hence the strict date. `bot_login` is the account that era's bot
    posted under when it was a person's (a prototype run with the maintainer's token):
    the dashboard takes that login's reviews and threads out of the human counts of the
    era's labelled PRs, so it needs the `label` (on an unlabelled PR, the same login is
    the person reviewing).
    """
    from datetime import date
    if not isinstance(eras, list) or not all(isinstance(e, dict) for e in eras):
        raise ProjectConfigError(f"{where} must be an array of tables ([[stats.eras]])")
    out: list[tuple[str, str, str, str]] = []
    for i, era in enumerate(eras, 1):
        unknown = sorted(set(era) - STATS_ERA_KEYS)
        if unknown:
            raise ProjectConfigError(f"{where} #{i}: unknown key(s) {unknown} "
                                     f"(possible: {', '.join(sorted(STATS_ERA_KEYS))})")
        name = era.get("name")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 60:
            raise ProjectConfigError(f"{where} #{i}: `name` must be a short text")
        if "since" not in era:
            raise ProjectConfigError(f"{where} '{name.strip()}': `since` is required "
                                     f"(YYYY-MM-DD)")
        try:
            since = date.fromisoformat(str(era["since"])).isoformat()
        except ValueError as e:
            raise ProjectConfigError(f"{where} '{name.strip()}': `since`: expected "
                                     f"YYYY-MM-DD") from e
        label = era.get("label", "")
        if not isinstance(label, str) or len(label.strip()) > 100:
            raise ProjectConfigError(f"{where} '{name.strip()}': `label` must be a "
                                     f"GitHub label name")
        bot_login = era.get("bot_login", "")
        if not isinstance(bot_login, str) or any(c.isspace() for c in bot_login.strip()) \
                or len(bot_login.strip()) > 100:
            raise ProjectConfigError(f"{where} '{name.strip()}': `bot_login` must be a "
                                     f"GitHub login")
        if bot_login.strip() and not label.strip():
            raise ProjectConfigError(f"{where} '{name.strip()}': `bot_login` needs a "
                                     f"`label` (only labelled PRs are corrected)")
        out.append((name.strip(), since, label.strip(), bot_login.strip()))
    out.sort(key=lambda e: e[1])
    days = [e[1] for e in out]
    doubles = sorted({d for d in days if days.count(d) > 1})
    if doubles:
        raise ProjectConfigError(f"{where}: two eras start on {', '.join(doubles)} "
                                 f"(each era needs its own `since`)")
    names = [e[0] for e in out]
    if len(set(names)) != len(names):
        raise ProjectConfigError(f"{where}: era names must be unique")
    return {"stats_eras": tuple(out)}


def _load_stats(table, project_name: str) -> dict:
    """`[stats]` of project.toml -> `ProjectConfig` fields. Absent = the defaults.

    Strict on purpose: a typo in a section id (`hall-of-fame`) or a key (`windows`) would
    otherwise silently hide a page, and nobody notices a page that is not there.
    """
    if table is None:
        return {}
    where = f"project '{project_name}': `[stats]`"
    if not isinstance(table, dict):
        raise ProjectConfigError(f"{where} must be a table")
    unknown = sorted(set(table) - _STATS_KEYS)
    if unknown:
        raise ProjectConfigError(f"{where}: unknown key(s) {unknown} "
                                 f"(possible: {', '.join(sorted(_STATS_KEYS))})")
    out: dict = {}
    if "sections" in table:
        sections = table["sections"]
        if not isinstance(sections, list) or not all(isinstance(x, str) for x in sections):
            raise ProjectConfigError(f"{where} `sections` must be a list of section ids")
        bad = [x for x in sections if x not in STATS_SECTIONS]
        if bad:
            raise ProjectConfigError(f"{where}: unknown section(s) {bad} "
                                     f"(known: {', '.join(STATS_SECTIONS)})")
        out["stats_sections"] = tuple(dict.fromkeys(sections))     # order kept, no doubles
    for key, allowed in (("names", STATS_NAMES), ("period", STATS_PERIODS)):
        if key in table:
            value = str(table[key]).strip().lower()
            if value not in allowed:
                raise ProjectConfigError(f"{where} `{key}`: {table[key]!r} "
                                         f"(expected: {', '.join(allowed)})")
            out[f"stats_{key}"] = value
    if "window" in table:
        from .enrich import parse_since  # lazy: enrich pulls the GitHub layer
        try:
            parse_since(str(table["window"]))
        except ValueError as e:
            raise ProjectConfigError(f"{where} `window`: {e}") from e
        out["stats_window"] = str(table["window"]).strip()
    if "sprint_anchor" in table:
        from datetime import date
        try:
            out["stats_sprint_anchor"] = date.fromisoformat(
                str(table["sprint_anchor"])).isoformat()
        except ValueError as e:
            raise ProjectConfigError(f"{where} `sprint_anchor`: expected YYYY-MM-DD") from e
    if "bot_since" in table:
        from datetime import date
        try:
            out["stats_bot_since"] = date.fromisoformat(str(table["bot_since"])).isoformat()
        except ValueError as e:
            raise ProjectConfigError(f"{where} `bot_since`: expected YYYY-MM-DD") from e
    if "sprint_anchor_number" in table:
        try:
            out["stats_sprint_anchor_number"] = int(table["sprint_anchor_number"])
        except (TypeError, ValueError) as e:
            raise ProjectConfigError(f"{where} `sprint_anchor_number` must be an "
                                     f"integer") from e
    if "sprint_length_days" in table:
        try:
            length = int(table["sprint_length_days"])
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            raise ProjectConfigError(f"{where} `sprint_length_days` must be an integer > 0")
        out["stats_sprint_length_days"] = length
    if "judgment" in table:
        judgment = table["judgment"]
        known = dict(STATS_DEFAULT_JUDGMENT)
        if not isinstance(judgment, dict):
            raise ProjectConfigError(f"{where} `judgment` must be a table")
        bad = sorted(set(judgment) - set(known))
        if bad:
            raise ProjectConfigError(f"{where} `judgment`: unknown judgment(s) {bad} "
                                     f"(possible: {', '.join(known)})")
        for k, v in judgment.items():
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                raise ProjectConfigError(f"{where} `judgment.{k}` must be a list of "
                                         f"reaction contents")
            known[k] = tuple(v)
        out["stats_judgment"] = tuple(known.items())
    if "debrief" in table:
        if not isinstance(table["debrief"], bool):
            raise ProjectConfigError(f"{where} `debrief` must be true or false")
        out["stats_debrief"] = table["debrief"]
    if "control_group" in table:
        if not isinstance(table["control_group"], bool):
            raise ProjectConfigError(f"{where} `control_group` must be true or false")
        out["stats_control_group"] = table["control_group"]
    if "roi" in table:
        out.update(_load_roi(table["roi"], f"{where} `roi`"))
    if "triage" in table:
        out.update(_load_triage(table["triage"], f"{where} `triage`"))
    if "eras" in table:
        out.update(_load_eras(table["eras"], f"{where} `eras`"))
    return out


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
                         language=spec.language,
                         show_model=_env_flag("REVIEWME_SHOW_MODEL"),
                         show_score=_env_flag("REVIEWME_SHOW_SCORE"))


def resolve_project(config: Config) -> ProjectConfig:
    """The active project: `config.project` if set, otherwise the simple mode one."""
    return load_project(config.project, projects_dir(config)) if config.project else _simple_project(config)


# --------------------------------------------------------------------------- selection

def _matches(path: str, patterns: tuple[str, ...]) -> bool:
    p = PurePosixPath(path)
    return any(p.full_match(pat) for pat in patterns)


def select_reviewers(project: ProjectConfig, changed_paths: list[str],
                     available_context: set[str] | None = None) -> tuple[list[ReviewerSpec], list[tuple[ReviewerSpec, str]]]:
    """Applies per-PR triggering.

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
