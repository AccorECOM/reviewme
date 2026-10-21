"""Reconciler: turns findings into inline comments, with dedup and thread
lifecycle.

Guarantees:
- ANTI-422: each finding is validated against the diff (`diff_utils.valid_positions`)
  BEFORE posting. Out-of-diff findings go into the summary, never inline. On a
  residual 422, we fall back to a global comment (nothing is lost).
- STABLE DEDUP: fingerprint = sha1(path + actual line content), NOT the snippet
  narrated by the LLM. Same finding across two runs -> same marker -> no duplicate.
- IDENTITY BY MARKER: we only recognise OUR comments by the marker
  `<!-- reviewme:REVIEWER:HASH -->`, never by author (otherwise we would touch human
  threads).
- PER-REVIEWER ISOLATION (ADR v3 D3): the dedup key is the PAIR (reviewer_id, hash).
  Without it, two reviewers pointing at the same line dedup each other and the second
  is silently swallowed.
- BACKWARD COMPAT: v0.2 markers (`<!-- reviewme:HASH -->`, without reviewer) are read
  as belonging to `tech`, the single reviewer at the time. The HASH itself is
  UNCHANGED (the reviewer_id is a prefix, it does NOT go into the sha1): a PR already
  commented by v0.2 therefore dedups normally on the first v3 run, without a burst of
  duplicates.
- LIFECYCLE: if a finding persists while its comment has become outdated (new
  commit), we REPLY in the existing thread instead of duplicating a top-level one.
- SECURITY: all text is scrubbed (secrets/paths) before posting.

v3 split (ADR v3 D5): `prepare()` performs NO write (pure computation, hence
parallelisable across reviewers); `post_all()` is the ONLY GitHub write point, which
allows sharing the dedup, staying within the 80 writes/min limit and applying the
per-PR comment cap (D6) — impossible to compute reviewer by reviewer.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field

from .config import Config
from .diff_utils import normalize_line, resolve_line, valid_positions
from .github_client import GitHubApiError, GitHubClient
from .models import ReviewResult, Severity

# Two accepted forms: `reviewme:<reviewer>:<hash>` (v3) and `reviewme:<hash>` (v0.2).
_MARKER_RE = re.compile(r"<!--\s*reviewme:(?:([a-z0-9][a-z0-9_-]{0,31}):)?([0-9a-f]{6,})\s*-->")

#: Reviewer assigned to v0.2 markers lacking an identifier (see ADR v3 D3).
LEGACY_REVIEWER_ID = "tech"
_SEV_LABEL = {
    Severity.BLOCKER: "[BLOCKER]",
    Severity.IMPORTANT: "[IMPORTANT]",
    Severity.MINOR: "[MINOR]",
}
#: Priority order when the D6 cap forces a cut.
_SEV_RANK = {Severity.BLOCKER: 0, Severity.IMPORTANT: 1, Severity.MINOR: 2}
from .strings import ALL_SIGNATURES, t  # noqa: E402  (posted texts, in the project's language)

#: Historical French signature, kept for import compatibility.
_SIGNATURE = ALL_SIGNATURES[1]
#: Verification pass verdicts that discard a finding (see `verify.py`).
_VERIFY_DROP = frozenset({"refuted", "missing", "uncited", "invalid"})


def fingerprint_hash(path: str, line_content: str) -> str:
    """Anchoring hash of a finding. MUST NOT CHANGE without a migration plan.

    Deliberately INDEPENDENT of the line number: that is what allows re-anchoring without
    duplicates when code moves. Accepted limitation: two lines with identical normalized
    content in the same file share the hash (rare; fine tracking of the 2nd occurrence
    may be lost). An index by (marker, line) is a v2 option.

    The `reviewer_id` does NOT go in here: it is added as a marker prefix (see
    `fingerprint`), which keeps v0.2 markers backward-compatible without recomputation.
    """
    return hashlib.sha1(f"{path}\n{normalize_line(line_content)}".encode()).hexdigest()[:10]


def fingerprint(path: str, line_content: str,
                reviewer_id: str = LEGACY_REVIEWER_ID) -> str:
    """HTML marker posted in the comment body: `<!-- reviewme:<reviewer>:<hash> -->`."""
    return f"<!-- reviewme:{reviewer_id}:{fingerprint_hash(path, line_content)} -->"


def summary_fingerprint(reviewer_id: str) -> str:
    """Marker of a reviewer's GLOBAL comment (`global` mode, ADR v3 D2).

    Anchored on the reviewer alone: there is only one global comment per reviewer and per
    PR, updated from commit to commit instead of stacking a new one each time.
    """
    h = hashlib.sha1(f"__summary__\n{reviewer_id}".encode()).hexdigest()[:10]
    return f"<!-- reviewme:{reviewer_id}:{h} -->"


def marker_key(body: str) -> tuple[str, str] | None:
    """(reviewer_id, hash) of an existing comment, or None if it is not ours.

    A v0.2 marker without a reviewer is assigned to `tech` (LEGACY_REVIEWER_ID): it is THE
    migration rule; without it, the first v3 review would repost everything as duplicates.
    """
    m = _MARKER_RE.search(body or "")
    if not m:
        return None
    return (m.group(1) or LEGACY_REVIEWER_ID, m.group(2))


def _scrub(text: str | None) -> str:
    from .scrub import scrub_text
    return scrub_text(text)


def _guard(text: str, logger: logging.Logger, pr_number: int) -> str:
    """Final safeguard: NEVER post a text that still contains a secret after scrubbing."""
    from .scrub import contains_secret
    if contains_secret(text):
        logger.warning("PR #%s: secret detected after scrub — text masked before posting", pr_number)
        return "[content masked — potential secret detected after scrub]"
    return text


def _comment_body(finding, marker: str, prefix: str = "", lang: str = "en") -> str:
    label = _SEV_LABEL.get(finding.severity, "[MINOR]")
    head = f"{prefix} " if prefix else ""
    lines = [f"**{head}{label}** {_scrub(finding.message)}"]
    if finding.suggestion:
        lines.append(f"\n{t('suggestion', lang)} {_scrub(finding.suggestion)}")
    if getattr(finding, "preexisting", False):
        lines.append("\n" + t("moved_code_note", lang))
    if (finding.verification or {}).get("status") == "error":
        # Verification failed: the comment still goes through, but the reader knows it.
        lines.append("\n" + t("unverified_note", lang))
    lines.append(f"\n{marker}")
    return "\n".join(lines)


def _summary_body(result: ReviewResult, out_of_diff: list, head_sha: str,
                  title: str = "", *,
                  retained: list | None = None, unverified: int = 0,
                  minor: list | None = None, preexisting: list | None = None,
                  advice: list | None = None, lang: str = "en") -> str:
    """Summary body. `retained`: listed findings (global mode, where they have no
    inline comment). `unverified`: findings discarded by verification.

    The synthesis written by the model describes ALL its findings, including those that
    verification discarded: posting it would bring them back through the back door. In
    that case, only the retained points are included, as a list.
    """
    parts = [f"## {title or t('review_title', lang)}"]
    if result.summary and not unverified:
        parts.append(_scrub(result.summary))
    elif unverified:
        parts.append(t("dropped_unverified", lang, n=unverified))
    if retained:
        parts.append("")
        for f in retained:
            parts.append(f"- `{_scrub(f.path)}:{f.line}` — {_SEV_LABEL.get(f.severity, '[MINOR]')} "
                         f"{_scrub(f.message)}")
    if advice:
        # Not defects: what a human must validate themselves on THIS PR.
        parts.append("\n" + t("advice_header", lang))
        for a in advice:
            ou = ", ".join(f"`{_scrub(p)}`" for p in a.paths)
            parts.append(f"- **{_scrub(a.topic)}** — {_scrub(a.why)}" + (f" ({ou})" if ou else ""))
    if minor:
        parts.append("\n" + t("minor_header", lang))
        for f in minor:
            parts.append(f"- `{_scrub(f.path)}:{f.line}` — {_scrub(f.message)}")
    if preexisting:
        parts.append("\n" + t("moved_header", lang))
        for f in preexisting:
            parts.append(f"- `{_scrub(f.path)}:{f.line}` — {_SEV_LABEL.get(f.severity, '[MINOR]')} "
                         f"{_scrub(f.message)}")
    if out_of_diff:
        parts.append("\n" + t("out_of_diff_header", lang))
        for f in out_of_diff:
            parts.append(f"- `{_scrub(f.path)}:{f.line}` — {_SEV_LABEL.get(f.severity, '[MINOR]')} {_scrub(f.message)}")
    parts.append(f"\n---\n{t('signature', lang)} · commit `{head_sha[:8]}`")
    return "\n".join(parts)


def _degraded_body(summary_body: str, batch: list, lang: str = "en") -> str:
    """Fallback: inline failed (residual 422) -> everything in one global comment."""
    parts = [summary_body, "\n" + t("degraded_header", lang)]
    for c in batch:
        body = _MARKER_RE.sub("", c["body"]).strip()
        parts.append(f"- `{_scrub(c['path'])}:{c['line']}` — {body}")
    return "\n".join(parts)


def _api_comment(item: dict) -> dict:
    """Only lets through fields accepted by the API (`_*` keys are internal)."""
    return {k: v for k, v in item.items() if not k.startswith("_")}


# --------------------------------------------------------------------------- PR context

@dataclass
class PrContext:
    """GitHub state read ONCE and shared by all the reviewers of a PR."""
    valid: dict
    inline_by_marker: dict[tuple[str, str], dict] = field(default_factory=dict)
    global_by_marker: dict[tuple[str, str], dict] = field(default_factory=dict)


def load_pr_context(gh: GitHubClient, pr_number: int, *, with_globals: bool = False) -> PrContext:
    ctx = PrContext(valid=valid_positions(gh.get_pr_files(pr_number)))

    for c in gh.list_review_comments(pr_number):
        mk = marker_key(c.get("body", ""))
        if not mk or c.get("in_reply_to_id") is not None:
            continue
        prev = ctx.inline_by_marker.get(mk)
        # on collision, prefer the NON-outdated comment (deterministic)
        if prev is None or (prev.get("position") is None and c.get("position") is not None):
            ctx.inline_by_marker[mk] = c

    if with_globals:
        for c in gh.list_issue_comments(pr_number):
            mk = marker_key(c.get("body", ""))
            if mk:
                ctx.global_by_marker[mk] = c

    return ctx


# --------------------------------------------------------------------------- preparation

@dataclass
class PreparedReview:
    """A reviewer's result, ready to post. No write has happened yet."""
    reviewer_id: str
    output_mode: str = "inline"
    summary: str = ""
    batch: list[dict] = field(default_factory=list)
    replies: list[tuple[int, str]] = field(default_factory=list)
    global_body: str | None = None          # `global` mode, or unreadable JSON fallback
    global_update_id: int | None = None     # existing global comment to update
    threads_resolus: set = field(default_factory=set)   # avoids replying twice
    counts: dict = field(default_factory=dict)
    findings: list = field(default_factory=list)        # all findings, with their `fate`
    language: str = "en"                                # language of posted texts


