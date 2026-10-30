"""Ledger of findings and runs: what it takes to JUDGE the bot, not just count it.

`stats.json` says how many findings were posted and what they cost. It does not say which
ones were right, nor what the confidence threshold silenced. To answer "are our settings
good and useful?", we must keep track of EVERY finding, posted or not, with its fate:

- `findings.jsonl`: one line per finding and per run. Without the message text by default
  (`ledger.include_text`), so portable via a CI cache like `stats.json`;
- `runs.jsonl`: one line per reviewer and per run (actual model, turns, cost, cache,
  context actually injected);
- `config-snapshot/<hash>/`: the state of the settings at run time, to attribute a change
  in behavior to a configuration change.

Append-only, and ONE FILE PER RUN (`findings/<timestamp>-<run_id>.jsonl`, written in one
block then renamed): two concurrent runs cannot interleave their lines, and a CI cache
that merges folders only loses whole files, never a truncated line. Readers (`read_all`)
merge the parts. Replaying a build adds a run, it overwrites nothing: analysis dedups by
(`pr`, `head_sha`, `variant`) if needed.

The ledger NEVER influences the review: any write error is logged then ignored.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .config import DATA_DIR, ROOT_DIR, Config
from .reconciler import fingerprint_hash

SCHEMA_VERSION = 1

#: Possible fates of a finding. `dropped_unverified` is reserved for the verification pass.
FATES = ("posted", "dropped_low", "dropped_unverified", "out_of_diff", "capped", "deduped",
         "invalid")

FINDINGS_FILE = "findings.jsonl"
RUNS_FILE = "runs.jsonl"
SNAPSHOT_DIR = "config-snapshot"


def ledger_dir(config: Config | None = None) -> Path:
    """Ledger folder.

    A SIMULATED run (`REVIEWME_FAKE`) writes separately, even when `REVIEWME_LEDGER_DIR` is
    set: its findings are made up, mixing them with real ones would skew every measure —
    but we still want to be able to check the plumbing.
    """
    explicit = os.environ.get("REVIEWME_LEDGER_DIR", "").strip()
    base = Path(explicit).expanduser() if explicit else DATA_DIR / "ledger"
    if config is not None and config.fake:
        return base.with_name(base.name + "-fake")
    return base


def variant() -> str:
    """`live` (the pipeline under evaluation) or `baseline` (the control running on the same PRs)."""
    explicit = os.environ.get("REVIEWME_LEDGER_VARIANT", "").strip()
    if explicit:
        return explicit
    baseline = os.environ.get("REVIEWME_BASELINE", "").strip().lower() in {"1", "true", "yes", "on"}
    return "baseline" if baseline else "live"


def engine_commit() -> str:
    """Commit of the engine that produced the run. `REVIEWME_ENGINE_COMMIT` wins (CI without .git)."""
    explicit = os.environ.get("REVIEWME_ENGINE_COMMIT", "").strip()
    if explicit:
        return explicit
    try:
        out = subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], cwd=ROOT_DIR,
                             capture_output=True, text=True, timeout=5, check=False)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    from . import __version__
    return f"v{__version__}"


#: Documented values of `REVIEWME_TRIGGER`. Free string: a deployment may name its own.
TRIGGERS = ("pr_build", "manual", "schedule", "watch")


def trigger() -> str:
    """What started the run (`REVIEWME_TRIGGER`), `unknown` when nothing says.

    The engine cannot tell a CI build from a hand-run command: the deployment says it.
    Read from the environment once per run, no I/O: it stays off the review's critical
    path. It separates the cost of the PR flow from re-runs and experiments.
    """
    return os.environ.get("REVIEWME_TRIGGER", "").strip() or "unknown"


def include_text(project) -> bool:
    """Write `message` and `suggestion`? Default no: the ledger stays free of PR content.

    `REVIEWME_LEDGER_INCLUDE_TEXT` takes precedence over `[ledger] include_text` in `project.toml`.
    """
    env = os.environ.get("REVIEWME_LEDGER_INCLUDE_TEXT")
    if env is not None:
        return env.strip().lower() in {"1", "true", "yes", "on"}
    return bool(getattr(project, "ledger_include_text", False))


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


# --------------------------------------------------------------------------- settings

def _config_files(project) -> list[tuple[str, Path]]:
    """(relative path, file) of the settings that drive the reviewers' behavior."""
    from .projects import TEMPLATES_DIR
    base = project.directory if project.directory is not None else TEMPLATES_DIR
    if base is None or not Path(base).is_dir():
        return []
    base = Path(base)
    motifs = ("project.toml", "common/*.md", "guidelines/*.md",
              "reviewers/*/reviewer.toml", "reviewers/*/system.md")
    fichiers = {p for m in motifs for p in base.glob(m) if p.is_file()}
    return sorted((p.relative_to(base).as_posix(), p) for p in fichiers)


