"""Devenir des remarques postées : `reviewme enrich --since 14d`.

Le registre (`ledger.py`) dit ce que le bot a dit. Il ne dit pas si c'était utile. Ce
module relit les PR de la fenêtre et note, pour chaque commentaire du bot :

- **suivi d'effet** : la ligne commentée a-t-elle disparu du fichier à la tête de la PR ?
  On compare des empreintes (`fingerprint_hash`), jamais du texte : ça marche sans que le
  registre ait gardé le contenu de la ligne ;
- **réactions** 👍 / 👎, par auteur, en distinguant l'auteur de la PR des autres ;
- **réponses** humaines, étiquetées par mots-clés (corrigé, faux positif, voulu…) ;
- **fil résolu** ou non, et par qui ;
- **commentaires humains voisins** (≤ 10 lignes, même fichier) : un humain qui commente à
  côté du bot dit quelque chose de ce que le bot a vu, ou raté.

Et, par PR (`prs.jsonl`) : état, commits après la première review, commentaires humains —
dont ceux sur des lignes où le bot n'a rien dit (manqués approchés) et ceux sur des lignes
où il avait un finding ÉCARTÉ (ce que le filtre coûte en rappel).

Collecte DÉTERMINISTE, sans LLM. Idempotent : chaque passe ajoute une ligne par
commentaire ; pour un même `comment_id`, la plus récente fait foi.

Lecture seule côté GitHub. Les textes (réponses, commentaires voisins) ne sont écrits que
si `ledger.include_text` est actif, comme dans le registre.
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .diff_utils import normalize_line
from .ledger import FINDINGS_FILE, SCHEMA_VERSION, _append, read_jsonl
from .reconciler import _MARKER_RE, _SIGNATURE, fingerprint_hash, marker_key

ENRICH_FILE = "enrich.jsonl"
PRS_FILE = "prs.jsonl"
NEARBY_LINES = 10
MAX_TEXT = 1000

#: Ordre = priorité : une réponse « non c'est voulu, corrigé quand même » est `intended`.
_LABELS = (
    ("false_positive", r"faux positif|false positive|\bfaux\b|\bwrong\b|incorrect|pas vrai|"
                       r"n'est pas le cas|\bhallucin|ne s'applique pas|doesn'?t apply|not true"),
    ("intended", r"volontaire|\bvoulu\b|intended|by design|expr[eè]s|on garde|c'est normal|"
                 r"assum[ée]|on le laisse|as expected|on purpose"),
    ("fixed", r"corrig[ée]|\bfix(ed)?\b|c'est fait|\bdone\b|r[ée]solu|modifi[ée]|chang[ée]|"
              r"updated|bien vu|good catch"),
    ("unimportant", r"pas important|\bnit\b|osef|pas prioritaire|plus tard|\blater\b|"
                    r"hors scope|hors sujet|out of scope|pas grave"),
)
_REMEMBER = re.compile(r"^\s*@reviewme\s+remember\s+(.+)", re.IGNORECASE | re.DOTALL)


def label_reply(text: str) -> str:
    """Étiquette d'une réponse humaine, par mots-clés. À relire : c'est un tri grossier."""
    t = (text or "").lower()
    for label, motif in _LABELS:
        if re.search(motif, t):
            return label
    return "question" if t.strip().endswith("?") else "other"


def parse_since(value: str) -> timedelta:
    """`14d`, `36h` -> timedelta. Un nombre seul compte en jours."""
    m = re.fullmatch(r"\s*(\d+)\s*([dh]?)\s*", value or "")
    if not m:
        raise ValueError(f"fenêtre invalide : {value!r} (attendu : 14d, 36h)")
    n = int(m.group(1))
    return timedelta(hours=n) if m.group(2) == "h" else timedelta(days=n)


def _is_bot(comment: dict) -> bool:
    """Un commentaire du bot se reconnaît à son marqueur ou à sa signature, jamais à
    l'auteur : avec un PAT, le bot commente sous l'identité d'un humain."""
    body = comment.get("body", "") or ""
    return marker_key(body) is not None or _SIGNATURE in body


def _clean(text: str) -> str:
    return _MARKER_RE.sub("", text or "").strip()[:MAX_TEXT]


