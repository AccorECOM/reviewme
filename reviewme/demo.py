"""`reviewme demo-data`: a realistic synthetic ledger, to validate the dashboard's design.

Before a team has real data, the only way to judge whether a page answers its questions
is to look at it with numbers that behave like real ones: uneven developers, reviewers of
different precision, pushes that re-find what is already commented, a control group,
two years of history across eras. Volume is not the point (about 70 reviewed PRs over 90
days); realism is. Everything is drawn from one `random.Random(seed)` and anchored on
`now`: the same seed and date give byte-identical files, so a page can be diffed after a
change to a section.

The ledger is written with the engine's own writers (`write_part`), the project settings
go through the real `project.toml` parser, and the page through the real `write`: a demo
that bypassed them would validate nothing. Logins are fictional (`*-demo`); no text
written by a person or a model is stored (as with `[ledger] include_text = false`), only
labels derived from French replies by the engine's own `label_reply`.

What is modelled (and is therefore worth checking on the page):
- 9 developers, one heavy and two occasional; ~40 % of merged PRs never reviewed
  (control group: slightly faster to merge, more human comments);
- `tech`, `architect`, `us`, `i18n` with acted-upon rates of about 60, 70, 50 and 35 %,
  plus one advice-only `advisor` run per PR; Sonnet / Opus split per reviewer;
- confidence around 75 (threshold 70), 10 % refuted by verification, 3 BLOCKERs (2 fixed
  before merge), reactions on ~45 % of findings, one rule contested 4 times;
- two `max_turns` runs, one `post_failed`, ~20 % of runs posting nothing new, cost 0.6 to
  3.5 $ per run with the diff size, cache ratio ~70 %, one settings change mid-window;
- two years of `history.jsonl` across three eras (a prototype bot under a person's login,
  a pause, ReviewMe), bug tickets per month, a triage pass.
"""
from __future__ import annotations

import json
import random
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from .enrich import ENRICH_FILE, PRS_FILE, label_reply
from .ledger import FINDINGS_FILE, RUNS_FILE, SNAPSHOT_DIR, write_part
from .pr_history import (
    HISTORY_BUGS_FILE,
    HISTORY_FILE,
    is_fix_branch,
    is_hotfix_branch,
    is_revert_branch,
)
from .triage import TRIAGE_FILE

REPO = "demo-org/mobile-app"
#: (login, weight): one heavy contributor, two occasional ones.
DEVS = (("alice-demo", 10), ("bob-demo", 5), ("carol-demo", 4), ("dave-demo", 4),
        ("erin-demo", 3), ("frank-demo", 3), ("grace-demo", 2), ("heidi-demo", 0.7),
        ("ivan-demo", 0.5))
MAINTAINER = "maintainer-demo"            # the v0 prototype bot posted under this login
SONNET, OPUS = "claude-sonnet-4-5", "claude-opus-4-5"
#: reviewer -> (acted-upon rate, share of Opus runs, rules, chance to run on a push)
REVIEWERS = {
    "tech": (0.60, 0.3, ("null-check", "force-unwrap", "error-handling", "thread-safety",
                         "memory-leak"), 1.0),
    "architect": (0.70, 0.8, ("layering", "dependency-direction", "god-object"), 0.45),
    "us": (0.50, 0.0, ("ac-missing", "edge-case", "acceptance-gap"), 0.6),
    "i18n": (0.35, 0.0, ("hardcoded-string", "plural-form", "date-format"), 0.4),
}
CONTESTED_RULE = ("i18n", "hardcoded-string")    # the one rule contested 4 times
THRESHOLD = 70
ERAS = (("prototype", "2026-03-10", "ai-review-poc", MAINTAINER),
        ("pause", "2026-10-09", "", ""),
        ("ReviewMe", "2026-11-03", "review-me", ""))

#: French replies, labelled by the engine's own keyword sort.
REPLIES_FIXED = ("Bien vu, c'est corrigé.", "Corrigé dans le dernier commit.",
                 "Bien vu, merci !")
