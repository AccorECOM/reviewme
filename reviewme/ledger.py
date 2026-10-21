"""Registre des findings et des runs : de quoi JUGER le bot, pas seulement le compter.

`stats.json` dit combien de remarques ont été postées et ce qu'elles ont coûté. Il ne dit
pas lesquelles étaient justes, ni ce que le seuil de confiance a fait taire. Pour répondre
à « nos réglages sont-ils bons et utiles ? », il faut garder la trace de CHAQUE finding,
posté ou non, avec son sort :

- `findings.jsonl` : une ligne par finding et par run. Sans le texte du message par défaut
  (`ledger.include_text`), donc transportable par un cache de CI comme `stats.json` ;
- `runs.jsonl` : une ligne par reviewer et par run (modèle réel, tours, coût, cache,
  contexte réellement injecté) ;
- `config-snapshot/<hash>/` : l'état des réglages au moment du run, pour attribuer un
  changement de comportement à un changement de configuration.

Append-only, et UN FICHIER PAR RUN (`findings/<horodatage>-<run_id>.jsonl`, écrit d'un
bloc puis renommé) : deux runs concurrents ne peuvent pas entrelacer leurs lignes, et un
cache de CI qui fusionne des dossiers ne perd que des fichiers entiers, jamais une ligne
coupée. Les lecteurs (`read_all`) fusionnent les parts. Rejouer un build ajoute un run, il
n'écrase rien : l'analyse dédoublonne par (`pr`, `head_sha`, `variant`) si besoin.

Le registre n'influence JAMAIS la review : toute erreur d'écriture est journalisée puis
ignorée.
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

#: Sorts possibles d'un finding. `dropped_unverified` est réservé à la passe de vérification.
FATES = ("posted", "dropped_low", "dropped_unverified", "out_of_diff", "capped", "deduped",
         "invalid")

FINDINGS_FILE = "findings.jsonl"
RUNS_FILE = "runs.jsonl"
SNAPSHOT_DIR = "config-snapshot"


def ledger_dir(config: Config | None = None) -> Path:
    """Dossier du registre.

    Un run SIMULÉ (`REVIEWME_FAKE`) écrit à part, même quand `REVIEWME_LEDGER_DIR` est
    posé : ses findings sont fabriqués, les mêler aux vrais fausserait toutes les mesures —
    mais on veut pouvoir vérifier la tuyauterie.
    """
    explicit = os.environ.get("REVIEWME_LEDGER_DIR", "").strip()
    base = Path(explicit).expanduser() if explicit else DATA_DIR / "ledger"
    if config is not None and config.fake:
        return base.with_name(base.name + "-simule")
    return base


def variant() -> str:
    """`live` (la chaîne évaluée) ou `baseline` (le témoin qui tourne sur les mêmes PR)."""
    return os.environ.get("REVIEWME_LEDGER_VARIANT", "").strip() or "live"


def engine_commit() -> str:
    """Commit du moteur qui a produit le run. `REVIEWME_ENGINE_COMMIT` prime (CI sans .git)."""
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


def include_text(project) -> bool:
    """Écrire `message` et `suggestion` ? Défaut non : le registre reste sans contenu de PR.

    `REVIEWME_LEDGER_INCLUDE_TEXT` prime sur `[ledger] include_text` du `project.toml`.
    """
    env = os.environ.get("REVIEWME_LEDGER_INCLUDE_TEXT")
    if env is not None:
        return env.strip().lower() in {"1", "true", "yes", "on"}
    return bool(getattr(project, "ledger_include_text", False))


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


# --------------------------------------------------------------------------- réglages

def _config_files(project) -> list[tuple[str, Path]]:
    """(chemin relatif, fichier) des réglages qui pilotent le comportement des reviewers."""
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
    """Empreinte des réglages du projet (personas, consignes, reviewer.toml)."""
    h = hashlib.sha256()
    for rel, path in _config_files(project):
        h.update(rel.encode())
        h.update(hashlib.sha256(path.read_bytes()).hexdigest().encode())
    return h.hexdigest()[:12]


def snapshot_config(project, root: Path, chash: str) -> None:
    """Garde une copie des `*.toml` et l'empreinte des `*.md` — une fois par état de config.

    Les prompts ne sont pas recopiés : leur hash suffit à dire QU'ils ont changé, et le
    dépôt de config dit COMMENT.
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