def _fingerprints_of(content: str | None, path: str) -> set[str]:
    if content is None:
        return set()
    return {fingerprint_hash(path, line) for line in content.splitlines()
            if normalize_line(line)}


class _Files:
    """Cache des empreintes d'un fichier à un commit (un appel API par couple)."""

    def __init__(self, gh):
        self.gh, self.cache = gh, {}

    def fingerprints(self, path: str, ref: str) -> set[str] | None:
        key = (path, ref)
        if key not in self.cache:
            try:
                self.cache[key] = _fingerprints_of(self.gh.get_file_at(path, ref), path)
            except Exception:
                self.cache[key] = None     # inconnu : on ne conclut rien
        return self.cache[key]


def _last_by_comment(rows: list[dict]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for r in rows:
        if r.get("comment_id") is not None:
            out[r["comment_id"]] = r
    return out


def enrich_pr(gh, pr_number: int, rows: list[dict], previous: dict[int, dict], *,
              with_text: bool, logger: logging.Logger, now: str | None = None,
              files: _Files | None = None) -> tuple[list[dict], dict]:
    """Enrichit UNE PR. `rows` = ses lignes du registre (variant live), `previous` = la
    dernière ligne d'enrichissement connue par comment_id. Renvoie (lignes, ligne PR)."""
    now = now or datetime.now(UTC).isoformat(timespec="seconds")
    files = files or _Files(gh)
    pr = gh.get_pr(pr_number)
    head = pr["head"]["sha"]
    author = (pr.get("user") or {}).get("login", "")
    comments = gh.list_review_comments(pr_number)

    try:
        threads = {((t.get("comments") or {}).get("nodes") or [{}])[0].get("databaseId"): t
                   for t in gh.review_threads(pr_number)}
    except Exception as e:
        logger.info("PR #%s : fils de review illisibles en GraphQL (%s)", pr_number,
                    type(e).__name__)
        threads = {}

    humans = [c for c in comments if not _is_bot(c)]
    human_roots = [c for c in humans if c.get("in_reply_to_id") is None]
    replies_by_parent: dict[int, list[dict]] = {}
    for c in humans:
        if c.get("in_reply_to_id") is not None:
            replies_by_parent.setdefault(c["in_reply_to_id"], []).append(c)

    def role(login: str) -> str:
        return "pr_author" if login and login == author else "other"

    out: list[dict] = []
    for cid, row in _last_by_comment(rows).items():
        if row.get("in_summary"):
            continue                       # commentaire global : pas de ligne à suivre
        prev = previous.get(cid, {})
        line = {"schema_version": SCHEMA_VERSION, "ts": now, "comment_id": cid,
                "pr": pr_number, "reviewer": row.get("reviewer"),
                "fingerprint": row.get("fingerprint"), "rule_id": row.get("rule_id"),
                "head_sha": head}

        # Suivi d'effet : l'empreinte n'existe plus nulle part dans le fichier à la tête.
        gone = prev.get("fingerprint_gone_at")
        if not gone and row.get("fingerprint"):
            present = files.fingerprints(row["path"], head)
            if present is not None and row["fingerprint"] not in present:
                gone = head
        line["fingerprint_gone_at"] = gone

        try:
            line["reactions"] = [
                {"content": r.get("content"), "user": (r.get("user") or {}).get("login", ""),
                 "role": role((r.get("user") or {}).get("login", ""))}
                for r in gh.list_comment_reactions(cid) if r.get("content") in ("+1", "-1")]
        except Exception:
            line["reactions"] = prev.get("reactions", [])     # garde la dernière lecture

        replies = []
        for c in sorted(replies_by_parent.get(cid, []), key=lambda c: c.get("created_at", "")):
            login = (c.get("user") or {}).get("login", "")
            body = c.get("body", "") or ""
            rep = {"user": login, "role": role(login), "ts": c.get("created_at"),
                   "label": label_reply(body)}
            rep["contests"] = rep["label"] in ("false_positive", "intended")
            remember = _REMEMBER.match(body)
            if remember:
                # Règle donnée explicitement par un humain : gardée telle quelle (c'est sa
                # finalité), mais seulement d'un auteur qui a le droit d'écrire.
                rep["remember"] = {"rule": remember.group(1).strip()[:MAX_TEXT],
                                   "trusted": gh.get_permission(login) in ("admin", "write",
                                                                           "maintain")}
            if with_text:
                rep["text"] = _clean(body)
            replies.append(rep)
        line["replies"] = replies

        thread = threads.get(cid)
        line["thread_resolved"] = bool(thread and thread.get("isResolved"))
        line["resolved_by"] = ((thread or {}).get("resolvedBy") or {}).get("login")

        nearby = []
        for c in human_roots:
            ln = c.get("line") or c.get("original_line")
            if c.get("path") == row.get("path") and ln and abs(ln - row.get("line", 0)) <= NEARBY_LINES:
                item = {"comment_id": c.get("id"), "user": (c.get("user") or {}).get("login"),
                        "line": ln}
                if with_text:
                    item["text"] = _clean(c.get("body", ""))
                nearby.append(item)
        line["human_comments_nearby"] = nearby
        out.append(line)

    # --- Par PR : manqués approchés et coût du filtre en rappel ---
    def near(c: dict, fates: tuple[str, ...]) -> bool:
        ln = c.get("line") or c.get("original_line") or 0
        return any(r.get("path") == c.get("path") and r.get("fate") in fates
                   and abs((r.get("line") or 0) - ln) <= NEARBY_LINES for r in rows)

    first_head = min(rows, key=lambda r: r.get("ts", ""))["head_sha"] if rows else head
    try:
        shas = [c.get("sha") for c in gh.list_pr_commits(pr_number)]
        after = len(shas) - shas.index(first_head) - 1 if first_head in shas else None
    except Exception:
        after = None
    spoken = ("posted", "deduped", "out_of_diff", "capped")
    dropped = ("dropped_low", "dropped_unverified")
    pr_line = {
        "schema_version": SCHEMA_VERSION, "ts": now, "pr": pr_number,
        "state": "merged" if pr.get("merged_at") else pr.get("state"),
        "merged_at": pr.get("merged_at"), "author": author, "head_sha": head,
        "commits_after_first_review": after,
        "human_comments": len(human_roots),
        "human_replies": len(humans) - len(human_roots),
        "human_comments_on_silent_lines": sum(
            1 for c in human_roots if not near(c, spoken + dropped)),
        "human_comments_on_dropped_findings": sum(
            1 for c in human_roots if near(c, dropped) and not near(c, spoken)),
    }
    if with_text:
        pr_line["human_comments_text"] = [
            {"comment_id": c.get("id"), "user": (c.get("user") or {}).get("login"),
             "path": c.get("path"), "line": c.get("line") or c.get("original_line"),
             "near_bot": near(c, spoken), "near_dropped": near(c, dropped),
             "text": _clean(c.get("body", ""))} for c in human_roots]
    return out, pr_line


def enrich(gh, root: Path, since: timedelta, logger: logging.Logger, *, with_text: bool,
           only_pr: int | None = None) -> dict:
    """Enrichit toutes les PR du registre vues dans la fenêtre. Renvoie des compteurs."""
    limite = (datetime.now(UTC) - since).isoformat(timespec="seconds")
    rows = [r for r in read_jsonl(root / FINDINGS_FILE)
            if r.get("variant", "live") == "live" and r.get("ts", "") >= limite
            and not r.get("dry_run")]
    if only_pr is not None:
        rows = [r for r in rows if r.get("pr") == only_pr]
    previous = _last_by_comment(read_jsonl(root / ENRICH_FILE))
    by_pr: dict[int, list[dict]] = {}
    for r in rows:
        by_pr.setdefault(r["pr"], []).append(r)

    files = _Files(gh)
    lines: list[dict] = []
    prs: list[dict] = []
    for pr_number, pr_rows in sorted(by_pr.items()):
        try:
            out, pr_line = enrich_pr(gh, pr_number, pr_rows, previous, with_text=with_text,
                                     logger=logger, files=files)
        except Exception as e:
            logger.warning("PR #%s : enrichissement impossible (%s: %s)", pr_number,
                           type(e).__name__, e)
            continue
        lines.extend(out)
        prs.append(pr_line)

    _append(root / ENRICH_FILE, lines)
    _append(root / PRS_FILE, prs)
    logger.info("enrich : %d PR, %d commentaire(s) suivis (%s)", len(prs), len(lines), root)
    return {"prs": len(prs), "comments": len(lines)}
