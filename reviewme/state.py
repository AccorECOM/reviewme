"""Local state cache (last_reviewed_sha per PR), protected by an inter-process lock.

Read-modify-write happens under the lock: without it, several threads wrote in parallel
-> lost updates. Here every mutation goes through `_locked()`, which takes an exclusive
`flock` on data/state.lock and runs load->modify->save in a critical section.

Entries are keyed by `owner/repo#N` so one install can watch several repos without the
PR #12 of one overwriting the PR #12 of the other. Entries written before that (key `N`)
are still read, and replaced on the next write.

NB: this cache stays local to one machine. The dedup TRUTH is GitHub (existing comments
carrying the fingerprint marker, see reconciler.py). With multiple instances, this cache
is only an optimization; a transient double-post is possible and cleaned up on the next
run.
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


def _key(pr_number: int, repo: str) -> str:
    return f"{repo}#{pr_number}" if repo else str(pr_number)


def _entry(section: dict, pr_number: int, repo: str) -> dict | None:
    """The PR's entry, falling back to the legacy key `N`. A legacy entry can belong to
    another repo, but it only matches if the head SHA matches too: a collision would need
    two repos sharing a commit under the same PR number."""
    return section.get(_key(pr_number, repo)) or section.get(str(pr_number))


def _drop_legacy(section: dict, pr_number: int, repo: str) -> None:
    if repo:
        section.pop(str(pr_number), None)


def is_reviewed(pr_number: int, head_sha: str, repo: str = "") -> bool:
    """True if THIS commit of THIS PR has already been reviewed successfully."""
    entry = _entry(_load_raw().get("reviewed", {}), pr_number, repo)
    return bool(entry) and entry.get("head_sha") == head_sha


def mark_reviewed(pr_number: int, head_sha: str, title: str, status: str = "success",
                  repo: str = "") -> None:
    with _locked():
        data = _load_raw()
        reviewed = data.setdefault("reviewed", {})
        _drop_legacy(reviewed, pr_number, repo)
        reviewed[_key(pr_number, repo)] = {
            "head_sha": head_sha,
            "title": title,
            "status": status,
            "reviewed_at": datetime.now(UTC).isoformat(),
        }
        errors = data.setdefault("errors", {})
        errors.pop(_key(pr_number, repo), None)
        errors.pop(str(pr_number), None)
        _save_raw(data)


def mark_error(pr_number: int, head_sha: str, title: str, error: str, repo: str = "") -> None:
    with _locked():
        data = _load_raw()
        errors = data.setdefault("errors", {})
        _drop_legacy(errors, pr_number, repo)
        errors[_key(pr_number, repo)] = {
            "head_sha": head_sha,
            "title": title,
            "error": error,
            "failed_at": datetime.now(UTC).isoformat(),
        }
        _save_raw(data)


def failed_at(pr_number: int, head_sha: str, repo: str = "") -> bool:
    """True if THIS commit of THIS PR failed on the last attempt.

    Lets poll mode NOT re-review indefinitely (and re-bill) a commit that fails
    permanently (diff too large, budget exceeded, agent crash). A NEW commit (different
    sha) is not filtered → it will be retried."""
    entry = _entry(_load_raw().get("errors", {}), pr_number, repo)
    return bool(entry) and entry.get("head_sha") == head_sha


def get_all_state() -> dict[str, Any]:
    return _load_raw()
