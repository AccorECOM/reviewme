"""Cache d'état local (last_reviewed_sha par PR), protégé par un verrou inter-process.

Le read-modify-write se fait sous verrou : sans lui, plusieurs threads
écrivaient en parallèle -> lost updates. Ici toute mutation passe par `_locked()` qui
prend un `flock` exclusif sur data/state.lock et exécute load->modify->save en section
critique.

NB : ce cache reste local à une machine. La VÉRITÉ de dédup est GitHub (les commentaires
existants portant le marqueur fingerprint, cf. reconciler.py). En multi-instance, ce
cache n'est qu'une optimisation ; le double-post transitoire est possible et nettoyé au
run suivant (assumé, cf. ADR D8 post-challenge).
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from .config import STATE_FILE, STATE_LOCK

try:
    import fcntl  # POSIX (macOS/Linux — cibles du projet)
    _HAS_FCNTL = True
except ImportError:  # pragma: no cover (Windows)
    _HAS_FCNTL = False


@contextmanager
def _locked():
    """Verrou exclusif inter-process autour d'une section critique."""
    STATE_LOCK.parent.mkdir(parents=True, exist_ok=True)
    lock_fh = open(STATE_LOCK, "w")
    try:
        if _HAS_FCNTL:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if _HAS_FCNTL:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
        lock_fh.close()


def _load_raw() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {"reviewed": {}, "errors": {}}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"reviewed": {}, "errors": {}}


def _save_raw(data: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def is_reviewed(pr_number: int, head_sha: str) -> bool:
    """True si CE commit de CETTE PR a déjà été reviewé avec succès."""
    data = _load_raw()
    entry = data.get("reviewed", {}).get(str(pr_number))
    return bool(entry) and entry.get("head_sha") == head_sha


def mark_reviewed(pr_number: int, head_sha: str, title: str, status: str = "success") -> None:
    with _locked():
        data = _load_raw()
        data.setdefault("reviewed", {})[str(pr_number)] = {
            "head_sha": head_sha,
            "title": title,
            "status": status,
            "reviewed_at": datetime.now(UTC).isoformat(),
        }
        data.setdefault("errors", {}).pop(str(pr_number), None)
        _save_raw(data)


def mark_error(pr_number: int, head_sha: str, title: str, error: str) -> None:
    with _locked():
        data = _load_raw()
        data.setdefault("errors", {})[str(pr_number)] = {
            "head_sha": head_sha,
            "title": title,
            "error": error,
            "failed_at": datetime.now(UTC).isoformat(),
        }
        _save_raw(data)


def failed_at(pr_number: int, head_sha: str) -> bool:
    """True si CE commit de CETTE PR a échoué à la dernière tentative.

    Sert au mode poll à ne PAS re-reviewer indéfiniment (et re-facturer) un commit qui
    échoue de façon permanente (diff trop gros, budget dépassé, crash agent). Un NOUVEAU
    commit (sha différent) n'est pas filtré → il sera bien retenté."""
    data = _load_raw()
    entry = data.get("errors", {}).get(str(pr_number))
    return bool(entry) and entry.get("head_sha") == head_sha


def get_all_state() -> dict[str, Any]:
    return _load_raw()