def prepare(pr: dict, config: Config, ctx: PrContext, result: ReviewResult,
            logger: logging.Logger, reviewer_id: str, *,
            output_mode: str = "inline", prefix: str = "",
            silent_when_clean: bool = False, advice: bool = False,
            language: str = "en") -> PreparedReview:
    """Computes what should be posted for ONE reviewer. No network side effect."""
    pr_number = pr["number"]
    head_sha = pr["head"]["sha"]
    counts = {"posted": 0, "replied": 0, "deduped": 0, "out_of_diff": 0,
              "invalid": 0, "dropped_low": 0, "dropped_unverified": 0, "capped": 0,
              "resolus": 0, "fallback": False}
    lang = language
    prep = PreparedReview(reviewer_id=reviewer_id, output_mode=output_mode, counts=counts,
                          findings=list(result.findings), language=lang)

    # --- Fallback: unusable JSON -> global comment ---
    if not result.parsed_ok:
        logger.warning("PR #%s [%s]: non-JSON agent output, global comment fallback",
                       pr_number, reviewer_id)
        counts["fallback"] = True
        prep.global_body = _guard(
            f"## {t('fallback_title', lang, reviewer=reviewer_id)}\n\n{_scrub(result.raw)}\n\n"
            f"---\n{t('signature', lang)} · commit `{head_sha[:8]}`", logger, pr_number)
        return prep

    # --- Validation against the diff (anti-422) ---
    inline: list = []
    out_of_diff: list = []
    for f in result.findings:
        fmap = ctx.valid.get(f.path, {})
        resolved = resolve_line(fmap, f.line, f.snippet) if fmap else None
        if f.confidence < config.confidence_threshold:
            counts["dropped_low"] += 1
            f.fate = "dropped_low"
            # Anchored anyway: the ledger must be able to tell whether a human commented on
            # the line the threshold silenced — that is the threshold's cost in recall.
            if resolved is not None:
                f.line, f.line_content = resolved
            continue
        # Refuted by the verification pass: to the ledger, never on the PR.
        if (f.verification or {}).get("status") in _VERIFY_DROP:
            counts["dropped_unverified"] += 1
            f.fate = "dropped_unverified"
            if resolved is not None:
                f.line, f.line_content = resolved
            continue
        if resolved is None:
            f.valid = False
            f.fate, f.in_summary = "out_of_diff", True
            out_of_diff.append(f)
            counts["out_of_diff"] += 1
        else:
            # re-anchoring on the actual line (fixes the LLM's line number imprecision)
            f.line, f.line_content = resolved
            f.valid = True
            inline.append(f)

    # --- Routing (outside global mode): only serious comments introduced by the PR go
    # inline. The rest goes into the summary, without being lost: a minor inline comment
    # costs the developer's attention as much as a real problem.
    minor: list = []
    preexisting: list = []
    if output_mode != "global":
        seuil = _SEV_RANK.get(Severity.coerce(config.inline_min_severity), 1)
        kept = []
        for f in inline:
            deja_en_ligne = (reviewer_id, fingerprint_hash(f.path, f.line_content)) \
                in ctx.inline_by_marker
            if deja_en_ligne:
                # Already commented inline: stays on its thread (dedup), not duplicated in the summary.
                kept.append(f)
                continue
            if f.preexisting and f.severity != Severity.BLOCKER:
                # A BLOCKER stays inline even if moved: we say so, we do not relegate it.
                preexisting.append(f)
            elif _SEV_RANK.get(f.severity, 2) > seuil:
                minor.append(f)
            else:
                kept.append(f)
                continue
            f.fate, f.in_summary = "posted", True
        inline = kept
        counts["in_summary"] = len(minor) + len(preexisting)

    # --- Advice for the human reviewer: only if it concerns files of the PR.
    # Advice without an anchor in the PR is generic ("test well"): discarded.
    conseils = []
    if advice:
        for a in getattr(result, "advice", []) or []:
            a.paths = [p for p in a.paths if p in ctx.valid]
            if a.paths:
                conseils.append(a)
    counts["advice"] = len(conseils)

    title = (t("review_title_reviewer", lang, reviewer=reviewer_id) if prefix
             else t("review_title", lang))
    prep.summary = _guard(_summary_body(
        result, out_of_diff, head_sha, title,
        retained=inline if output_mode == "global" else None,
        unverified=counts["dropped_unverified"], minor=minor, preexisting=preexisting,
        advice=conseils, lang=lang),
        logger, pr_number)

    # --- `global` mode: a single comment, updated from commit to commit ---
    if output_mode == "global":
        marker = summary_fingerprint(reviewer_id)
        existing = ctx.global_by_marker.get(marker_key(marker))
        # "Silence is better than noise": nothing to say and nothing posted so far ->
        # no comment. An already posted comment, however, is updated so as not to leave
        # in place remarks that no longer hold.
        if silent_when_clean and not inline and not out_of_diff and not conseils \
                and existing is None:
            logger.info("PR #%s [%s]: nothing to report, no comment (silent_when_clean)",
                        pr_number, reviewer_id)
            return prep
        prep.global_body = f"{prep.summary}\n\n{marker}"
        prep.global_update_id = existing.get("id") if existing else None
        for f in inline:
            f.fate, f.in_summary = "posted", True
        return prep

    # --- Inline reconciliation ---
    for f in inline:
        marker = fingerprint(f.path, f.line_content, reviewer_id)
        # The dedup key is the PAIR (reviewer, hash): two reviewers can comment on the
        # same line without cancelling each other out (ADR v3 D3).
        existing = ctx.inline_by_marker.get((reviewer_id, fingerprint_hash(f.path, f.line_content)))
        if existing is None:
            f.fate = "posted"
            prep.batch.append({
                "path": f.path, "line": f.line, "side": "RIGHT",
                "body": _guard(_comment_body(f, marker, prefix, lang), logger, pr_number),
                "_severity": f.severity, "_confidence": f.confidence, "_reviewer": reviewer_id,
                "_finding": f,
            })
            continue
        # Already commented: the finding is attached to the existing comment.
        f.fate, f.comment_id = "deduped", existing.get("id")
        if existing.get("position") is None:
            # finding still there but comment became outdated -> reply in the thread
            prep.replies.append((existing["id"],
                                 t("still_relevant", lang, sha=head_sha[:8],
                                   signature=t("signature", lang))))
        else:
            counts["deduped"] += 1

    # --- Fixed points: the comment still exists, the finding has disappeared ---
    # Without this, an addressed comment stays open indefinitely and nobody knows it has
    # been addressed. We ONLY infer it if the commented line actually changed
    # (`position is None` = the comment became outdated on GitHub's side): otherwise, a
    # model that varies from one run to the next would be enough to announce an imaginary fix.
    # "Reproduced" = the model still saw this point, even if verification or the threshold
    # discarded it this time: it is not a fix, we announce nothing.
    reproduits = {fingerprint_hash(f.path, f.line_content) for f in result.findings
                  if f.line_content}
    for (rid, empreinte), commentaire in ctx.inline_by_marker.items():
        if rid != reviewer_id or empreinte in reproduits:
            continue
        if commentaire.get("position") is not None:
            continue                       # the line has not moved: we conclude nothing
        if commentaire.get("id") in prep.threads_resolus:
            continue
        prep.threads_resolus.add(commentaire["id"])
        prep.replies.append((commentaire["id"],
                             t("gone", lang, sha=head_sha[:8], signature=t("signature", lang))))
        counts["resolus"] = counts.get("resolus", 0) + 1

    if counts["dropped_low"]:
        logger.info("PR #%s [%s]: %d finding(s) below the confidence threshold (%d) not posted",
                    pr_number, reviewer_id, counts["dropped_low"], config.confidence_threshold)
    return prep


