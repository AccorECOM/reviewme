"""Code préexistant : une ligne « ajoutée » par la PR qui existait déjà ailleurs avant elle.

Un fichier déplacé, un bloc copié ou extrait dans une nouvelle fonction apparaissent en
vert dans le diff. Le relecteur y trouve des défauts qui ne sont pas ceux de la PR
(« constructeur trop long », « variable statique non protégée ») et l'auteur les reçoit comme
des reproches injustes. Les abandonner ferait perdre un vrai problème ; on les TAGUE.

`git blame -C -C -C` suit les lignes à travers déplacements et copies, tous fichiers et
tous commits confondus. Une ligne dont le commit d'origine n'est pas un commit de la PR
est préexistante. Ne porte que sur les lignes qui ont un finding : -C -C -C est coûteux,
et on n'a besoin de savoir que pour elles.

Best-effort : clone peu profond, base absente, git en échec -> rien n'est tagué.
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
    """Commits de la PR (`origin/<base>..HEAD`), ou None si la base est introuvable."""
    out = _git(repo_path, "rev-list", f"origin/{base_ref}..HEAD")
    return None if out is None else {sha.strip() for sha in out.splitlines() if sha.strip()}


_HEADER = re.compile(r"^([0-9a-f]{40}) \d+ (\d+)")
DEADLINE_S = 120


def preexisting_lines(repo_path: str, path: str, lines: set[int],
                      commits: set[str]) -> set[int]:
    """Parmi `lines` (côté HEAD), celles dont l'origine précède la PR. Un seul `blame` par
    fichier, avec un `-L` par ligne."""
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
    """Contenus normalisés des lignes SUPPRIMÉES par la PR."""
    from .diff_utils import normalize_line
    return {normalize_line(b[1:]) for b in diff.splitlines()
            if b.startswith("-") and not b.startswith("---") and normalize_line(b[1:])}


def tag_preexisting(findings: list, repo_path: str, base_ref: str,
                    logger: logging.Logger, anchor_of=None, removed: set[str] | None = None) -> int:
    """Pose `preexisting = True` sur les findings qui portent sur du code DÉPLACÉ par la PR.

    Deux conditions : le blame fait remonter la ligne avant la PR, ET son contenu fait
    partie des lignes que la PR supprime ailleurs. Sans la seconde, une ligne COPIÉE (un
    motif dangereux recollé) passerait pour préexistante alors que la PR l'introduit.

    Une ligne de CONTEXTE du diff (non ajoutée, typiquement dans un fichier renommé) est
    préexistante par définition, sans blame.

    `anchor_of(finding)` -> (ligne, contenu[, ajoutée]) ré-ancrés sur le diff, ou None.
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
        logger.info("pré-existant : commits de la PR introuvables (base origin/%s, clone "
                    "peu profond ?) — aucun tag", base_ref)
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
            logger.info("pré-existant : délai de %ds atteint, fichiers restants non examinés",
                        DEADLINE_S)
            break
        old |= {(path, n) for n in preexisting_lines(repo_path, path, ls, commits)}
    for f in findings:
        a = anchors[id(f)]
        if a and not f.preexisting and (f.path, a[0]) in old:
            f.preexisting = True
            tagged += 1
    return tagged
