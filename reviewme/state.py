"""Local state cache (last_reviewed_sha per PR), protected by an inter-process lock.

Read-modify-write happens under the lock: without it, several threads wrote in parallel
-> lost updates. Here every mutation goes through `_locked()`, which takes an exclusive
`flock` on data/state.lock and runs load->modify->save in a critical section.

NB: this cache stays local to one machine. The dedup TRUTH is GitHub (existing comments
carrying the fingerprint marker, see reconciler.py). With multiple instances, this cache
is only an optimization; a transient double-post is possible and cleaned up on the next
run (accepted, see ADR D8 post-challenge).
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from .config import STATE_FILE, STATE_LOCK

try:
    import fcntl  # POSIX (macOS/Linux — project targets)
    _HAS_FCNTL = True
except ImportError:  # pragma: no cover (Windows)
    _HAS_FCNTL = False


@contextmanager
def _locked():
    """Exclusive inter-process lock around a critical section."""
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
    """True if THIS commit of THIS PR has already been reviewed successfully."""
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
    """True if THIS commit of THIS PR failed on the last attempt.

    Lets poll mode NOT re-review indefinitely (and re-bill) a commit that fails
    permanently (diff too large, budget exceeded, agent crash). A NEW commit (different
    sha) is not filtered → it will be retried."""
    data = _load_raw()
    entry = data.get("errors", {}).get(str(pr_number))
    return bool(entry) and entry.get("head_sha") == head_sha


def get_all_state() -> dict[str, Any]:
    return _load_raw()