# --------------------------------------------------------------------------- writing

def _apply_cap(prepared: list[PreparedReview], cap: int, logger: logging.Logger,
               pr_number: int) -> list[dict]:
    """Comment cap PER PR, all reviewers combined (ADR v3 D6).

    Four zealous reviewers = 15+ comments = the dev tunes out and disables the bot. We
    keep the most serious, then the most confident; the rest is logged, not posted.
    """
    items = [c for p in prepared for c in p.batch]
    if cap <= 0 or len(items) <= cap:
        return items

    items.sort(key=lambda c: (_SEV_RANK.get(c["_severity"], 3), -c["_confidence"]))
    kept, dropped = items[:cap], items[cap:]
    for c in dropped:
        if c.get("_finding") is not None:
            c["_finding"].fate = "capped"
    for p in prepared:
        p.counts["capped"] = sum(1 for c in dropped if c["_reviewer"] == p.reviewer_id)
    logger.info("PR #%s: cap of %d comments reached, %d finding(s) not posted: %s",
                pr_number, cap, len(dropped),
                ", ".join(f"{c['_reviewer']}:{c['path']}:{c['line']}" for c in dropped))
    return kept


def _attach_comment_ids(gh: GitHubClient, pr_number: int, review: dict, batch: list[dict],
                        logger: logging.Logger) -> None:
    """Attaches to each posted finding the id of its comment (for the ledger).

    `create_review` only returns the review: we re-read its comments and match them
    by marker. Best-effort: a failure only affects the ledger, never the PR.
    """
    # Key (path, line, marker): two lines with identical content share the marker.
    par_cle = {(c["path"], c["line"], marker_key(c["body"])): c["_finding"] for c in batch
               if c.get("_finding") is not None}
    if not par_cle or not (review or {}).get("id"):
        return
    try:
        commentaires = gh.list_review_comments_of_review(pr_number, review["id"])
    except Exception as e:
        logger.info("PR #%s: ids of posted comments not re-read (%s) — incomplete ledger",
                    pr_number, type(e).__name__)
        return
    for c in commentaires:
        f = par_cle.get((c.get("path"), c.get("line") or c.get("original_line"),
                         marker_key(c.get("body", ""))))
        if f is not None:
            f.comment_id = c.get("id")