def config_hash(project) -> str:
    """Fingerprint of the project's settings (personas, guidelines, reviewer.toml)."""
    h = hashlib.sha256()
    for rel, path in _config_files(project):
        h.update(rel.encode())
        h.update(hashlib.sha256(path.read_bytes()).hexdigest().encode())
    return h.hexdigest()[:12]


def snapshot_config(project, root: Path, chash: str) -> None:
    """Keep a copy of the `*.toml` files and the fingerprint of the `*.md` — once per config state.

    Prompts are not copied: their hash is enough to say THAT they changed, and the config
    repo says HOW.
    """
    cible = root / SNAPSHOT_DIR / chash
    if (cible / "manifest.json").exists():
        return
    cible.mkdir(parents=True, exist_ok=True)
    manifest = {"config_hash": chash, "project": project.name,
                "ts": datetime.now(UTC).isoformat(timespec="seconds"), "files": {}}
    for rel, path in _config_files(project):
        contenu = path.read_bytes()
        manifest["files"][rel] = hashlib.sha256(contenu).hexdigest()[:16]
        if path.suffix == ".toml":
            dest = cible / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(contenu)
    (cible / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                                         encoding="utf-8")


# --------------------------------------------------------------------------- rows

def diff_size(diff: str) -> tuple[int, int]:
    """(files, changed lines) of a unified diff."""
    fichiers = lignes = 0
    for brute in diff.splitlines():
        if brute.startswith("+++ "):
            fichiers += 1
        elif brute.startswith(("+", "-")) and not brute.startswith(("+++", "---")):
            lignes += 1
    return fichiers, lignes


def _verification(v: dict | None, with_text: bool) -> dict:
    """The verifier's verdict. Its justification (`reason`) is text about the PR."""
    if not v:
        return {"status": "skipped", "confidence": None}
    return {k: val for k, val in v.items() if with_text or k != "reason"}


def diff_paths(diff: str) -> set[str]:
    """Paths (new-file side) touched by a unified diff."""
    return {brute[6:].strip() for brute in diff.splitlines() if brute.startswith("+++ b/")}


def finding_rows(base: dict, reviewer: str, result, findings: list, *,
                 with_text: bool) -> list[dict]:
    """`findings.jsonl` rows for ONE reviewer: each finding, plus the invalid ones."""
    rows = []
    for f in findings:
        row = {
            **base,
            "reviewer": reviewer,
            "model": result.metadata.get("model", "?"),
            "model_configured": result.metadata.get("model_configured", ""),
            "fingerprint": fingerprint_hash(f.path, f.line_content) if f.line_content else "",
            "path": f.path,
            "line": f.line,
            "severity_raw": f.severity_raw,
            "severity": f.severity.value,
            "confidence": f.confidence,
            "rule_id": f.rule_id,
            "has_evidence": bool(f.evidence),
            "verification": _verification(f.verification, with_text),
            "fate": f.fate or "invalid",
            "comment_id": f.comment_id,
            "in_summary": f.in_summary,
            "preexisting": f.preexisting,
        }
        if with_text:
            row["message"] = f.message
            row["suggestion"] = f.suggestion
        rows.append(row)
    for _ in range(getattr(result, "invalid", 0) or 0):
        rows.append({**base, "reviewer": reviewer, "model": result.metadata.get("model", "?"),
                     "fingerprint": "", "path": "", "line": 0, "severity_raw": "",
                     "severity": "", "confidence": 0, "rule_id": "", "has_evidence": False,
                     "verification": {"status": "skipped", "confidence": None},
                     "fate": "invalid", "comment_id": None, "in_summary": False})
    return rows


