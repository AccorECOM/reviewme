"""Unified patch parser -> valid inline positions (anti-422 defense).

GitHub validates a review batch ATOMICALLY: a single comment on a line outside a hunk
makes the WHOLE review fail (422). So from the per-file patches
(`GET /pulls/{n}/files`) we build the set of lines actually commentable on the RIGHT
side, and filter findings BEFORE posting.

A RIGHT-commentable line = a line present in the new file inside a hunk: added line
(`+`) or context line (` `). We also keep the actual line content, which serves as a
STABLE anchor for the fingerprint.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


@dataclass
class LinePos:
    line: int
    content: str
    added: bool  # True = added line (+), False = context line


def parse_patch(patch: str) -> dict[int, LinePos]:
    """Map new-file line number -> LinePos, for a single file's patch."""
    positions: dict[int, LinePos] = {}
    if not patch:
        return positions
    new_line: int | None = None
    for raw in patch.split("\n"):
        m = _HUNK_RE.match(raw)
        if m:
            new_line = int(m.group(1))
            continue
        if new_line is None:
            continue
        if not raw:
            # empty context line
            positions[new_line] = LinePos(new_line, "", added=False)
            new_line += 1
            continue
        tag, body = raw[0], raw[1:]
        if tag == "+":
            positions[new_line] = LinePos(new_line, body, added=True)
            new_line += 1
        elif tag == "-":
            pass  # LEFT side, does not advance the new-file counter
        elif tag == "\\":
            pass  # "\ No newline at end of file"
        else:  # context (space prefix)
            positions[new_line] = LinePos(new_line, body, added=False)
            new_line += 1
    return positions


def valid_positions(files: list[dict]) -> dict[str, dict[int, LinePos]]:
    """From the `GET /pulls/{n}/files` response, return
    { path: { line_no: LinePos } } of RIGHT-commentable lines."""
    out: dict[str, dict[int, LinePos]] = {}
    for f in files or []:
        path = f.get("filename")
        patch = f.get("patch")  # missing for binary / too-large files
        if not path or not patch:
            continue
        parsed = parse_patch(patch)
        if parsed:
            out[path] = parsed
    return out


def resolve_line(file_map: dict[int, LinePos], claimed_line: int, snippet: str) -> tuple[int, str] | None:
    """Return a commentable (line, content), CORRECTING an imprecise LLM line number.

    LLMs are often off by a few lines (they land on a neighbouring empty line).
    If the finding's `snippet` does not match the content of the claimed line, we search
    the diff for the line whose content matches the snippet (the closest to the claimed
    line). Without a usable snippet, we fall back to the claimed line if it exists.
    Returns None if nothing is commentable (finding outside the diff)."""
    ns = normalize_line(snippet)
    claimed = file_map.get(claimed_line)
    if claimed is not None and ns and ns in normalize_line(claimed.content):
        return claimed_line, claimed.content
    if ns:
        cands = [ln for ln, lp in file_map.items()
                 if normalize_line(lp.content) and (ns == normalize_line(lp.content) or ns in normalize_line(lp.content))]
        if cands:
            best = min(cands, key=lambda ln: abs(ln - claimed_line))
            return best, file_map[best].content
    if claimed is not None:
        return claimed_line, claimed.content
    return None


def normalize_line(content: str) -> str:
    """Normalize a line's content for a stable fingerprint (independent of shifting
    indentation/whitespace). Deliberately aggressive: we want the same key as long as
    the line's logic does not change."""
    return re.sub(r"\s+", " ", content or "").strip()
