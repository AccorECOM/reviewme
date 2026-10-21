"""Data models + robust parsing of the agent's output.

The engine (`reviewer.py`) asks `claude -p` for structured JSON. We NEVER trust its
shape: `parse_review_output` extracts the JSON even when buried in text / ```json
fences, and returns None if it is unrecoverable -> the caller then falls back to a
single global comment, rather than crashing.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum


class Severity(str, Enum):
    BLOCKER = "BLOCKER"
    IMPORTANT = "IMPORTANT"
    MINOR = "MINOR"

    @classmethod
    def coerce(cls, value: str) -> Severity:
        v = (value or "").strip().upper()
        for s in cls:
            if s.value == v:
                return s
        # lenient heuristics. An unknown or "medium" severity goes down, never up: a WARNING
        # promoted to IMPORTANT ended up inline without deserving it.
        if v in {"CRITICAL", "BLOCK", "BLOCKING"}:
            return cls.BLOCKER
        if v in {"HIGH", "MAJOR"}:
            return cls.IMPORTANT
        return cls.MINOR


@dataclass
class Finding:
    """A review finding. The `line`/`path` fields are on the RIGHT side (new file).

    `snippet` and `rule_id` are DISPLAY only — never used for the fingerprint: narrated by
    the LLM, hence unstable.
    The fingerprint is anchored on the actual content of the diff line (`line_content`).
    """
    path: str
    line: int
    severity: Severity
    message: str
    snippet: str = ""              # display only
    rule_id: str = ""             # display metadata only
    confidence: int = 0
    suggestion: str | None = None
    side: str = "RIGHT"           # v1: RIGHT only
    severity_raw: str = ""        # severity as written by the model, before `coerce`
    evidence: str = ""            # evidence cited by the model (optional)
    # computed at runtime:
    valid: bool = False            # set by validation against the diff
    line_content: str = ""        # actual line content (fingerprint source)
    # the finding's fate, for the ledger (`ledger.py`) — never for posting:
    fate: str = ""                # posted | dropped_low | out_of_diff | capped | deduped
    in_summary: bool = False       # delivered in the summary rather than inline
    comment_id: int | None = None  # GitHub comment carrying it, once posted
    verification: dict | None = None  # verdict of the verification pass (`verify.py`)
    preexisting: bool = False      # the line existed before the PR (moved, copied)

    def title(self) -> str:
        base = self.message.strip().splitlines()[0] if self.message.strip() else "(untitled)"
        return base[:120]


@dataclass
class Advice:
    """Point of attention for the HUMAN reviewer: not a defect, something to validate."""
    topic: str
    why: str
    paths: list[str] = field(default_factory=list)


#: Beyond this, it is no longer targeted advice but a generic checklist.
MAX_ADVICE = 3


@dataclass
class ReviewResult:
    status: str                    # v1: always "COMMENT"
    summary: str
    findings: list[Finding] = field(default_factory=list)
    raw: str = ""                 # raw output (for the fallback)
    metadata: dict = field(default_factory=dict)
    parsed_ok: bool = True         # False -> the caller must fall back to a global comment
    invalid: int = 0               # findings rejected at parsing (no usable `path`/`line`)
    advice: list[Advice] = field(default_factory=list)  # advice for the human reviewer


def _extract_json_blob(text: str) -> str | None:
    """Extract the first plausible JSON object from a text (fences, surrounding prose, etc.)."""
    if not text:
        return None
    # 1) fence ```json ... ```
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        return fence.group(1)
    # 2) from the first { to the last balanced } (simple but effective heuristic)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return None


def parse_review_output(text: str, metadata: dict | None = None) -> ReviewResult:
    """Parse the agent's output into a ReviewResult. Never raises: returns parsed_ok=False
    (with raw filled in) if the JSON is missing/invalid, to allow the fallback."""
    metadata = metadata or {}
    raw = (text or "").strip()
    blob = _extract_json_blob(raw)
    if not blob:
        return ReviewResult(status="COMMENT", summary="", findings=[], raw=raw,
                            metadata=metadata, parsed_ok=False)
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return ReviewResult(status="COMMENT", summary="", findings=[], raw=raw,
                            metadata=metadata, parsed_ok=False)

    findings: list[Finding] = []
    invalid = 0
    for item in data.get("findings", []) or []:
        if not isinstance(item, dict):
            invalid += 1
            continue
        path = str(item.get("path", "")).strip()
        line_raw = item.get("line")
        try:
            line = int(line_raw)
        except (TypeError, ValueError):
            invalid += 1
            continue  # a finding without a usable line cannot be inline
        if not path or line <= 0:
            invalid += 1
            continue
        findings.append(
            Finding(
                path=path,
                line=line,
                severity=Severity.coerce(str(item.get("severity", "MINOR"))),
                message=str(item.get("message", "")).strip(),
                snippet=str(item.get("snippet", "")).strip(),
                rule_id=str(item.get("rule_id", "")).strip(),
                confidence=_coerce_int(item.get("confidence"), default=0),
                suggestion=(str(item["suggestion"]).strip() if item.get("suggestion") else None),
                severity_raw=str(item.get("severity", "")).strip(),
                evidence=str(item.get("evidence", "") or "").strip(),
            )
        )

    return ReviewResult(
        status="COMMENT",  # v1: the bot never posts APPROVE/REQUEST_CHANGES (avoids the self-review 422)
        summary=str(data.get("summary", "")).strip(),
        advice=_parse_advice(data.get("advice")),
        findings=findings,
        raw=raw,
        metadata=metadata,
        parsed_ok=True,
        invalid=invalid,
    )


def _parse_advice(raw) -> list[Advice]:
    """Readable advice only: non-empty topic and reason, three at most."""
    out: list[Advice] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        topic = str(item.get("topic", "") or "").strip()
        why = str(item.get("why", "") or "").strip()
        paths = item.get("paths") or []
        if isinstance(paths, str):
            paths = [paths]
        if topic and why:
            out.append(Advice(topic=topic[:200], why=why[:600],
                              paths=[str(p).strip() for p in paths if str(p).strip()][:5]))
    return out[:MAX_ADVICE]


def _coerce_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