def run_row(base: dict, reviewer: str, result, *, threshold: int, diff_files: int,
            diff_lines: int, fates: dict, advice_count: int = 0) -> dict:
    m = result.metadata
    return {
        **base,
        "reviewer": reviewer,
        "model": m.get("model", "?"),
        # The name the config asked for (may be a gateway alias) and the model that did
        # most of the work: `model` lists every model the call used.
        "model_configured": m.get("model_configured", ""),
        "model_reported": m.get("model_reported", ""),
        "parsed_ok": result.parsed_ok,
        "num_turns": m.get("total_turns", 0),
        "max_turns": m.get("max_turns"),
        "subtype": m.get("subtype", ""),
        "is_error": m.get("is_error", False),
        "duration_s": round((m.get("duration_ms", 0) or 0) / 1000, 1),
        "cost_usd": m.get("cost_usd", 0),
        "input_tokens": m.get("input_tokens", 0),
        "output_tokens": m.get("output_tokens", 0),
        "cache_read_tokens": m.get("cache_read_tokens", 0),
        "cache_create_tokens": m.get("cache_create_tokens", 0),
        "diff_files": diff_files,
        "diff_lines": diff_lines,
        "context_files": m.get("context_files", []),
        "verification": m.get("verification"),
        "threshold": threshold,
        "findings": len(result.findings) + (getattr(result, "invalid", 0) or 0),
        "fates": fates,
        # Advice for the human reviewer actually displayed (anchored in the PR).
        "advice": advice_count,
    }


def write_part(root: Path, name: str, rows: list[dict], part_id: str = "") -> Path | None:
    """Write `rows` into a part of `name` (`findings.jsonl` -> `findings/<id>.jsonl`).

    Written to a temporary file then renamed: a part is either complete or absent.
    """
    if not rows:
        return None
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    folder = root / Path(name).stem
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{stamp}-{part_id or uuid.uuid4().hex[:12]}.jsonl"
    tmp = target.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                   encoding="utf-8")
    tmp.replace(target)
    return target


def read_all(root: Path, name: str) -> list[dict]:
    """All rows of `name`: the legacy single file, then the parts in order."""
    rows = read_jsonl(root / name)
    folder = root / Path(name).stem
    if folder.is_dir():
        for part in sorted(folder.glob("*.jsonl")):
            rows.extend(read_jsonl(part))
    return rows


def record(pr: dict, config: Config, project, prepared: list, outcomes: list, diff: str,
           logger: logging.Logger, *, root: Path | None = None,
           status: str = "ok") -> str | None:
    """Write the run to the ledger. Returns the `run_id`, or None if nothing was written.

    `prepared` and `outcomes` are aligned (one `PreparedReview` per executed reviewer).
    `status` = `post_failed` when the GitHub write failed: the run remains measurable.
    """
    try:
        root = root or ledger_dir(config)
        chash = config_hash(project)
        snapshot_config(project, root, chash)
        run_id = new_run_id()
        base = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "repo": config.github_repo,
            "pr": pr["number"],
            "head_sha": pr["head"]["sha"],
            "base_sha": (pr.get("base") or {}).get("sha", ""),
            "variant": variant(),
            "engine_commit": engine_commit(),
            "config_hash": chash,
            "dry_run": config.dry_run,
            "status": status,
            "trigger": trigger(),
        }
        with_text = include_text(project)
        files, lines = diff_size(diff)
        f_rows: list[dict] = []
        r_rows: list[dict] = []
        for prep, (spec, result) in zip(prepared, outcomes, strict=True):
            rows = finding_rows(base, spec.id, result, prep.findings, with_text=with_text)
            fates: dict[str, int] = {}
            for r in rows:
                fates[r["fate"]] = fates.get(r["fate"], 0) + 1
            f_rows.extend(rows)
            r_row = run_row(base, spec.id, result, threshold=config.confidence_threshold,
                            diff_files=files, diff_lines=lines, fates=fates,
                            advice_count=prep.counts.get("advice", 0))
            if with_text and prep.counts.get("advice"):
                r_row["advice_topics"] = [a.topic for a in result.advice if a.paths]
            r_rows.append(r_row)
        write_part(root, FINDINGS_FILE, f_rows, run_id)
        write_part(root, RUNS_FILE, r_rows, run_id)
        logger.info("PR #%s: ledger %s — %d finding(s), %d reviewer run(s) (%s)",
                    pr["number"], run_id, len(f_rows), len(r_rows), root)
        return run_id
    except Exception as e:
        logger.warning("PR #%s: ledger not written (%s: %s) — no effect on the review",
                       pr.get("number"), type(e).__name__, e)
        return None


def read_jsonl(path: Path) -> list[dict]:
    """Read a JSONL, ignoring corrupted lines (write interrupted in CI)."""
    if not path.is_file():
        return []
    rows, rejected = [], 0
    for brute in path.read_text(encoding="utf-8").splitlines():
        brute = brute.strip()
        if not brute:
            continue
        try:
            rows.append(json.loads(brute))
        except ValueError:
            rejected += 1
    if rejected:
        logging.getLogger("reviewme").warning("ledger: %d unreadable line(s) ignored "
                                              "in %s", rejected, path)
    return rows
