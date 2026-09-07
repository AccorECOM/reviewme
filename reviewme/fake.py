"""Mode simulé : parcourir toute la chaîne SANS appeler le LLM.

Une review coûte quelques dollars et deux minutes. Quand on met au point la CI, la
sélection des reviewers, les prechecks ou le posting, on relance des dizaines de fois
et on repaie à chaque essai un modèle dont on n'utilise pas la réponse.

`REVIEWME_FAKE=1` remplace le seul appel au modèle. TOUT le reste tourne pour de vrai :
sélection, prechecks, lecture des conventions, validation contre le diff, empreintes,
dédup, seuil de confiance, plafond, écriture GitHub. C'est donc un test de la
tuyauterie, pas une simulation de bout en bout.

Les findings sont ancrés sur de VRAIES lignes ajoutées du diff : sans ça, ils seraient
tous rejetés par la validation anti-422 et on ne testerait rien.

À combiner avec `DRY_RUN=1` en général — sinon des remarques marquées [SIMULÉ]
atterrissent sur une vraie PR.
"""
from __future__ import annotations

import re
from typing import Iterator

from .models import Finding, ReviewResult

_ENTETE_FICHIER = re.compile(r"^\+\+\+ b/(.+)$")
_ENTETE_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")

# Trois niveaux qui encadrent le seuil par défaut (70) : on veut voir le filtrage
# à l'œuvre, pas seulement le chemin nominal.
_GABARITS = (
    (95, "error", "Finding simulé à confiance haute : doit être posté."),
    (75, "warning", "Finding simulé juste au-dessus du seuil : doit être posté."),
    (40, "info", "Finding simulé sous le seuil : doit être écarté (`dropped_low`)."),
)


def _lignes_ajoutees(diff: str) -> Iterator[tuple[str, int, str]]:
    """(chemin, numéro de ligne côté RIGHT, contenu) pour chaque ligne ajoutée du diff."""
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
            continue          # côté LEFT : ne consomme pas de numéro côté RIGHT
        else:
            ligne += 1


def review_simulee(reviewer_id: str, diff: str) -> ReviewResult:
    """ReviewResult déterministe, sans aucun appel au modèle."""
    candidates = [c for c in _lignes_ajoutees(diff) if c[2].strip()]

    # Un point d'ancrage différent par reviewer : sinon les quatre produisent le même
    # fingerprint et le dédup masque le fan-out qu'on cherche justement à observer.
    decalage = sum(ord(c) for c in reviewer_id) % max(len(candidates), 1)

    findings = []
    for i, (confiance, severite, texte) in enumerate(_GABARITS):
        if not candidates:
            break
        chemin, ligne, contenu = candidates[(decalage + i * 7) % len(candidates)]
        findings.append(Finding(
            path=chemin, line=ligne, severity=severite,
            message=f"[SIMULÉ · {reviewer_id}] {texte}",
            snippet=contenu.strip()[:80], rule_id=f"fake.{reviewer_id}.{i}",
            confidence=confiance,
        ))

    return ReviewResult(
        status="COMMENT",
        summary=(f"[SIMULÉ · {reviewer_id}] Aucun modèle n'a été appelé "
                 f"(`REVIEWME_FAKE`). {len(findings)} finding(s) fabriqué(s) sur "
                 f"{len(candidates)} ligne(s) ajoutée(s) du diff."),
        findings=findings,
        raw="",
        metadata={"model": "simulé", "cost_usd": 0.0, "duration_ms": 0,
                  "input_tokens": 0, "output_tokens": 0, "fake": True},
        parsed_ok=True,
    )
