"""Hall of Fame: the fun page, the one people actually open on a Friday.

The eight categories of the local dashboard (`web/server.py`), rebuilt on the ledger so
they work on a CI runner too, plus four only the ledger can tell. Runs have no author:
it comes from `prs.jsonl` (enrich), then `stats.json`, else `?` (`Context.author_of`).

Rule of the house: titles are about cost, volume and participation — never about the
quality of someone's code. "Most expensive" is a joke about the bot's bill; "most
findings received" would be a public ranking of bugs, and has no place here.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from .. import charts as C
from .. import readings as R
from ..data import UNKNOWN, Context, cost_of
from ..i18n import plural, texts

ID = "hall_of_fame"
TITLE = {"en": "Hall of Fame", "fr": "Hall of Fame"}
MIN_PRS = 2                  # averages per PR mean nothing on a single PR

#: id -> (title, subtitle). Keep the ids: they are the stable API of `/api/dashboard`.
_TITLES = {
    "en": {
        "golden-dev": ("The Golden Dev", "Kept the meter running the most"),
        "money-pit": ("The Money Pit", "Priciest PRs on average"),
        "recidivist": ("The Repeat Customer", "Most reviews per PR on average"),
        "mammoth": ("The Mammoth", "Biggest diffs on average"),
        "time-sink": ("The Time Bender", "Kept the bot busy the longest"),
        "token-monster": ("The Token Monster", "Fed the model the most tokens"),
        "regular": ("The Regular", "Most PRs reviewed"),
        "bargain": ("The Bargain Hunter", "Cheapest PRs on average"),
        "tamer": ("The Bot Tamer", "Talked back to the bot the most"),
        "chosen-one": ("The Chosen One", "BLOCKER dodged before the merge"),
        "ghost": ("The Silent Type", "Reviewed PRs, not a single reaction"),
        "serial-pusher": ("The Serial Pusher", "Most reviews on a single PR"),
    },
    "fr": {
        "golden-dev": ("Le Dev en Or", "A fait tourner le compteur le plus fort"),
        "money-pit": ("Le Gouffre", "Les PR les plus chères en moyenne"),
        "recidivist": ("Le Récidiviste", "Le plus de relectures par PR en moyenne"),
        "mammoth": ("Le Mammouth", "Les plus gros diffs en moyenne"),
        "time-sink": ("Le Dévoreur de Temps", "A occupé le bot le plus longtemps"),
        "token-monster": ("L'Ogre à Tokens", "A le plus nourri le modèle"),
        "regular": ("L'Habitué", "Le plus de PR relues"),
        "bargain": ("Le Bon Plan", "Les PR les moins chères en moyenne"),
        "tamer": ("Le Dompteur", "A le plus répondu du tac au tac au bot"),
        "chosen-one": ("L'Élu", "BLOCKER évité avant le merge"),
        "ghost": ("Le Discret", "Des PR relues, pas une seule réaction"),
        "serial-pusher": ("Le Pousseur en Série", "Le plus de relectures sur une seule PR"),
    },
}

_T = {
    "en": {
        "prs_runs": "{prs} PRs · {runs} reviews", "per_pr": "{v}/PR",
        "x_per_pr": "{v}× per PR", "lines": "{v} lines per review",
        "record_pr": "record: PR #{pr}, {n} reviews", "hours": "{v} h", "min": "{v} min",
        "tokens": "{v} tokens", "prs": "PRs", "n_prs": "{n} PRs", "n_prs_one": "{n} PR",
        "pushback": "{n} pushbacks", "pushback_one": "{n} pushback",
        "blockers": "{n} BLOCKERs", "blockers_one": "{n} BLOCKER", "zero": "0 reactions",
        "reviews": "{n} reviews", "reviews_one": "{n} review",
        "prs_runs_one": "{prs} PR · {runs} reviews",
        "leaderboard": "Leaderboard", "avg_lines": "Lines/review", "per_pr_col": "Cost/PR",
        "runs_pr": "Reviews/PR", "tokens_col": "Tokens", "record": "Record PR",
        "none": "Not enough reviews in this window for a Hall of Fame.",
    },
    "fr": {
        "prs_runs": "{prs} PR · {runs} relectures", "per_pr": "{v}/PR",
        "x_per_pr": "{v}× par PR", "lines": "{v} lignes par relecture",
        "record_pr": "record : PR #{pr}, {n} relectures", "hours": "{v} h",
        "min": "{v} min", "tokens": "{v} tokens", "prs": "PR", "n_prs": "{n} PR",
        "n_prs_one": "{n} PR", "pushback": "{n} répliques", "pushback_one": "{n} réplique",
        "blockers": "{n} BLOCKER", "blockers_one": "{n} BLOCKER", "zero": "0 réaction",
        "reviews": "{n} relectures", "reviews_one": "{n} relecture",
        "prs_runs_one": "{prs} PR · {runs} relectures", "leaderboard": "Classement",
        "avg_lines": "Lignes/relecture", "per_pr_col": "Coût/PR", "runs_pr": "Relectures/PR",
        "tokens_col": "Tokens", "record": "PR record",
        "none": "Pas assez de relectures sur cette fenêtre pour un Hall of Fame.",
    },
}


def _tokens(n: int) -> str:
    return f"{n / 1_000_000:.1f}M" if n >= 1_000_000 else f"{n / 1000:.0f}k"


def compute(ctx: Context) -> dict:
    """Per author (login internally, printed through `name_of` only at the end)."""
    a: dict[str, dict] = defaultdict(lambda: {"cost": 0.0, "runs": 0, "prs": set(),
                                              "tokens": 0, "duration_s": 0.0, "lines": [],
                                              "per_pr": Counter()})
    for rows in ctx.run_groups().values():
        pr = rows[0].get("pr")
        login = ctx.author_login(pr)
        if login == UNKNOWN:
            continue
        d = a[login]
        d["cost"] += cost_of(rows)
        d["runs"] += 1
        d["prs"].add(pr)
        d["tokens"] += sum((r.get("input_tokens", 0) or 0) + (r.get("output_tokens", 0) or 0)
                           for r in rows)
        # Reviewers run side by side: a run lasts as long as its slowest reviewer.
        d["duration_s"] += max((r.get("duration_s", 0) or 0) for r in rows)
        d["lines"].append(max((r.get("diff_lines", 0) or 0) for r in rows))
        d["per_pr"][pr] += 1
    if not a:
        return {"categories": [], "leaderboard": []}

    # Ledger-only signals: pushback as PR author, BLOCKERs fixed in time, participation.
    pushback: Counter = Counter()
    active: set[str] = set()
    for e in ctx.enrich.values():
        for r in e.get("reactions", []) + e.get("replies", []):
            if r.get("user"):
                active.add(r["user"])
        for r in e.get("replies", []):
            if r.get("contests") and r.get("role") == "pr_author" and r.get("user"):
                pushback[r["user"]] += 1
    chosen: Counter = Counter(ctx.author_login(j.get("pr")) for j in ctx.judged
                              if j.get("severity") == "BLOCKER" and ctx.acted_before_merge(j))
    chosen.pop(UNKNOWN, None)

    def avg_pr(login: str) -> float:
        return a[login]["cost"] / len(a[login]["prs"])

    def runs_pr(login: str) -> float:
        return a[login]["runs"] / len(a[login]["prs"])

    def avg_lines(login: str) -> float:
        return sum(a[login]["lines"]) / len(a[login]["lines"])

    def record(login: str) -> tuple[int, int]:
        pr, n = a[login]["per_pr"].most_common(1)[0]
        return n, pr

    def best(candidates, key, reverse=True):
        """Highest (or lowest) `key`; ties broken by login so a render is stable."""
        pool = sorted(candidates)
        return (max if reverse else min)(pool, key=key) if pool else None

    cats: list[dict] = []

    def add(cid: str, login: str | None, value: str, detail: str, raw: float) -> None:
        if login is not None:
            cats.append({"id": cid, "who": ctx.name_of(login), "value": value,
                         "detail": detail, "raw": round(raw, 4)})

    multi = [x for x in a if len(a[x]["prs"]) >= MIN_PRS]
    T = texts(_T, ctx.language)

    w = best(a, lambda x: a[x]["cost"])
    add("golden-dev", w, C.money(a[w]["cost"]),
        plural(T, "prs_runs", len(a[w]["prs"]), prs=len(a[w]["prs"]), runs=a[w]["runs"]),
        a[w]["cost"])
    w = best(multi, avg_pr)
    if w:
        add("money-pit", w, T["per_pr"].format(v=C.money(avg_pr(w))),
            plural(T, "n_prs", len(a[w]["prs"])), avg_pr(w))
    w = best(multi, runs_pr)
    if w:
        n, pr = record(w)
        add("recidivist", w, T["x_per_pr"].format(v=f"{runs_pr(w):.1f}"),
            T["record_pr"].format(pr=pr, n=n), runs_pr(w))
    w = best([x for x in a if len(a[x]["lines"]) >= MIN_PRS], avg_lines)
    if w:
        add("mammoth", w, T["lines"].format(v=f"{avg_lines(w):.0f}"),
            plural(T, "reviews", a[w]["runs"]), avg_lines(w))
    w = best(a, lambda x: a[x]["duration_s"])
    dur = a[w]["duration_s"]
    add("time-sink", w, T["hours"].format(v=f"{dur / 3600:.1f}") if dur >= 3600
        else T["min"].format(v=f"{dur / 60:.0f}"), plural(T, "reviews", a[w]["runs"]), dur)
    w = best(a, lambda x: a[x]["tokens"])
    add("token-monster", w, T["tokens"].format(v=_tokens(a[w]["tokens"])),
        plural(T, "reviews", a[w]["runs"]), a[w]["tokens"])
    w = best(a, lambda x: len(a[x]["prs"]))
    add("regular", w, plural(T, "n_prs", len(a[w]["prs"])),
        plural(T, "reviews", a[w]["runs"]), len(a[w]["prs"]))
    # With a single qualified author, the priciest and the cheapest are the same person.
    w = best(multi, avg_pr, reverse=False) if len(multi) >= 2 else None
    if w:
        add("bargain", w, T["per_pr"].format(v=C.money(avg_pr(w))),
            plural(T, "n_prs", len(a[w]["prs"])), avg_pr(w))
    w = best(pushback, lambda x: pushback[x])
    if w:
        add("tamer", w, plural(T, "pushback", pushback[w]), "", pushback[w])
    w = best(chosen, lambda x: chosen[x])
    if w:
        add("chosen-one", w, plural(T, "blockers", chosen[w]), "", chosen[w])
    w = best([x for x in a if x not in active], lambda x: len(a[x]["prs"]))
    if w:
        add("ghost", w, T["zero"], plural(T, "n_prs", len(a[w]["prs"])), len(a[w]["prs"]))
    w = best(a, lambda x: record(x)[0])
    n, pr = record(w)
    add("serial-pusher", w, plural(T, "reviews", n), f"PR #{pr}", n)

    leaderboard = [{"dev": ctx.name_of(x), "prs": len(d["prs"]), "runs": d["runs"],
                    "cost_usd": round(d["cost"], 4), "cost_per_pr": round(avg_pr(x), 4),
                    "runs_per_pr": round(runs_pr(x), 1), "tokens": d["tokens"],
                    "avg_diff_lines": round(avg_lines(x)), "record_pr": record(x)[1],
                    "record_runs": record(x)[0]}
                   for x, d in sorted(a.items(), key=lambda kv: (-kv[1]["cost"], kv[0]))]
    return {"categories": cats, "leaderboard": leaderboard}


def render(res: dict, ctx: Context) -> str:
    T = texts(_T, ctx.language)
    titles = _TITLES.get(ctx.language, _TITLES["en"])
    if not res["categories"]:
        return C.card(C.empty(T["none"]))
    cards = []
    for c in res["categories"]:
        name, sub = titles[c["id"]]
        detail = f'<div class="s">{C.esc(c["detail"])}</div>' if c["detail"] else ""
        cards.append(f'<div class="tile" id="hof-{C.esc(c["id"])}">'
                     f'<div class="t">{C.esc(name)}</div><div class="s">{C.esc(sub)}</div>'
                     f'<div class="who">@{C.esc(c["who"])}</div>'
                     f'<div class="n">{C.esc(c["value"])}</div>{detail}</div>')
    board = C.table([T["dev"], T["prs"], T["runs"], T["cost"], T["per_pr_col"], T["runs_pr"],
                     T["tokens_col"], T["avg_lines"], T["record"]],
                    [(r["dev"], r["prs"], r["runs"], C.money(r["cost_usd"]),
                      C.money(r["cost_per_pr"]), r["runs_per_pr"], _tokens(r["tokens"]),
                      r["avg_diff_lines"], f'#{r["record_pr"]} ({r["record_runs"]})')
                     for r in res["leaderboard"]], right=(1, 2, 3, 4, 5, 6, 7, 8))
    return f'<div class="hof">{"".join(cards)}</div>' + C.card(
        board, T["leaderboard"], reading=R.top(
            T["prs"], [(r["dev"], r["prs"]) for r in res["leaderboard"]], C.num, ctx.language))
