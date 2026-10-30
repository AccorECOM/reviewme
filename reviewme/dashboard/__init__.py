"""Statistics dashboard: one HTML page built from the ledger, one menu entry per section.

    html, results = build(ledger_root, StatsSettings.default(), "en")

`results` holds each section's `compute` output (JSON-serialisable): the same numbers as
the page, for `/api/dashboard` or a test. Sections and options come from `[stats]` in
`project.toml` (see `settings.py`).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .settings import StatsSettings

__all__ = ["StatsSettings", "build", "write"]


def build(ledger_root: Path, settings: StatsSettings | None = None, language: str = "en",
          stats_file: Path | None = None, *, now: datetime | None = None) -> tuple[str, dict]:
    """Build the page. Returns `(html, {section_id: result})`."""
    from .data import load_context
    from .render import page, run_sections
    from .sections import MODULES

    settings = settings or StatsSettings.default()
    ctx = load_context(Path(ledger_root), settings, language, stats_file=stats_file, now=now)
    modules = [MODULES[sid] for sid in settings.enabled() if sid in MODULES]
    sections = run_sections(ctx, modules)
    return page(ctx, sections), {sid: v["result"] for sid, v in sections.items()}


def write(dest: Path, ledger_root: Path, settings: StatsSettings | None = None,
          language: str = "en", stats_file: Path | None = None, *,
          now: datetime | None = None) -> Path:
    html, _ = build(ledger_root, settings, language, stats_file, now=now)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(html, encoding="utf-8")
    return dest
