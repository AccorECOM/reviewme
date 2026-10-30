"""ReviewMe dashboard — read-only, 127.0.0.1 only.

Security invariants:
- REMOVED: the `Access-Control-Allow-Origin: *` header (it let any browser tab read the
  internal data cross-origin).
- REMOVED: the unauthenticated WRITE endpoint `POST /api/requeue/<pr>` (CSRF + budget
  burn). The dashboard is now strictly read-only.
- Bind to 127.0.0.1 only. To expose it further: reverse proxy + authentication (not
  provided here — see doc §Security).
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict
from datetime import UTC, date, datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

WEB_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
REVIEWS_DIR = DATA_DIR / "reviews"
STATE_FILE = DATA_DIR / "state.json"
LOG_FILE = DATA_DIR / "reviews.log"

# --- Sprint configuration of the legacy Hall of Fame (`/api/hall-of-fame`, reviews/ files) ---
# Overridable: REVIEWME_SPRINT_ANCHOR_DATE, _NUM, _LENGTH_DAYS. The ledger dashboard
# (`/dashboard`) reads `[stats] sprint_*` in project.toml instead; these variables stay as
# the fallback of this page only. The arithmetic is shared (`dashboard.data`).
_DEFAULT_ANCHOR_DATE = date(2026, 1, 5)
_DEFAULT_ANCHOR_NUM = 1
_DEFAULT_LENGTH_DAYS = 14


def _sprint_config() -> tuple[date, int, int]:
    """Read at use time (after .env is loaded); an invalid value falls back to the default."""
    env = os.environ
    try:
        anchor = date.fromisoformat(env.get("REVIEWME_SPRINT_ANCHOR_DATE", "") or _DEFAULT_ANCHOR_DATE.isoformat())
    except ValueError:
        print("REVIEWME_SPRINT_ANCHOR_DATE: expected YYYY-MM-DD, using the default", file=sys.stderr)
        anchor = _DEFAULT_ANCHOR_DATE
    try:
        num = int(env.get("REVIEWME_SPRINT_ANCHOR_NUM", "") or _DEFAULT_ANCHOR_NUM)
        length = int(env.get("REVIEWME_SPRINT_LENGTH_DAYS", "") or _DEFAULT_LENGTH_DAYS)
    except ValueError:
        print("REVIEWME_SPRINT_ANCHOR_NUM / _LENGTH_DAYS: expected integers, using the defaults", file=sys.stderr)
        num, length = _DEFAULT_ANCHOR_NUM, _DEFAULT_LENGTH_DAYS
    if length < 1:
        print("REVIEWME_SPRINT_LENGTH_DAYS must be >= 1, using the default", file=sys.stderr)
        length = _DEFAULT_LENGTH_DAYS
    return anchor, num, length


_FR_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_REVIEW_FILE_RE = re.compile(r"_(\d{8})_(\d{6})\.json$")


def _sprint_num_for_date(d: date) -> int:
    from ..dashboard.data import sprint_number
    return sprint_number(d, *_sprint_config())


def _sprint_bounds(num: int) -> tuple[date, date]:
    from ..dashboard.data import sprint_bounds
    return sprint_bounds(num, *_sprint_config())


def _fmt_day(d: date) -> str:
    return f"{d.day} {_FR_MONTHS[d.month - 1]}"


def _sprint_label(num: int) -> str:
    start, end = _sprint_bounds(num)
    period = (f"{start.day} → {end.day} {_FR_MONTHS[end.month - 1]}"
              if start.month == end.month else f"{_fmt_day(start)} → {_fmt_day(end)}")
    return f"Sprint {num} — {period}"


def _review_datetime(filename: str) -> datetime | None:
    m = _REVIEW_FILE_RE.search(filename)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def build_dashboard(ledger_root: Path | None = None) -> tuple[str, dict]:
    """The ledger dashboard, rebuilt on every request: (HTML, per-section results).

    Rebuilt rather than cached: a few thousand JSONL lines take milliseconds, and a page
    that is always fresh needs no invalidation. Settings and language come from the
    active project (`[stats]`), or the defaults.
    """
    from ..dashboard import build
    from ..dashboard.settings import active_settings
    settings, language = active_settings()
    root = ledger_root or Path(os.environ.get("REVIEWME_LEDGER_DIR", "").strip()
                               or DATA_DIR / "ledger").expanduser()
    return build(root, settings, language, DATA_DIR / "stats.json")


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/state":
            self._json(self._get_state())
        elif parsed.path == "/api/reviews":
            self._json(self._get_reviews())
        elif parsed.path == "/api/stats":
            # Aggregated counters: the only source available when detailed reviews are
            # absent (CI runner, history carried over by a cache).
            from ..stats import resume
            self._json(resume())
        elif parsed.path == "/api/logs":
            self._json(self._get_logs())
        elif parsed.path == "/dashboard":
            self._html(build_dashboard()[0])
        elif parsed.path == "/api/dashboard":
            self._json(build_dashboard()[1])
        elif parsed.path == "/api/hall-of-fame":
            sprint = parse_qs(parsed.query).get("sprint", [None])[0]
            self._json(self._get_hall_of_fame(sprint))
        else:
            super().do_GET()

    # Read-only: no do_POST (the requeue write endpoint was removed).

    def _html(self, page: str) -> None:
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _get_state(self) -> dict:
        if STATE_FILE.exists():
            try:
                return json.loads(STATE_FILE.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        return {"reviewed": {}, "errors": {}}

    def _get_reviews(self) -> list[dict]:
        reviews = []
        if REVIEWS_DIR.exists():
            for f in sorted(REVIEWS_DIR.glob("pr_*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
                try:
                    reviews.append(json.loads(f.read_text(encoding="utf-8")))
                except json.JSONDecodeError:
                    continue
        return reviews

    def _get_dated_reviews(self) -> list[tuple[datetime | None, dict]]:
        dated = []
        if REVIEWS_DIR.exists():
            for f in sorted(REVIEWS_DIR.glob("pr_*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
                try:
                    dated.append((_review_datetime(f.name), json.loads(f.read_text(encoding="utf-8"))))
                except json.JSONDecodeError:
                    continue
        return dated

    def _get_logs(self) -> list[dict]:
        logs = []
        if LOG_FILE.exists():
            for line in LOG_FILE.read_text(encoding="utf-8").strip().splitlines():
                try:
                    logs.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return list(reversed(logs))

    def _get_hall_of_fame(self, sprint: str | None = None) -> dict:
        dated = self._get_dated_reviews()
        sprint_nums = sorted({_sprint_num_for_date(dt.date()) for dt, _ in dated if dt is not None}, reverse=True)
        current_sprint = _sprint_num_for_date(datetime.now(UTC).date())

        sprints_meta = [{"id": "all", "label": "All time", "num": None, "is_current": False}]
        for n in sprint_nums:
            start, end = _sprint_bounds(n)
            sprints_meta.append({"id": str(n), "num": n, "label": _sprint_label(n),
                                 "start": start.isoformat(), "end": end.isoformat(),
                                 "is_current": n == current_sprint})

        if sprint is None:
            sprint = str(sprint_nums[0]) if sprint_nums else "all"

        if sprint == "all":
            reviews = [r for _, r in dated]
        else:
            try:
                target = int(sprint)
            except (TypeError, ValueError):
                target = None
            reviews = [r for dt, r in dated if dt is not None and _sprint_num_for_date(dt.date()) == target]

        result = self._compute_hof(reviews)
        result["sprints"] = sprints_meta
        result["selected"] = sprint
        return result

    def _compute_hof(self, reviews: list[dict]) -> dict:
        if not reviews:
            return {"total": {}, "categories": [], "dev_leaderboard": []}

        total_cost = sum(r.get("cost_usd", 0) for r in reviews)
        total_tokens = sum(r.get("input_tokens", 0) + r.get("output_tokens", 0) for r in reviews)
        total_duration = sum(r.get("duration_ms", 0) for r in reviews)
        total_stats = {
            "review_count": len(reviews),
            "pr_count": len({r["pr_number"] for r in reviews}),
            "dev_count": len({r["pr_author"] for r in reviews if r.get("pr_author")}),
            "total_cost": round(total_cost, 2),
            "total_tokens": total_tokens,
            "total_duration_h": round(total_duration / 3_600_000, 1),
        }

        author_costs: dict[str, float] = defaultdict(float)
        author_reviews: dict[str, int] = defaultdict(int)
        author_prs: dict[str, set] = defaultdict(set)
        author_tokens: dict[str, int] = defaultdict(int)
        author_duration: dict[str, int] = defaultdict(int)
        author_diffs: dict[str, list] = defaultdict(list)
        author_max_rereviews: dict[str, tuple] = {}
        pr_reviews_by_author: dict[str, dict] = defaultdict(lambda: defaultdict(int))

        for r in reviews:
            author = r.get("pr_author", "")
            if not author:
                continue
            author_costs[author] += r.get("cost_usd", 0)
            author_reviews[author] += 1
            author_prs[author].add(r["pr_number"])
            author_tokens[author] += r.get("input_tokens", 0) + r.get("output_tokens", 0)
            author_duration[author] += r.get("duration_ms", 0)
            author_diffs[author].append(r.get("diff_size_kb", 0))
            pr_reviews_by_author[author][r["pr_number"]] += 1

        for author, pr_counts in pr_reviews_by_author.items():
            max_pr = max(pr_counts, key=pr_counts.get)
            author_max_rereviews[author] = (pr_counts[max_pr], max_pr)

        if not author_costs:
            return {"total": total_stats, "categories": [], "dev_leaderboard": []}

        def dev_name(login: str) -> str:
            """Display login. `LOGIN_SUFFIX_STRIP` strips an organization suffix
            (e.g. `-acme` on `alice-acme`) when the internal convention adds one.
            Empty by default: the login is shown as-is."""
            suffix = os.environ.get("LOGIN_SUFFIX_STRIP", "")
            if suffix and login.lower().endswith(suffix.lower()):
                return login[: -len(suffix)]
            return login

        categories = []
        top_dev = max(author_costs, key=author_costs.get)
        categories.append({"id": "golden-dev", "title": "The Golden Dev", "subtitle": "The most expensive dev",
                           "icon": "gold", "value": f"${author_costs[top_dev]:.2f}", "detail": f"@{dev_name(top_dev)}",
                           "label": f"{len(author_prs[top_dev])} PRs | {author_reviews[top_dev]} reviews",
                           "extra": f"~${author_costs[top_dev] / len(author_prs[top_dev]):.2f}/PR"})

        qualified = {a: author_costs[a] / len(author_prs[a]) for a in author_costs if len(author_prs[a]) >= 2}
        if qualified:
            worst = max(qualified, key=qualified.get)
            categories.append({"id": "money-pit", "title": "The Money Pit", "subtitle": "Most expensive on average per PR",
                               "icon": "fire", "value": f"${qualified[worst]:.2f}/PR", "detail": f"@{dev_name(worst)}",
                               "label": f"{len(author_prs[worst])} PRs for ${author_costs[worst]:.2f} in total",
                               "extra": f"{author_reviews[worst]} reviews in total"})

        avg_rereviews = {a: author_reviews[a] / len(author_prs[a]) for a in author_costs if len(author_prs[a]) >= 2}
        if avg_rereviews:
            rec = max(avg_rereviews, key=avg_rereviews.get)
            mc, mp = author_max_rereviews[rec]
            categories.append({"id": "recidivist", "title": "The Repeat Offender", "subtitle": "Most re-reviews per PR on average",
                               "icon": "loop", "value": f"{avg_rereviews[rec]:.1f}x/PR", "detail": f"@{dev_name(rec)}",
                               "label": f"{author_reviews[rec]} reviews for {len(author_prs[rec])} PRs",
                               "extra": f"Record: PR #{mp} reviewed {mc}x"})

        avg_diff = {a: sum(author_diffs[a]) / len(author_diffs[a]) for a in author_diffs if len(author_diffs[a]) >= 2}
        if avg_diff:
            mam = max(avg_diff, key=avg_diff.get)
            categories.append({"id": "mammoth", "title": "The Mammoth", "subtitle": "Biggest diffs on average",
                               "icon": "heavy", "value": f"{avg_diff[mam]:.0f} KB/review", "detail": f"@{dev_name(mam)}",
                               "label": f"Record: {max(author_diffs[mam]):.0f} KB in one review",
                               "extra": f"{len(author_prs[mam])} PRs | ${author_costs[mam]:.2f} in total"})

        chrono = max(author_duration, key=author_duration.get)
        categories.append({"id": "time-sink", "title": "The Time Sink", "subtitle": "The dev who kept the AI running the longest",
                           "icon": "clock", "value": f"{author_duration[chrono] / 3_600_000:.1f}h", "detail": f"@{dev_name(chrono)}",
                           "label": f"{author_reviews[chrono]} reviews | {len(author_prs[chrono])} PRs",
                           "extra": f"~{author_duration[chrono] / author_reviews[chrono] / 60_000:.0f} min/review on average"})

        token_king = max(author_tokens, key=author_tokens.get)
        tok = author_tokens[token_king]
        tok_str = f"{tok / 1_000_000:.1f}M" if tok > 1_000_000 else f"{tok / 1000:.0f}k"
        categories.append({"id": "token-monster", "title": "The Token Eater", "subtitle": "The dev who consumed the most tokens",
                           "icon": "chip", "value": tok_str, "detail": f"@{dev_name(token_king)}",
                           "label": f"{author_reviews[token_king]} reviews | ${author_costs[token_king]:.2f}",
                           "extra": f"~{tok / author_reviews[token_king] / 1000:.0f}k tokens/review"})

        abonne = max(author_prs, key=lambda a: len(author_prs[a]))
        categories.append({"id": "regular", "title": "The Regular", "subtitle": "The dev with the most reviewed PRs",
                           "icon": "bolt", "value": f"{len(author_prs[abonne])} PRs", "detail": f"@{dev_name(abonne)}",
                           "label": f"${author_costs[abonne]:.2f} in total", "extra": f"{author_reviews[abonne]} reviews in total"})

        if qualified:
            best = min(qualified, key=qualified.get)
            categories.append({"id": "bargain", "title": "The Model Student", "subtitle": "Cheapest on average per PR",
                               "icon": "leaf", "value": f"${qualified[best]:.2f}/PR", "detail": f"@{dev_name(best)}",
                               "label": f"{len(author_prs[best])} PRs for ${author_costs[best]:.2f} in total",
                               "extra": f"~{author_reviews[best] / len(author_prs[best]):.1f} reviews/PR"})

        dev_leaderboard = []
        for author in sorted(author_costs, key=author_costs.get, reverse=True):
            tk = author_tokens[author]
            tks = f"{tk / 1_000_000:.1f}M" if tk > 1_000_000 else f"{tk / 1000:.0f}k"
            avg_diff_val = sum(author_diffs[author]) / len(author_diffs[author]) if author_diffs[author] else 0
            mc, mp = author_max_rereviews.get(author, (0, 0))
            dev_leaderboard.append({"author": dev_name(author), "login": author,
                                    "total_cost": round(author_costs[author], 2),
                                    "review_count": author_reviews[author], "pr_count": len(author_prs[author]),
                                    "avg_cost_per_pr": round(author_costs[author] / len(author_prs[author]), 2),
                                    "avg_reviews_per_pr": round(author_reviews[author] / len(author_prs[author]), 1),
                                    "tokens": tks, "avg_diff_kb": round(avg_diff_val),
                                    "worst_pr": mp, "worst_pr_reviews": mc})

        return {"total": total_stats, "categories": categories, "dev_leaderboard": dev_leaderboard}

    def log_message(self, format, *args):
        pass


def main() -> None:
    from dotenv import load_dotenv
    load_dotenv(DATA_DIR.parent / ".env")   # REVIEWME_SPRINT_*, same file as the engine
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print(__doc__.strip())
        print("\\nUsage: reviewme-web [port]   (default 8420, listens on 127.0.0.1)")
        return

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8420
    server = HTTPServer(("127.0.0.1", port), DashboardHandler)
    print(f"ReviewMe Dashboard (read-only): http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
