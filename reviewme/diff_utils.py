"""Parseur de patch unifié -> positions inline valides (défense anti-422).

GitHub valide le batch d'une review de façon ATOMIQUE : un seul commentaire sur une
ligne hors-hunk fait échouer TOUTE la review (422). On construit donc, à partir des
patches par fichier (`GET /pulls/{n}/files`), l'ensemble des lignes réellement
commentables côté RIGHT, et on filtre les findings AVANT de poster.

Une ligne RIGHT-commentable = une ligne présente dans le nouveau fichier à l'intérieur
d'un hunk : ligne ajoutée (`+`) ou ligne de contexte (` `). On garde aussi le contenu
réel de la ligne, qui sert d'ancre STABLE au fingerprint.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


@dataclass
class LinePos:
    line: int
    content: str
    added: bool  # True = ligne ajoutée (+), False = ligne de contexte


def parse_patch(patch: str) -> dict[int, LinePos]:
    """Map new-file line number -> LinePos, pour un patch d'un fichier."""
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
            # ligne vide de contexte
            positions[new_line] = LinePos(new_line, "", added=False)
            new_line += 1
            continue
        tag, body = raw[0], raw[1:]
        if tag == "+":
            positions[new_line] = LinePos(new_line, body, added=True)
            new_line += 1
        elif tag == "-":
            pass  # côté LEFT, n'avance pas le compteur new-file
        elif tag == "\\":
            pass  # "\ No newline at end of file"
        else:  # contexte (préfixe espace)
            positions[new_line] = LinePos(new_line, body, added=False)
            new_line += 1
    return positions


def valid_positions(files: list[dict]) -> dict[str, dict[int, LinePos]]:
    """À partir de la réponse de `GET /pulls/{n}/files`, renvoie
    { path: { line_no: LinePos } } des lignes RIGHT-commentables."""
    out: dict[str, dict[int, LinePos]] = {}
    for f in files or []:
        path = f.get("filename")
        patch = f.get("patch")  # absent pour les fichiers binaires / trop gros
        if not path or not patch:
            continue
        parsed = parse_patch(patch)
        if parsed:
            out[path] = parsed
    return out


def resolve_line(file_map: dict[int, LinePos], claimed_line: int, snippet: str) -> tuple[int, str] | None:
    """Renvoie (line, content) commentable, en CORRIGEANT un numéro de ligne imprécis du LLM.

    Les LLM se trompent souvent de quelques lignes (ils tombent sur une ligne vide voisine).
    Si le `snippet` fourni par le finding ne correspond pas au contenu de la ligne annoncée,
    on cherche dans le diff la ligne dont le contenu matche le snippet (la plus proche de la
    ligne annoncée). Sans snippet exploitable, on retombe sur la ligne annoncée si elle existe.
    Renvoie None si rien n'est commentable (finding hors-diff)."""
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
    """Normalise le contenu d'une ligne pour un fingerprint stable (indépendant de
    l'indentation/espaces qui bougent). Volontairement agressif : on veut la même clé
    tant que la logique de la ligne ne change pas."""
    return re.sub(r"\s+", " ", content or "").strip()