# --------------------------------------------------------------------------- lignes

def diff_size(diff: str) -> tuple[int, int]:
    """(fichiers, lignes modifiées) d'un diff unifié."""
    fichiers = lignes = 0
    for brute in diff.splitlines():
        if brute.startswith("+++ "):
            fichiers += 1
        elif brute.startswith(("+", "-")) and not brute.startswith(("+++", "---")):
            lignes += 1
    return fichiers, lignes


def finding_rows(base: dict, reviewer: str, result, findings: list, *,
                 with_text: bool) -> list[dict]:
    """Lignes de `findings.jsonl` pour UN reviewer : chaque finding, plus les invalides."""
    rows = []
    for f in findings:
        row = {
            **base,
            "reviewer": reviewer,
            "model": result.metadata.get("model", "?"),
            "fingerprint": fingerprint_hash(f.path, f.line_content) if f.line_content else "",
            "path": f.path,
            "line": f.line,
            "severity_raw": f.severity_raw,
            "severity": f.severity.value,
            "confidence": f.confidence,
            "rule_id": f.rule_id,
            "has_evidence": bool(f.evidence),
            "verification": {"status": "skipped", "confidence": None},
            "fate": f.fate or "invalid",
            "comment_id": f.comment_id,
            "in_summary": f.in_summary,
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
            diff_lines: int, fates: dict) -> dict:
    m = result.metadata
    return {
        **base,
        "reviewer": reviewer,
        "model": m.get("model", "?"),
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
    }


def write_part(root: Path, name: str, rows: list[dict], part_id: str = "") -> Path | None:
    """Écrit `rows` dans une part de `name` (`findings.jsonl` -> `findings/<id>.jsonl`).

    Écriture dans un fichier temporaire puis renommage : une part est complète ou absente.
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
    """Toutes les lignes de `name` : fichier unique historique, puis parts dans l'ordre."""
    rows = read_jsonl(root / name)
    folder = root / Path(name).stem
    if folder.is_dir():
        for part in sorted(folder.glob("*.jsonl")):
            rows.extend(read_jsonl(part))
    return rows


def record(pr: dict, config: Config, project, prepared: list, outcomes: list, diff: str,
           logger: logging.Logger, *, root: Path | None = None,
           status: str = "ok") -> str | None:
    """Écrit le run dans le registre. Renvoie le `run_id`, ou None si rien n'a été écrit.

    `prepared` et `outcomes` sont alignés (un `PreparedReview` par reviewer exécuté).
    `status` = `post_failed` quand l'écriture GitHub a échoué : le run reste mesurable.
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
            r_rows.append(run_row(base, spec.id, result, threshold=config.confidence_threshold,
                                  diff_files=files, diff_lines=lines, fates=fates))
        write_part(root, FINDINGS_FILE, f_rows, run_id)
        write_part(root, RUNS_FILE, r_rows, run_id)
        logger.info("PR #%s : registre %s — %d finding(s), %d run(s) reviewer (%s)",
                    pr["number"], run_id, len(f_rows), len(r_rows), root)
        return run_id
    except Exception as e:
        logger.warning("PR #%s : registre non écrit (%s: %s) — sans effet sur la review",
                       pr.get("number"), type(e).__name__, e)
        return None


def read_jsonl(path: Path) -> list[dict]:
    """Lit un JSONL en ignorant les lignes corrompues (écriture interrompue en CI)."""
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
        logging.getLogger("reviewme").warning("registre : %d ligne(s) illisible(s) ignorée(s) "
                                              "dans %s", rejected, path)
    return rows
