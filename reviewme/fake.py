"""Simulated mode: run the whole pipeline WITHOUT calling the LLM.

A review costs a few dollars and two minutes. When tuning the CI, reviewer selection,
prechecks or posting, you rerun dozens of times and pay again on each attempt for a
model whose answer you do not use.

`REVIEWME_FAKE=1` replaces only the model call. EVERYTHING else runs for real:
selection, prechecks, reading conventions, validation against the diff, fingerprints,
dedup, confidence threshold, cap, GitHub writes. So it is a test of the plumbing, not
an end-to-end simulation.

Findings are anchored on REAL added lines of the diff: otherwise they would all be
rejected by the anti-422 validation and nothing would be tested.

Usually combine with `DRY_RUN=1` — otherwise findings tagged [SIMULATED] land on a
real PR.
"""
from __future__ import annotations

import re
from collections.abc import Iterator

from .models import Advice, Finding, ReviewResult, Severity

_ENTETE_FICHIER = re.compile(r"^\+\+\+ b/(.+)$")
_ENTETE_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")

# Three levels around the default threshold (70): we want to see filtering at work,
# not just the nominal path.
#
# Severity is the ENUM, not a free string: `_SEV_LABEL` and `_SEV_RANK` are keyed by
# Severity members. A raw string would silently fall back to [MINOR] for all three, and
# the severity sort — which drives capping — would test nothing.
_GABARITS = (
    (95, Severity.BLOCKER, "Simulated finding, high confidence: must be posted."),
    (75, Severity.IMPORTANT, "Simulated finding just above the threshold: must be posted."),
    (40, Severity.MINOR, "Simulated finding below the threshold: must be dropped (`dropped_low`)."),
)


def _lignes_ajoutees(diff: str) -> Iterator[tuple[str, int, str]]:
    """(path, RIGHT-side line number, content) for each added line of the diff."""
    chemin, ligne = "", 0
    for brute in diff.splitlines():
        entete = _ENTETE_FICHIER.match(brute)
        if entete:
            chemin, ligne = entete.group(1), 0
            continue
        hunk = _ENTETE_HUNK.match(brute)
        if hunk:
            ligne = int(hunk.group(1))
            continue
        if not chemin or not ligne:
            continue
        if brute.startswith("+"):
            yield chemin, ligne, brute[1:]
            ligne += 1
        elif brute.startswith("-"):
            continue          # LEFT side: does not consume a RIGHT-side line number
        else:
            ligne += 1


def review_simulee(reviewer_id: str, diff: str) -> ReviewResult:
    """Deterministic ReviewResult, without any model call."""
    candidates = [c for c in _lignes_ajoutees(diff) if c[2].strip()]

    # A different anchor per reviewer: otherwise all four produce the same fingerprint and
    # dedup hides the fan-out we are precisely trying to observe.
    decalage = sum(ord(c) for c in reviewer_id) % max(len(candidates), 1)

    findings = []
    for i, (confiance, severite, texte) in enumerate(_GABARITS):
        if not candidates:
            break
        chemin, ligne, contenu = candidates[(decalage + i * 7) % len(candidates)]
        findings.append(Finding(
            path=chemin, line=ligne, severity=severite,
            message=f"[SIMULATED · {reviewer_id}] {texte}",
            snippet=contenu.strip()[:80], rule_id=f"fake.{reviewer_id}.{i}",
            confidence=confiance,
        ))

    return ReviewResult(
        status="COMMENT",
        summary=(f"[SIMULATED · {reviewer_id}] No model was called "
                 f"(`REVIEWME_FAKE`). {len(findings)} finding(s) made up from "
                 f"{len(candidates)} added line(s) of the diff."),
        findings=findings,
        # Advice anchored on a real file of the diff: the "review advice" section must
        # appear in the simulated summary.
        advice=[Advice(topic=f"[SIMULATED · {reviewer_id}] Point for a human to check",
                       why="Made-up advice to test the display.",
                       paths=[candidates[0][0]])] if candidates else [],
        raw="",
        metadata={"model": "simulated", "cost_usd": 0.0, "duration_ms": 0,
                  "input_tokens": 0, "output_tokens": 0, "fake": True},
        parsed_ok=True,
    )
