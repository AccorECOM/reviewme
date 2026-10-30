"""Dashboard settings: what `[stats]` in `project.toml` asks for, with usable defaults.

The project parser (`projects._load_stats`) validates the table and keeps it as flat
fields on `ProjectConfig`; this object is what the dashboard actually consumes. It exists
on its own so `reviewme stats --html` keeps working WITHOUT a project (simple mode, a CI
runner that only has the ledger): `StatsSettings.default()` is the same page any project
gets when it says nothing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from ..projects import STATS_DEFAULT_JUDGMENT, STATS_DEFAULT_SECTIONS, STATS_SECTIONS, ProjectConfig

#: Judgment ids, in display order. Reactions that map to none of them are counted apart:
#: a 👀 is a signal too, it just says nothing about right or wrong.
JUDGMENTS = tuple(k for k, _ in STATS_DEFAULT_JUDGMENT)


@dataclass(frozen=True)
class Era:
    """One `[[stats.eras]]`: from `since` until the next era's `since`."""
    name: str
    since: date
    label: str = ""                        # GitHub label of the era's bot-reviewed PRs
    bot_login: str = ""                    # account the era's bot posted under, if a person's


@dataclass(frozen=True)
class StatsSettings:
    sections: tuple[str, ...] = STATS_DEFAULT_SECTIONS
    names: str = "login"                   # "login" | "masked"
    window: str = "90d"                    # label as written; `window_delta` is the value
    period: str = "week"                   # "week" | "sprint"
    sprint_anchor: date = date(2026, 1, 5)
    sprint_anchor_number: int = 1
    sprint_length_days: int = 14
    judgment: tuple[tuple[str, tuple[str, ...]], ...] = STATS_DEFAULT_JUDGMENT
    roi: tuple[tuple[str, float], ...] = ()   # `[stats.roi]`, defaults filled; () = absent
    roi_currency: str = "EUR"
    # Day the bot started, for the before/after pages. None = the first live run's day.
    bot_since: date | None = None
    # Named periods, sorted by `since`. () = one implicit era, "ReviewMe", from the start
    # date above: the pages then read exactly as before eras existed.
    eras: tuple[Era, ...] = ()
    debrief: bool = True                   # `[stats] debrief`: the summary's opening card
    project: str = ""                      # display name in the header ("" = none)
    # `[ledger] include_text` (or its env override): the only thing that lets the page
    # print text written by a model about the PRs (the triage's recurring-miss titles).
    include_text: bool = False
    # The project's folder, to point a contested rule at the prompt file that defines it.
    # Not a display setting: two settings that only differ by it render the same numbers.
    project_dir: Path | None = field(default=None, compare=False)
    # Overrides the parsed `window` when a caller already has a timedelta (`export --since`).
    window_override: timedelta | None = field(default=None, compare=False)

    @classmethod
    def default(cls, project: str = "") -> StatsSettings:
        return cls(project=project)

    @classmethod
    def from_project(cls, project: ProjectConfig) -> StatsSettings:
        from ..ledger import include_text
        return cls(
            sections=project.stats_sections,
            names=project.stats_names,
            window=project.stats_window,
            period=project.stats_period,
            sprint_anchor=date.fromisoformat(project.stats_sprint_anchor),
            sprint_anchor_number=project.stats_sprint_anchor_number,
            sprint_length_days=project.stats_sprint_length_days,
            judgment=project.stats_judgment,
            roi=project.stats_roi,
            roi_currency=project.stats_roi_currency or "EUR",
            bot_since=date.fromisoformat(project.stats_bot_since)
            if project.stats_bot_since else None,
            debrief=project.stats_debrief,
            eras=tuple(Era(n, date.fromisoformat(d), lbl, bot)
                       for n, d, lbl, bot in project.stats_eras),
            project="" if project.simple else project.name,
            include_text=include_text(project),
            project_dir=None if project.simple else project.directory,
        )

    @property
    def window_delta(self) -> timedelta:
        if self.window_override is not None:
            return self.window_override
        from ..enrich import parse_since
        return parse_since(self.window)

    @property
    def window_label(self) -> str:
        """`90d` as written, or the override spelled the same way (`7d`, `36h`)."""
        if self.window_override is None:
            return self.window
        secs = int(self.window_override.total_seconds())
        return f"{secs // 86400}d" if secs % 86400 == 0 else f"{secs // 3600}h"

    def judgment_of(self, content: str | None) -> str | None:
        """Judgment of a reaction (`+1` -> `useful`), None when the vocabulary has no say."""
        for judgment, contents in self.judgment:
            if content in contents:
                return judgment
        return None

    def enabled(self) -> tuple[str, ...]:
        """Enabled sections, in menu order, unknown ids skipped (defence in depth)."""
        return tuple(s for s in self.sections if s in STATS_SECTIONS)


def active_settings(warn=None) -> tuple[StatsSettings, str]:
    """(settings, language) of the active project (`.env`, PROJECT), or the defaults.

    The dashboard must build on a machine that only has the ledger (CI runner without
    PROJECT or token): an unresolvable environment falls back to the default page in
    `REVIEWME_LANGUAGE` instead of failing. An INVALID project is another matter: PROJECT
    names a folder whose `[stats]` (eras, `bot_since`, sections) the page should follow,
    so `warn` (stderr by default) says which error, and the project's own `[stats]` are
    still used when only its reviewers are at fault (`load_project_settings`); the
    defaults only when `project.toml` itself is broken.
    """
    import os
    import sys

    from ..config import load_config
    from ..projects import ProjectConfigError, load_project_settings, projects_dir, resolve_project

    def default() -> tuple[StatsSettings, str]:
        language = (os.environ.get("REVIEWME_LANGUAGE") or "en").strip().lower()
        return StatsSettings.default(), language

    if warn is None:
        def warn(text: str) -> None:
            print(text, file=sys.stderr)
    try:
        config = load_config(require_repo=False)
    except (RuntimeError, ValueError, OSError):
        return default()
    try:
        project = resolve_project(config)
    except ProjectConfigError as e:
        try:
            project = load_project_settings(config.project, projects_dir(config))
        except (ProjectConfigError, ValueError, OSError):
            warn(f"Warning: invalid project config ({e}); the dashboard uses the default "
                 f"settings, [stats] ignored.")
            return default()
        warn(f"Warning: invalid project config ({e}); the dashboard still uses its "
             f"[stats], [ledger] and [review] settings.")
    except (RuntimeError, ValueError, OSError):
        return default()
    return StatsSettings.from_project(project), project.language
