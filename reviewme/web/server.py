"""Dashboard ReviewMe — read-only, 127.0.0.1 uniquement.

Invariants de sécurité :
- SUPPRIMÉ : header `Access-Control-Allow-Origin: *` (permettait à n'importe quel onglet
  navigateur de lire les données internes en cross-origin).
- SUPPRIMÉ : l'endpoint d'ÉCRITURE non authentifié `POST /api/requeue/<pr>` (CSRF +
  burn de budget). Le dashboard est désormais strictement lecture seule.
- Bind 127.0.0.1 uniquement. Pour exposer au-delà : reverse-proxy + authentification
  (non fournis ici — cf. doc §Sécurité).
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

# --- Configuration des sprints (ancre : sprint 89 = lundi 2026-05-25, cycles de 14j) ---
SPRINT_ANCHOR_DATE = date(2026, 5, 25)
SPRINT_ANCHOR_NUM = 89
SPRINT_LENGTH_DAYS = 14

_FR_MONTHS = ["janv", "févr", "mars", "avr", "mai", "juin",
              "juil", "août", "sept", "oct", "nov", "déc"]
_REVIEW_FILE_RE = re.compile(r"_(\d{8})_(\d{6})\.json$")


def _sprint_num_for_date(d: date) -> int:
    return SPRINT_ANCHOR_NUM + (d - SPRINT_ANCHOR_DATE).days // SPRINT_LENGTH_DAYS


def _sprint_bounds(num: int) -> tuple[date, date]:
    from datetime import timedelta
    start = SPRINT_ANCHOR_DATE + timedelta(days=(num - SPRINT_ANCHOR_NUM) * SPRINT_LENGTH_DAYS)
    return start, start + timedelta(days=SPRINT_LENGTH_DAYS - 1)


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
            # Compteurs agrégés : seule source disponible quand les reviews détaillées
            # ne sont pas là (runner de CI, historique transporté par un cache).
            from ..stats import resume
            self._json(resume())
        elif parsed.path == "/api/logs":
            self._json(self._get_logs())
        elif parsed.path == "/api/hall-of-fame":
            sprint = parse_qs(parsed.query).get("sprint", [None])[0]
            self._json(self._get_hall_of_fame(sprint))
        else:
            super().do_GET()

    # Lecture seule : aucun do_POST (l'endpoint d'écriture requeue a été retiré).

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

        sprints_meta = [{"id": "all", "label": "Depuis le début", "num": None, "is_current": False}]
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
            """Login d'affichage. `LOGIN_SUFFIX_STRIP` retire un suffixe d'organisation
            (ex. `-acme` sur `alice-acme`) quand la convention interne en ajoute un.
            Vide par défaut : le login est affiché tel quel."""
            suffix = os.environ.get("LOGIN_SUFFIX_STRIP", "")
            if suffix and login.lower().endswith(suffix.lower()):
                return login[: -len(suffix)]
            return login

        categories = []
        top_dev = max(author_costs, key=author_costs.get)
        categories.append({"id": "golden-dev", "title": "Le Dev en Or", "subtitle": "Le dev qui coute le plus cher",
                           "icon": "gold", "value": f"${author_costs[top_dev]:.2f}", "detail": f"@{dev_name(top_dev)}",
                           "label": f"{len(author_prs[top_dev])} PRs | {author_reviews[top_dev]} reviews",
                           "extra": f"~${author_costs[top_dev] / len(author_prs[top_dev]):.2f}/PR"})

        qualified = {a: author_costs[a] / len(author_prs[a]) for a in author_costs if len(author_prs[a]) >= 2}
        if qualified:
            worst = max(qualified, key=qualified.get)
            categories.append({"id": "money-pit", "title": "Le Gouffre par PR", "subtitle": "Le plus cher en moyenne par PR",
                               "icon": "fire", "value": f"${qualified[worst]:.2f}/PR", "detail": f"@{dev_name(worst)}",
                               "label": f"{len(author_prs[worst])} PRs pour ${author_costs[worst]:.2f} au total",
                               "extra": f"{author_reviews[worst]} reviews cumulees"})

        avg_rereviews = {a: author_reviews[a] / len(author_prs[a]) for a in author_costs if len(author_prs[a]) >= 2}
        if avg_rereviews:
            rec = max(avg_rereviews, key=avg_rereviews.get)
            mc, mp = author_max_rereviews[rec]
            categories.append({"id": "recidivist", "title": "Le Recidiviste", "subtitle": "Le plus de re-reviews par PR en moyenne",
                               "icon": "loop", "value": f"{avg_rereviews[rec]:.1f}x/PR", "detail": f"@{dev_name(rec)}",
                               "label": f"{author_reviews[rec]} reviews pour {len(author_prs[rec])} PRs",
                               "extra": f"Record: PR #{mp} reviewee {mc}x"})

        avg_diff = {a: sum(author_diffs[a]) / len(author_diffs[a]) for a in author_diffs if len(author_diffs[a]) >= 2}
        if avg_diff:
            mam = max(avg_diff, key=avg_diff.get)
            categories.append({"id": "mammoth", "title": "Le Mammouth", "subtitle": "Les plus gros diffs en moyenne",
                               "icon": "heavy", "value": f"{avg_diff[mam]:.0f} KB/review", "detail": f"@{dev_name(mam)}",
                               "label": f"Record: {max(author_diffs[mam]):.0f} KB sur une review",
                               "extra": f"{len(author_prs[mam])} PRs | ${author_costs[mam]:.2f} au total"})

        chrono = max(author_duration, key=author_duration.get)
        categories.append({"id": "time-sink", "title": "Le Chronophage", "subtitle": "Le dev qui a fait tourner l'IA le plus longtemps",
                           "icon": "clock", "value": f"{author_duration[chrono] / 3_600_000:.1f}h", "detail": f"@{dev_name(chrono)}",
                           "label": f"{author_reviews[chrono]} reviews | {len(author_prs[chrono])} PRs",
                           "extra": f"~{author_duration[chrono] / author_reviews[chrono] / 60_000:.0f} min/review en moyenne"})

        token_king = max(author_tokens, key=author_tokens.get)
        tok = author_tokens[token_king]
        tok_str = f"{tok / 1_000_000:.1f}M" if tok > 1_000_000 else f"{tok / 1000:.0f}k"
        categories.append({"id": "token-monster", "title": "Le Devoreur de Tokens", "subtitle": "Le dev qui a consomme le plus de tokens",
                           "icon": "chip", "value": tok_str, "detail": f"@{dev_name(token_king)}",
                           "label": f"{author_reviews[token_king]} reviews | ${author_costs[token_king]:.2f}",
                           "extra": f"~{tok / author_reviews[token_king] / 1000:.0f}k tokens/review"})

        abonne = max(author_prs, key=lambda a: len(author_prs[a]))
        categories.append({"id": "regular", "title": "L'Abonne", "subtitle": "Le dev avec le plus de PRs reviewees",
                           "icon": "bolt", "value": f"{len(author_prs[abonne])} PRs", "detail": f"@{dev_name(abonne)}",
                           "label": f"${author_costs[abonne]:.2f} au total", "extra": f"{author_reviews[abonne]} reviews cumulees"})

        if qualified:
            best = min(qualified, key=qualified.get)
            categories.append({"id": "bargain", "title": "Le Bon Eleve", "subtitle": "Le moins cher en moyenne par PR",
                               "icon": "leaf", "value": f"${qualified[best]:.2f}/PR", "detail": f"@{dev_name(best)}",
                               "label": f"{len(author_prs[best])} PRs pour ${author_costs[best]:.2f} au total",
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
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print(__doc__.strip())
        print("\\nUsage : reviewme-web [port]   (défaut 8420, écoute sur 127.0.0.1)")
        return

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8420
    server = HTTPServer(("127.0.0.1", port), DashboardHandler)
    print(f"ReviewMe Dashboard (read-only) : http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