REPLIES_CONTEST = ("Faux positif : la valeur ne peut pas être nulle ici.",
                   "C'est volontaire, on garde ce comportement.",
                   "Ne s'applique pas à ce module.")
REPLIES_OTHER = ("Pas prioritaire, on verra plus tard.", "Pourquoi ce changement ?",
                 "Hors scope pour cette PR.")


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def _gh(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class _Gen:
    def __init__(self, seed: int, days: int, now: datetime):
        self.rng = random.Random(seed)
        self.days, self.now = days, now
        self.start = now - timedelta(days=days)
        self.findings: list[dict] = []
        self.runs: list[dict] = []
        self.enrich: list[dict] = []
        self.prs: list[dict] = []
        self.triage: list[dict] = []
        self.history: list[dict] = []
        self.stats_runs: list[dict] = []
        self._comment = 5000
        self._run = 0
        self.blockers_left = [True, True, False]   # 3 BLOCKERs, 2 fixed before merge
        self.max_turns_left, self.post_failed_left = 2, 1
        self._follow: dict[str, list[int]] = {}    # reviewer -> [acted upon, posted]

    # ---- helpers
    def dev(self) -> str:
        return self.rng.choices([d for d, _ in DEVS], [w for _, w in DEVS])[0]

    def work_time(self, around: datetime) -> datetime:
        """Moved to a weekday working hour: the weekday and hour charts must look human."""
        dt = around
        while dt.weekday() >= 5:
            dt += timedelta(days=1)
        hour = min(18, max(8, int(self.rng.gauss(13, 2.5))))
        return dt.replace(hour=hour, minute=self.rng.randrange(60), second=0, microsecond=0)

    def size(self) -> tuple[int, int, int]:
        lines = int(min(1500, max(12, self.rng.lognormvariate(5.1, 0.9))))
        add = int(lines * self.rng.uniform(0.55, 0.85))
        return add, lines - add, max(1, min(40, lines // 35 + self.rng.randint(0, 3)))

    def cost(self, lines: int, model: str) -> float:
        base = 0.6 + 2.6 * min(1.0, lines / 1200) * self.rng.uniform(0.7, 1.1)
        return round(min(3.5, base * (1.25 if model == OPUS else 1.0)), 4)

    # ---- one reviewed PR
    def reviewed_pr(self, number: int, created: datetime, config_of) -> None:
        rng = self.rng
        author = self.dev()
        add, dele, files = self.size()
        lines = add + dele
        merged = rng.random() < 0.88
        pushes = min(7, 1 + int(rng.expovariate(1.0)))
        cycle_h = rng.uniform(6, 30) + lines / 25 + pushes * rng.uniform(2, 8)
        merged_at = created + timedelta(hours=cycle_h)
        if merged_at > self.now - timedelta(hours=2):
            merged = False
        first_review = created + timedelta(hours=rng.uniform(1.5, 14))
        push_times = sorted(created + timedelta(minutes=rng.randint(4, 40))
                            + timedelta(hours=cycle_h * i / (pushes + 0.5))
                            for i in range(pushes))
        open_findings: list[dict] = []           # posted, still on the PR
        first_bot = None
        prs_line_comments = 0
        for i, at in enumerate(push_times):
            if at > self.now:
                break
            self._run += 1
            run_id, head = f"demo-{self._run:04d}", f"{number:x}{i:02d}{rng.randrange(4096):03x}"
            chash = config_of(at)
            base = {"schema_version": 1, "run_id": run_id, "ts": _iso(at), "repo": REPO,
                    "pr": number, "head_sha": head, "base_sha": "main", "variant": "live",
                    "engine_commit": "demo", "config_hash": chash, "dry_run": False,
                    "status": "ok", "trigger": "pr_build" if rng.random() < 0.85
                    else rng.choice(["manual", "watch"])}
            first_bot = first_bot or _iso(at)
            # Earlier findings this push fixed: their line is gone at this head.
            for f in open_findings:
                if f["_follow"] and not f["_gone"] and rng.random() < 0.6:
                    f["_gone"] = head
            new_posts = 0
            for rid, (prec, opus, rules, chance) in REVIEWERS.items():
                if i > 0 and rng.random() > 0.7:
                    continue                      # not every reviewer re-runs every push
                if i == 0 and rng.random() > chance and rid != "tech":
                    continue
                model = OPUS if rng.random() < opus else SONNET
                cost = self.cost(lines, model)
                inp = int(30000 + lines * 90 * rng.uniform(0.8, 1.2))
                run = {**base, "reviewer": rid, "model": model, "parsed_ok": True,
                       "num_turns": rng.randint(6, 22), "max_turns": 30,
                       "subtype": "success", "is_error": False,
                       "duration_s": int(40 + lines / 8 + rng.uniform(0, 60)),
                       "cost_usd": cost, "input_tokens": inp,
                       "output_tokens": int(inp * rng.uniform(0.03, 0.06)),
                       "cache_read_tokens": int(inp * rng.uniform(0.62, 0.78)),
                       "cache_create_tokens": int(inp * 0.08), "diff_files": files,
                       "diff_lines": lines,
                       "context_files": [{"path": "AGENTS.md", "bytes": 4200,
                                          "truncated": False, "source": "context.auto"}],
                       "threshold": THRESHOLD, "fates": {}, "advice": 0}
                # The first two big diffs a deep reviewer reads run out of turns.
                if self.max_turns_left and lines > 500 and rid in ("tech", "architect"):
                    self.max_turns_left -= 1
                    run |= {"subtype": "error_max_turns", "is_error": True,
                            "parsed_ok": False, "num_turns": 30, "findings": 0,
                            "verification": None}
                    self.runs.append(run)
                    continue
                produced = self.findings_of(base, rid, model, rules, prec, i, open_findings,
                                            merged, merged_at)
                new_posts += sum(1 for f in produced if f["fate"] == "posted"
                                 and not f.get("_dedup"))
                verified = [f for f in produced if f["verification"]["status"] != "skipped"]
                run["findings"] = len(produced)
                run["verification"] = {
                    "status": "ok", "confirmed": sum(1 for f in verified if
                                                     f["verification"]["status"] == "confirmed"),
                    "refuted": sum(1 for f in verified
                                   if f["verification"]["status"] == "refuted"),
                    "cost_usd": round(cost * 0.18, 4)} if verified else None
                if self.post_failed_left and i == 1 and rng.random() < 0.2:
                    self.post_failed_left -= 1
                    run["status"] = "post_failed"
                self.runs.append(run)
                for f in produced:
                    f.pop("_dedup", None)
                    self.findings.append({k: v for k, v in f.items() if not k.startswith("_")})
            if i == 0:                            # one advice-only run per PR
                self.runs.append({**base, "reviewer": "advisor", "model": SONNET,
                                  "parsed_ok": True, "num_turns": 4, "max_turns": 10,
                                  "subtype": "success", "is_error": False, "duration_s": 25,
                                  "cost_usd": round(self.cost(lines, SONNET) * 0.35 + 0.4, 4),
                                  "input_tokens": 18000, "output_tokens": 600,
                                  "cache_read_tokens": 12600, "cache_create_tokens": 1500,
                                  "diff_files": files, "diff_lines": lines,
                                  "context_files": [], "verification": None,
                                  "threshold": THRESHOLD, "findings": 0, "fates": {},
                                  "advice": 1})
            prs_line_comments += new_posts
            self.stats_runs.append({"pr": number, "auteur": author, "ts": _iso(at)})
        state = "merged" if merged else ("open" if rng.random() < 0.8 else "closed")
        for f in open_findings:
            self.enrich.append(self.enrich_line(f, number, author, state == "merged",
                                                merged_at))
        human_comments = rng.choice([0, 0, 1, 1, 2, 3])
        self.prs.append({
            "schema_version": 1, "ts": _iso(self.now - timedelta(hours=3)), "pr": number,
            "state": state, "merged_at": _gh(merged_at) if state == "merged" else None,
            "author": author, "head_sha": "h", "commits_after_first_review": pushes - 1,
            "human_comments": human_comments, "human_replies": rng.randint(0, 2),
            "human_comments_on_silent_lines": min(human_comments, rng.choice([0, 0, 1])),
            "human_comments_on_dropped_findings": rng.choice([0, 0, 0, 1]),
            "reviewed": True, "control_group": True, "created_at": _gh(created),
            "first_human_review_at": _gh(first_review) if rng.random() < 0.85 else None,
            "human_reviews": rng.randint(1, 3), "first_bot_run_at": first_bot,
            "additions": add, "deletions": dele, "changed_files": files})

    def findings_of(self, base, rid, model, rules, prec, push, open_findings, merged,
                    merged_at) -> list[dict]:
        rng = self.rng
        out = []
        # Re-finds of what is still open: deduped, never posted twice.
        for f in open_findings:
            if f["reviewer"] == rid and not f["_gone"] and rng.random() < 0.7:
                out.append({**f, **{k: base[k] for k in ("run_id", "ts", "head_sha",
                                                         "config_hash", "trigger")},
                            "fate": "deduped", "_dedup": True})
        n_new = rng.choices([0, 1, 2, 3, 4], [0.15, 0.35, 0.25, 0.15, 0.1])[0] if push == 0 \
            else rng.choices([0, 1, 2], [0.45, 0.4, 0.15])[0]
        for _ in range(n_new):
            sev = rng.choices(["IMPORTANT", "MINOR"], [0.42, 0.58])[0]
            # BLOCKERs only on PRs that get merged: "fixed before merge" needs a merge.
            if self.blockers_left and merged and rid in ("tech", "architect") \
                    and rng.random() < 0.08:
                sev = "BLOCKER"
            conf = max(40, min(99, int(rng.gauss(75, 11))))
            refuted = rng.random() < 0.10
            status = "refuted" if refuted else ("confirmed" if conf >= THRESHOLD
                                                else "skipped")
            fate = ("dropped_unverified" if refuted else
                    "posted" if conf >= THRESHOLD else "dropped_low")
            rule = rng.choice(rules)
            f = {**base, "reviewer": rid, "model": model,
                 "fingerprint": f"{rid}-{rng.getrandbits(40):010x}",
                 "path": rng.choice(["App/Checkout/CartView.swift", "App/Core/Api.kt",
                                     "App/Profile/ProfileViewModel.kt",
                                     "App/Resources/strings.xml", "App/Auth/Login.swift"]),
                 "line": rng.randint(8, 420), "severity_raw": sev, "severity": sev,
                 "confidence": conf, "rule_id": rule, "has_evidence": True,
                 "verification": {"status": status, "confidence": conf}, "fate": fate,
                 "comment_id": None, "in_summary": False, "preexisting": False,
                 "_gone": None, "_follow": self.follow(rid, prec), "_blocker_fix": None}
            if fate == "posted":
                self._comment += 1
                f["comment_id"] = self._comment
                if sev == "BLOCKER":
                    fix = self.blockers_left.pop(0)
                    f["_blocker_fix"] = fix
                    f["_follow"] = fix
                open_findings.append(f)
            elif sev == "BLOCKER":
                f["severity"] = f["severity_raw"] = "IMPORTANT"
            out.append(f)
        return out

    def follow(self, rid: str, prec: float) -> bool:
        """Acted upon or not, keeping each reviewer's running rate near its target: with
        ~40 findings per reviewer, independent coin flips can land 15 points off, and
        the demo exists to show reviewers whose precision differs as configured."""
        k, n = self._follow.setdefault(rid, [0, 0])
        out = k + self.rng.random() < prec * (n + 1)
        self._follow[rid] = [k + out, n + 1]
        return out

    def enrich_line(self, f: dict, pr: int, author: str, merged: bool,
                    merged_at: datetime) -> dict:
        rng = self.rng
        posted_at = datetime.fromisoformat(f["ts"])
        followed = f["_follow"]
        reactions, replies = [], []
        if rng.random() < 0.45:
            content = rng.choices(["+1", "-1", "rocket", "confused"],
                                  [0.5 if followed else 0.25, 0.1 if followed else 0.3,
                                   0.15 if followed else 0.0, 0.25])[0]
            reactions.append({"content": content, "user": author, "role": "pr_author",
                              "ts": _gh(posted_at + timedelta(hours=rng.uniform(0.3, 9)))})
        if rng.random() < 0.3:
            text = rng.choice(REPLIES_FIXED if followed else
                              REPLIES_CONTEST + REPLIES_OTHER)
            label = label_reply(text)
            reply_at = posted_at + timedelta(hours=rng.uniform(0.5, 20))
            if merged and reply_at.isoformat() > _iso(merged_at):
                reply_at = merged_at - timedelta(minutes=30)
            replies.append({"user": author, "role": "pr_author", "ts": _gh(reply_at),
                            "label": label, "contests": label in ("false_positive",
                                                                  "intended")})
        # Acted upon: the line went away at a later push, or (no later push, or a fix
        # elsewhere) a "fixed" reply says so; a BLOCKER fix is always seen in the code.
        gone = f["_gone"] or (f["head_sha"] + "x" if followed and (
            rng.random() < 0.6 or f["_blocker_fix"]) else None)
        if not followed:
            gone = None
        if followed and not gone and not any(r["label"] == "fixed" for r in replies):
            replies.append({"user": author, "role": "pr_author",
                            "ts": _gh(posted_at + timedelta(hours=2)), "label": "fixed",
                            "contests": False})
        signal = [r["ts"] for r in reactions] + [r["ts"] for r in replies]
        return {"schema_version": 1, "ts": _iso(self.now - timedelta(hours=4)),
                "comment_id": f["comment_id"], "pr": pr, "reviewer": f["reviewer"],
                "rule_id": f["rule_id"], "fingerprint_gone_at": gone, "file_gone_at": None,
                "reactions": reactions, "replies": replies,
                "thread_resolved": followed and rng.random() < 0.6,
                "resolved_by": author if followed else None,
                "first_signal_at": min(signal) if signal else None,
                "human_comments_nearby": []}

    # ---- contests: exactly one rule at 4, the others under 3
    def shape_contests(self) -> None:
        by_cid = {e["comment_id"]: e for e in self.enrich}
        posted = {f["comment_id"]: f for f in self.findings
                  if f["fate"] == "posted" and f["comment_id"]}

        def contested(e: dict) -> bool:
            return any(r["content"] == "-1" for r in e["reactions"]) or \
                any(r["contests"] for r in e["replies"])

        def calm(e: dict) -> None:
            e["reactions"] = [r for r in e["reactions"] if r["content"] != "-1"]
            e["replies"] = [r for r in e["replies"] if not r["contests"]]

        rules: dict[tuple, list[int]] = {}
        for cid, f in sorted(posted.items()):
            rules.setdefault((f["reviewer"], f["rule_id"]), []).append(cid)
        for key, cids in sorted(rules.items()):
            hits = [c for c in cids if c in by_cid and contested(by_cid[c])]
            keep = 4 if key == CONTESTED_RULE else 2
            for c in hits[keep:]:
                calm(by_cid[c])
            if key == CONTESTED_RULE:
                # Contest the ones nobody acted upon first: a fixed finding is rarely
                # contested, and turning one would bend the reviewer's precision.
                spare = sorted((c for c in cids if c in by_cid and c not in hits),
                               key=lambda c: bool(by_cid[c]["fingerprint_gone_at"]
                                                  or any(r["label"] == "fixed"
                                                         for r in by_cid[c]["replies"])))
                for c in spare[:keep - len(hits)]:
                    e = by_cid[c]
                    e["replies"].append({"user": "dave-demo", "role": "other",
                                         "ts": e["first_signal_at"] or _gh(self.now),
                                         "label": label_reply(REPLIES_CONTEST[0]),
                                         "contests": True})
        for cid, e in by_cid.items():
            if contested(e):
                f = posted[cid]
                self.triage.append({
                    "schema_version": 1, "ts": _iso(self.now - timedelta(hours=2)),
                    "kind": "finding", "repo": REPO, "pr": e["pr"], "comment_id": cid,
                    "reviewer": f["reviewer"], "rule_id": f["rule_id"],
                    "cause": self.rng.choice(["false_positive", "false_positive",
                                              "missing_context", "preexisting",
                                              "unimportant", "question"]),
                    "confidence": self.rng.randint(55, 95), "model": SONNET,
                    "run_id": "demo-triage"})
        self.triage.append({"schema_version": 1, "ts": _iso(self.now - timedelta(hours=2)),
                            "kind": "patterns", "repo": None, "run_id": "demo-triage",
                            "misses": 14, "threads": 9, "calls": 1, "cost_usd": 0.12,
                            "model": SONNET,
                            "patterns": [{"count": 5, "example_comment_ids": [9001, 9002]},
                                         {"count": 3, "example_comment_ids": [9003]},
                                         {"count": 2, "example_comment_ids": [9004]}]})

    # ---- control group
    def unreviewed_pr(self, number: int, created: datetime) -> None:
        rng = self.rng
        add, dele, _ = self.size()
        # A little faster than reviewed PRs (no second pass), with more human comments.
        merged_at = created + timedelta(hours=rng.uniform(8, 34) + (add + dele) / 30)
        if merged_at > self.now:
            return
        first = created + timedelta(hours=rng.uniform(2, 20))
        self.prs.append({
            "schema_version": 1, "ts": _iso(self.now - timedelta(hours=3)), "pr": number,
            "state": "merged", "merged_at": _gh(merged_at), "author": self.dev(),
            "head_sha": "h", "commits_after_first_review": rng.randint(0, 2),
            "human_comments": rng.randint(1, 6), "human_replies": rng.randint(0, 3),
            "human_comments_on_silent_lines": 0, "human_comments_on_dropped_findings": 0,
            "reviewed": False, "control_group": True, "created_at": _gh(created),
            "first_human_review_at": _gh(first) if rng.random() < 0.8 else None,
            "human_reviews": rng.randint(1, 3), "first_bot_run_at": None,
            "additions": None, "deletions": None, "changed_files": None})

    # ---- two years of history
    def history_rows(self) -> None:
        rng = self.rng
        start = self.now - timedelta(days=730)
        eras = [(n, datetime.fromisoformat(d).replace(tzinfo=UTC), lbl, bot)
                for n, d, lbl, bot in ERAS]
        number, t = 200, start
        while t < self.now - timedelta(hours=6):
            t += timedelta(hours=rng.expovariate(1 / 26))     # ~28 PRs a month
            if t >= self.now:
                break
            number += 1
            era = next((e for e in reversed(eras) if e[1] <= t), None)
            name = era[0] if era else "before"
            # v0 sped reviews up a little; the pause lost part of it.
            speed = {"before": 1.0, "prototype": 0.8, "pause": 0.9}.get(name, 0.75)
            add, dele, files = self.size()
            created = self.work_time(t)
            merged = rng.random() < 0.9
            merge_h = (rng.uniform(5, 40) + (add + dele) / 18) * speed
            merged_at = created + timedelta(hours=merge_h)
            if merged_at > self.now:
                merged = False
            branch = rng.choices(["feature/", "fix/", "bugfix/", "hotfix/", "chore/",
                                  "revert-"], [0.55, 0.2, 0.06, 0.05, 0.11, 0.03])[0]
            head = (f"{branch}{number - 1}-x" if branch == "revert-"
                    else f"{branch}MOB-{rng.randint(100, 999)}-change")
            author = self.dev()
            people = [d for d, _ in DEVS if d != author]
            reviewers = rng.sample(people, rng.choice([1, 1, 2, 2, 3]))
            first_at = created + timedelta(hours=rng.uniform(1, 20) * speed)
            review_authors = {p: rng.choice([1, 1, 2]) for p in reviewers}
            first_by = {p: first_at + timedelta(hours=rng.uniform(0, 6) * k)
                        for k, p in enumerate(reviewers)}
            threads = {p: rng.choice([0, 0, 1, 2]) for p in reviewers}
            labels = []
            if era and era[2] and name != "ReviewMe" and rng.random() < 0.6:
                labels.append(era[2])
                bot = era[3]
                if bot:                           # the prototype, under a person's login
                    review_authors[bot] = rng.choice([1, 2])
                    first_by[bot] = created + timedelta(minutes=rng.randint(6, 40))
                    threads[bot] = rng.choice([1, 2, 3])
            human_reviews = sum(review_authors.values())
            human_threads = sum(threads.values())
            self.history.append({
                "schema_version": 1, "ts": _iso(self.now - timedelta(hours=5)),
                "repo": REPO, "pr": number, "author": author, "author_is_bot": False,
                "created_at": _gh(created), "merged_at": _gh(merged_at) if merged else None,
                "closed_at": _gh(merged_at),
                "state": "merged" if merged else "closed", "is_draft": False,
                "base_ref": "main", "head_ref": head, "additions": add, "deletions": dele,
                "changed_files": files, "commits": rng.randint(1, 8),
                "human_reviews": human_reviews,
                "first_human_review_at": _gh(min(first_by.values())) if first_by else None,
                "review_threads": human_threads, "bot_threads": 0,
                "human_threads": human_threads,
                "review_authors": dict(sorted(review_authors.items())),
                "thread_authors": {k: v for k, v in sorted(threads.items()) if v},
                "review_first_at": {k: _gh(v) for k, v in sorted(first_by.items())},
                "issue_comments": rng.randint(0, 4), "labels": labels,
                "is_revert": is_revert_branch(head), "is_hotfix_branch": is_hotfix_branch(head),
                "is_fix_branch": is_fix_branch(head), "bot_reviewed": bool(labels)})
            if rng.random() < 0.06:               # a dependency bot, left out by the page
                number += 1
                self.history.append({**self.history[-1], "pr": number,
                                     "author": "renovate[bot]", "author_is_bot": True,
                                     "head_ref": "renovate/deps", "labels": []})

    def bug_months(self) -> list[dict]:
        out, d = [], (self.now - timedelta(days=730)).date().replace(day=1)
        while d <= self.now.date():
            v0 = d >= date(2026, 3, 1)
            out.append({"schema_version": 1, "ts": _iso(self.now - timedelta(hours=5)),
                        "projects": "MOB", "month": f"{d.year}-{d.month:02d}",
                        "bugs": max(0, int(self.rng.gauss(9 if v0 else 12, 3)))})
            d = (d + timedelta(days=32)).replace(day=1)
        return out


def generate(root: Path, *, seed: int = 1, days: int = 90,
             now: datetime | None = None) -> dict:
    """Write the synthetic ledger under `root` (and `root/../stats.json`'s content is
    returned under `stats`). Returns counters, for the CLI and the tests."""
    now = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    g = _Gen(seed, days, now)
    change = g.start + timedelta(days=days * 0.55)      # one settings change mid-window

    def config_of(at: datetime) -> str:
        return "demo-cfg-a" if at < change else "demo-cfg-b"

    # ~70 reviewed and ~40 unreviewed PRs, interleaved by creation date.
    number = 1200
    reviewed = unreviewed = 0
    span = timedelta(days=days - 1)
    for _ in range(112):
        number += 1
        created = g.work_time(g.start + span * g.rng.random())
        if g.rng.random() < 0.63:
            g.reviewed_pr(number, created, config_of)
            reviewed += 1
        else:
            g.unreviewed_pr(number, created)
            unreviewed += 1
    g.shape_contests()
    g.history_rows()
    # A baseline run on a few PRs (the tuning page compares it with live).
    for r in [r for r in g.runs if r["reviewer"] == "tech"][:12:3]:
        g.runs.append({**r, "run_id": r["run_id"] + "-b", "variant": "baseline",
                       "dry_run": True})
        g.findings.extend({**f, "run_id": r["run_id"] + "-b", "variant": "baseline",
                           "dry_run": True, "comment_id": None}
                          for f in g.findings if f["run_id"] == r["run_id"]
                          and f["reviewer"] == "tech")
    # A `remember` request, from someone with write access.
    first = next(e for e in g.enrich if e["replies"])
    first["replies"][0]["remember"] = {"trusted": True}

    def by_ts(rows: list[dict]) -> list[dict]:
        return sorted(rows, key=lambda r: (r.get("ts") or "", str(r.get("run_id", ""))))

    write_part(root, RUNS_FILE, by_ts(g.runs))
    write_part(root, FINDINGS_FILE, by_ts(g.findings))
    write_part(root, ENRICH_FILE, g.enrich)
    write_part(root, PRS_FILE, g.prs)
    write_part(root, TRIAGE_FILE, g.triage)
    write_part(root, HISTORY_FILE, g.history)
    write_part(root, HISTORY_BUGS_FILE, g.bug_months())
    for chash, ts in (("demo-cfg-a", g.start), ("demo-cfg-b", change)):
        snap = root / SNAPSHOT_DIR / chash
        snap.mkdir(parents=True, exist_ok=True)
        (snap / "manifest.json").write_text(json.dumps(
            {"config_hash": chash, "project": "demo", "ts": _iso(ts),
             "files": {"project.toml": chash[-1] * 16}}, indent=1), encoding="utf-8")
    return {"now": now, "prs_reviewed": reviewed, "prs_unreviewed": unreviewed,
            "runs": len(g.runs), "findings": len(g.findings), "history": len(g.history),
            "stats": {"runs": g.stats_runs}}


PROJECT_TOML = """\
# Demo project written by `reviewme demo-data`: the dashboard reads [stats] from here.
[review]
language = "{language}"

[reviewers]
core = ["tech"]

[stats]
window = "{days}d"
control_group = true

[stats.roi]
hourly_cost = 75
currency = "EUR"
usd_rate = 0.92

[[stats.eras]]
name = "prototype"
since = "2026-03-10"
label = "ai-review-poc"
bot_login = "{maintainer}"

[[stats.eras]]
name = "pause"
since = "2026-10-09"

[[stats.eras]]
name = "ReviewMe"
since = "2026-11-03"
label = "review-me"
"""


def build_demo(out: Path, *, seed: int = 1, days: int = 90, language: str = "en",
               now: datetime | None = None) -> dict:
    """Ledger in `out/ledger`, `out/stats.json`, and `out/dashboard-demo-<language>.html`
    built through a real `project.toml` (in a temporary config home)."""
    from .dashboard import write
    from .dashboard.settings import StatsSettings
    from .projects import load_project_settings

    out = Path(out)
    ledger = out / "ledger"
    if ledger.exists():
        import shutil
        shutil.rmtree(ledger)         # a demo is regenerated whole, never appended to
    r = generate(ledger, seed=seed, days=days, now=now)
    stats = out / "stats.json"
    stats.write_text(json.dumps(r.pop("stats"), indent=1), encoding="utf-8")
    with tempfile.TemporaryDirectory() as home:
        pdir = Path(home) / "projects" / "demo"
        pdir.mkdir(parents=True)
        (pdir / "project.toml").write_text(PROJECT_TOML.format(
            language=language, days=days, maintainer=MAINTAINER), encoding="utf-8")
        project = load_project_settings("demo", Path(home) / "projects")
        settings = StatsSettings.from_project(project)
        page = write(out / f"dashboard-demo-{language}.html", ledger, settings,
                     project.language, stats, now=r["now"])
    return {**r, "page": page}
