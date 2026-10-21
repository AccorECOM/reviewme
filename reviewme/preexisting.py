"""Pre-existing code: a line "added" by the PR that already existed elsewhere before it.

A moved file, or a block copied or extracted into a new function, shows up green in the
diff. The reviewer finds defects in it that are not the PR's ("constructor too long",
"unprotected static variable") and the author receives them as unfair criticism.
Dropping them would lose a real problem; so we TAG them.

`git blame -C -C -C` follows lines across moves and copies, across all files and all
commits. A line whose originating commit is not a PR commit is pre-existing. Only applies
to lines that have a finding: -C -C -C is expensive, and we only need to know for them.

Best-effort: shallow clone, missing base, git failure -> nothing is tagged.
"""
from __future__ import annotations

import logging
import re
import subprocess

_TIMEOUT_S = 60


def _git(repo_path: str, *args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=repo_path, capture_output=True, text=True,
                             timeout=_TIMEOUT_S, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


def pr_commits(repo_path: str, base_ref: str) -> set[str] | None:
    """The PR's commits (`origin/<base>..HEAD`), or None if the base cannot be found."""
    out = _git(repo_path, "rev-list", f"origin/{base_ref}..HEAD")
    return None if out is None else {sha.strip() for sha in out.splitlines() if sha.strip()}


_HEADER = re.compile(r"^([0-9a-f]{40}) \d+ (\d+)")
DEADLINE_S = 120


def preexisting_lines(repo_path: str, path: str, lines: set[int],
                      commits: set[str]) -> set[int]:
    """Among `lines` (HEAD side), those whose origin predates the PR. A single `blame` per
    file, with one `-L` per line."""
    args = ["blame", "-C", "-C", "-C", "-w", "--porcelain"]
    for line in sorted(lines):
        args += ["-L", f"{line},{line}"]
    out = _git(repo_path, *args, "HEAD", "--", path)
    if not out:
        return set()
    found: set[int] = set()
    for raw in out.splitlines():
        m = _HEADER.match(raw)
        if not m:
            continue
        sha, final = m.group(1), int(m.group(2))
        if final in lines and set(sha) != {"0"} and sha not in commits:
            found.add(final)
    return found


def removed_lines(diff: str) -> set[str]:
    """Normalized contents of the lines REMOVED by the PR."""
    from .diff_utils import normalize_line
    return {normalize_line(b[1:]) for b in diff.splitlines()
            if b.startswith("-") and not b.startswith("---") and normalize_line(b[1:])}


def tag_preexisting(findings: list, repo_path: str, base_ref: str,
                    logger: logging.Logger, anchor_of=None, removed: set[str] | None = None) -> int:
    """Set `preexisting = True` on findings about code MOVED by the PR.

    Two conditions: blame traces the line back to before the PR, AND its content is among
    the lines the PR removes elsewhere. Without the second, a COPIED line (a dangerous
    pattern pasted again) would pass as pre-existing even though the PR introduces it.

    A CONTEXT line of the diff (not added, typically in a renamed file) is pre-existing
    by definition, without blame.

    `anchor_of(finding)` -> (line, content[, added]) re-anchored on the diff, or None.
    """
    import time

    from .diff_utils import normalize_line
    if not findings:
        return 0
    anchor_of = anchor_of or (lambda f: (f.line, f.line_content, True))
    anchors = {id(f): anchor_of(f) for f in findings}
    tagged = 0
    for f in findings:
        a = anchors[id(f)]
        if a and len(a) > 2 and not a[2]:
            f.preexisting = True
            tagged += 1
    if not base_ref or not removed:
        return tagged
    commits = pr_commits(repo_path, base_ref)
    if not commits:
        logger.info("pre-existing: PR commits not found (base origin/%s, shallow "
                    "clone?) — no tag", base_ref)
        return tagged
    by_path: dict[str, set[int]] = {}
    for f in findings:
        a = anchors[id(f)]
        if a and not f.preexisting and normalize_line(a[1]) in removed:
            by_path.setdefault(f.path, set()).add(a[0])
    deadline = time.monotonic() + DEADLINE_S
    old: set[tuple[str, int]] = set()
    for path, ls in by_path.items():
        if time.monotonic() > deadline:
            logger.info("pre-existing: %ds deadline reached, remaining files not examined",
                        DEADLINE_S)
            break
        old |= {(path, n) for n in preexisting_lines(repo_path, path, ls, commits)}
    for f in findings:
        a = anchors[id(f)]
        if a and not f.preexisting and (f.path, a[0]) in old:
            f.preexisting = True
            tagged += 1
    return tagged
