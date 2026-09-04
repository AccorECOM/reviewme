"""Conventions du dépôt reviewé, lues à la SOURCE (ADR v3 D13).

Les conventions d'un projet vivent déjà dans son dépôt (`AGENTS.md`, `CONTRIBUTING.md`,
docs internes). Les recopier dans la config d'un reviewer crée une copie qui diverge
silencieusement dès la première évolution du repo. Un reviewer déclare donc ce qu'il faut
lire, et le core le résout au moment de la review :

    [context]
    read = ["AGENTS.md", "documentations/CODING_STYLE.md", "documentations/"]

- **fichier** -> son contenu est injecté (toujours à jour, et l'agent ne peut pas « oublier »
  d'aller le lire) ;
- **dossier** -> l'inventaire de ses `.md` est injecté, l'agent ouvre ce qui le concerne
  (injecter un dossier entier coûterait cher pour rien) ;
- **chemin absent** -> WARNING explicite. C'est le gain principal du déclaratif : un chemin
  mort se voit, au lieu de faire silencieusement perdre ses conventions au reviewer.
"""
from __future__ import annotations

import logging
from pathlib import Path

MAX_FILE_BYTES = 20_000       # un fichier de conventions plus gros n'est pas fait pour être lu d'un bloc
MAX_TOTAL_BYTES = 60_000      # garde-fou de coût sur l'ensemble du bloc
MAX_LISTED = 40               # inventaire d'un dossier


def _safe_join(root: Path, rel: str) -> Path | None:
    """Résout un chemin DANS le dépôt. Refuse toute échappée (`../`, chemin absolu).

    `root` doit DÉJÀ être résolu par l'appelant : sur macOS `/tmp` est un lien vers
    `/private/tmp`, donc comparer un chemin résolu à une racine non résolue échoue.
    """
    try:
        target = (root / rel).resolve()
        target.relative_to(root)
    except (ValueError, OSError):
        return None
    return target


def build_repo_context(spec, repo_path: str, logger: logging.Logger) -> str:
    """Bloc de conventions à injecter dans le prompt, ou "" si le reviewer n'en déclare pas."""
    if not spec.context_read:
        return ""

    # Résolu une fois : REPO_PATH peut passer par un lien symbolique (fréquent sur macOS).
    root = Path(repo_path).expanduser().resolve()
    if not root.is_dir():
        logger.warning("[%s] REPO_PATH introuvable (%s) : conventions du dépôt non lues",
                       spec.id, root)
        return ""

    parts: list[str] = []
    missing: list[str] = []
    budget = MAX_TOTAL_BYTES

    for rel in spec.context_read:
        target = _safe_join(root, rel)
        if target is None:
            logger.warning("[%s] context.read : chemin refusé (hors du dépôt) : %s", spec.id, rel)
            continue
        if not target.exists():
            missing.append(rel)
            continue

        if target.is_dir():
            docs = sorted(p.relative_to(root).as_posix()
                          for p in target.rglob("*.md") if p.is_file())[:MAX_LISTED]
            if docs:
                parts.append(f"### Documents disponibles dans `{rel}` (ouvre ceux qui concernent la PR)\n"
                             + "\n".join(f"- `{d}`" for d in docs))
            continue

        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            logger.warning("[%s] context.read : lecture impossible de %s (%s)", spec.id, rel, e)
            continue

        if len(text) > MAX_FILE_BYTES:
            text = text[:MAX_FILE_BYTES] + "\n\n_(tronqué — ouvre le fichier pour la suite)_"
        if len(text) > budget:
            logger.info("[%s] context.read : budget atteint, %s non injecté", spec.id, rel)
            continue
        budget -= len(text)
        parts.append(f"### `{rel}`\n{text}")

    if missing:
        # Volontairement bruyant : un chemin mort prive le reviewer de ses conventions sans
        # que personne ne s'en aperçoive.
        logger.warning("[%s] context.read : %d chemin(s) introuvable(s) dans le dépôt : %s",
                       spec.id, len(missing), ", ".join(missing))

    if not parts:
        return ""

    return ("# Conventions du dépôt reviewé (lues à la source, donc à jour)\n\n"
            "Ce sont les règles du projet : elles PRIMENT sur tes habitudes. Ce qu'elles "
            "n'exigent pas n'est pas un finding.\n\n" + "\n\n".join(parts))
