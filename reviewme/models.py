"""Modèles de données + parsing robuste de la sortie de l'agent.

Le moteur (`reviewer.py`) demande à `claude -p` un JSON structuré. On ne fait JAMAIS
confiance à sa forme : `parse_review_output` extrait le JSON même noyé dans du texte /
des fences ```json, et renvoie None si c'est irrécupérable -> l'appelant retombe alors
sur un unique commentaire global, plutôt que de crasher.
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
        # heuristiques tolérantes
        if v in {"CRITICAL", "BLOCK", "BLOCKING", "HIGH"}:
            return cls.BLOCKER
        if v in {"WARNING", "WARN", "MEDIUM", "MAJOR"}:
            return cls.IMPORTANT
        return cls.MINOR


@dataclass
class Finding:
    """Un finding de review. Les champs `line`/`path` sont côté RIGHT (nouveau fichier).

    `snippet` et `rule_id` sont de l'AFFICHAGE uniquement — jamais utilisés pour le
    fingerprint : narrés par le LLM, donc instables.
    Le fingerprint s'ancre sur le contenu réel de la ligne du diff (`line_content`).
    """
    path: str
    line: int
    severity: Severity
    message: str
    snippet: str = ""              # affichage seulement
    rule_id: str = ""             # métadonnée d'affichage seulement
    confidence: int = 0
    suggestion: str | None = None
    side: str = "RIGHT"           # v1 : RIGHT uniquement
    # calculés au runtime :
    valid: bool = False            # renseigné par la validation contre le diff
    line_content: str = ""        # contenu réel de la ligne (source du fingerprint)

    def title(self) -> str:
        base = self.message.strip().splitlines()[0] if self.message.strip() else "(sans titre)"
        return base[:120]


@dataclass
class ReviewResult:
    status: str                    # v1 : toujours "COMMENT"
    summary: str
    findings: list[Finding] = field(default_factory=list)
    raw: str = ""                 # sortie brute (pour le fallback)
    metadata: dict = field(default_factory=dict)
    parsed_ok: bool = True         # False -> l'appelant doit fallback en commentaire global


def _extract_json_blob(text: str) -> str | None:
    """Extrait le premier objet JSON plausible d'un texte (fences, prose autour, etc.)."""
    if not text:
        return None
    # 1) fence ```json ... ```
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        return fence.group(1)
    # 2) du premier { au dernier } équilibré (heuristique simple mais efficace)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return None


def parse_review_output(text: str, metadata: dict | None = None) -> ReviewResult:
    """Parse la sortie de l'agent en ReviewResult. Ne lève jamais : renvoie parsed_ok=False
    (avec raw rempli) si le JSON est absent/invalide, pour permettre le fallback."""
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
    for item in data.get("findings", []) or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path", "")).strip()
        line_raw = item.get("line")
        try:
            line = int(line_raw)
        except (TypeError, ValueError):
            continue  # un finding sans ligne exploitable ne peut pas être inline
        if not path or line <= 0:
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
            )
        )

    return ReviewResult(
        status="COMMENT",  # v1 : le bot ne poste jamais APPROVE/REQUEST_CHANGES (évite le 422 self-review)
        summary=str(data.get("summary", "")).strip(),
        findings=findings,
        raw=raw,
        metadata=metadata,
        parsed_ok=True,
    )


def _coerce_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