def post_all(pr: dict, config: Config, gh: GitHubClient, prepared: list[PreparedReview],
             logger: logging.Logger) -> dict:
    """ONLY GitHub write point (ADR v3 D5). Applies the D6 cap then posts."""
    pr_number = pr["number"]
    head_sha = pr["head"]["sha"]
    # `posted` stays the total (compatibility), but we distinguish the kind: without this, a
    # single global comment read as "1 inline" while no finding had gone through.
    totals = {"posted": 0, "posted_inline": 0, "posted_global": 0, "replied": 0,
              "deduped": 0, "out_of_diff": 0, "invalid": 0, "dropped_low": 0,
              "dropped_unverified": 0, "capped": 0, "resolus": 0, "fallback": False,
              "reviewers": len(prepared)}
    for p in prepared:
        for k in ("deduped", "out_of_diff", "invalid", "dropped_low", "dropped_unverified",
                  "capped", "resolus"):
            totals[k] += p.counts.get(k, 0)
        totals["fallback"] = totals["fallback"] or p.counts.get("fallback", False)

    batch = _apply_cap(prepared, config.max_comments_per_pr, logger, pr_number)
    replies = [r for p in prepared for r in p.replies]
    globals_ = [p for p in prepared if p.global_body]
    inline_summaries = [p.summary for p in prepared if p.output_mode != "global" and p.summary]
    summary_body = "\n\n---\n\n".join(inline_summaries) if inline_summaries else ""
    # Comments to deliver through the summary only (out-of-diff, minor, pre-existing).
    out_of_diff_only = any(p.counts.get("out_of_diff") or p.counts.get("in_summary")
                           or p.counts.get("advice") for p in prepared)

    if config.dry_run:
        # In dry-run, SHOW what would be posted: that is the whole point of the mode,
        # without which we can neither judge relevance nor calibrate the confidence threshold.
        logger.info("PR #%s [DRY-RUN]: %d inline, %d global, %d replies",
                    pr_number, len(batch), len(globals_), len(replies))
        for c in batch:
            body = _MARKER_RE.sub("", c["body"]).replace("\n", " ").strip()
            logger.info("  [%s] %s:%s (confidence %s) — %s",
                        c["_reviewer"], c["path"], c["line"], c["_confidence"], body[:400])
        for p in prepared:
            if p.global_body:
                extrait = _MARKER_RE.sub("", p.global_body).strip()
                logger.info("  [%s] global comment:\n%s", p.reviewer_id, extrait[:1500])
            elif p.summary and p.output_mode != "global":
                logger.info("  [%s] summary:\n%s", p.reviewer_id, p.summary[:1000])
        totals["posted"] = totals["posted_inline"] = len(batch)
        totals["posted_global"] = len(globals_)
        return totals

    # --- Inline post: ONE single write for all reviewers (limit 80 creations/min) ---
    if batch:
        api_batch = [_api_comment(c) for c in batch]
        try:
            review = gh.create_review(pr_number, head_sha, summary_body, api_batch, event="COMMENT")
            totals["posted_inline"] = len(api_batch)
            totals["posted"] = len(api_batch)
            _attach_comment_ids(gh, pr_number, review, batch, logger)
        except GitHubApiError as e:
            if e.status_code == 422:
                logger.warning("PR #%s: 422 on the review batch, global comment fallback", pr_number)
                repli = gh.post_issue_comment(pr_number, _guard(_degraded_body(summary_body, api_batch,
                                                                               prepared[0].language),
                                                                logger, pr_number))
                for c in batch:
                    if c.get("_finding") is not None:
                        c["_finding"].in_summary = True
                        c["_finding"].comment_id = (repli or {}).get("id")
                totals["fallback"] = True
            else:
                raise
    elif summary_body and out_of_diff_only:
        # No inline, but comments to deliver through the summary only (out-of-diff,
        # minor, pre-existing). ONE comment, updated from commit to commit:
        # posting one on every push would drown the conversation.
        # NB: a completely clean PR does NOT get a comment.
        marker = summary_fingerprint("resume")
        body = f"{summary_body}\n\n{marker}"
        existing = next((c for c in gh.list_issue_comments(pr_number)
                         if marker_key(c.get("body", "")) == marker_key(marker)), None)
        if existing:
            gh.update_issue_comment(existing["id"], body)
        else:
            gh.post_issue_comment(pr_number, body)

    # --- Global comments (reviewers in `global` mode, fallbacks) ---
    for p in globals_:
        body = _guard(p.global_body, logger, pr_number)
        try:
            if p.global_update_id:
                posted = gh.update_issue_comment(p.global_update_id, body)
            else:
                posted = gh.post_issue_comment(pr_number, body)
            for f in p.findings:
                if f.fate == "posted" and f.in_summary:
                    f.comment_id = (posted or {}).get("id")
            totals["posted_global"] += 1
            totals["posted"] += 1
        except GitHubApiError:
            logger.warning("PR #%s [%s]: global comment failed", pr_number, p.reviewer_id)

    # --- Replies in threads (re-anchoring / dialogue) ---
    for cid, msg in replies:
        try:
            gh.reply_to_comment(pr_number, cid, msg)
            totals["replied"] += 1
        except GitHubApiError:
            logger.warning("PR #%s: thread reply failed %s", pr_number, cid)

    logger.info("PR #%s: %d inline, %d global, %d replies, %d dedup, %d out-of-diff, "
                "%d below threshold, %d unverified, %d capped, %d resolved",
                pr_number, totals["posted_inline"], totals["posted_global"], totals["replied"],
                totals["deduped"], totals["out_of_diff"], totals["dropped_low"],
                totals["dropped_unverified"], totals["capped"], totals["resolus"])
    return totals


def reconcile(pr: dict, config: Config, gh: GitHubClient, result: ReviewResult,
              logger: logging.Logger, reviewer_id: str | None = None) -> dict:
    """Single-reviewer facade: prepares then posts in one pass."""
    reviewer_id = reviewer_id or getattr(config, "reviewer_id", None) or LEGACY_REVIEWER_ID
    ctx = load_pr_context(gh, pr["number"])
    prep = prepare(pr, config, ctx, result, logger, reviewer_id)
    return post_all(pr, config, gh, [prep], logger)
