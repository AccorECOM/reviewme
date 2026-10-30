"""Quality: are the findings right, and do people say so?

Built on `quality.compute` (acted upon / approved / contested on inline posted findings)
plus what it does not break down:

- **judgment by reaction**, through the project's `[stats.judgment]` vocabulary, read on
  `reactions[].content` (`enrich` keeps every GitHub reaction; rows collected before it
  did only hold 👍/👎);
- **why findings are contested**, from the reply labels — a rule contested as
  "intended" needs a project convention, one contested as "false positive" needs a fix;
- **verification yield**: what the verification pass refuted, and whether what it
  confirmed is then contested less;
- **judgment delay**: median hours from the run that posted a finding to its first human
  signal (reaction or reply). A finding answered a week later was read after the code
  moved on: its verdict says less about the review than one answered within the hour.

With `reviewme triage` rows in the window, the keyword chart gives way to the **causes
of contested findings** (one cause per finding, latest classification wins), broken down
by reviewer, and the **recurring misses** the triage grouped from human comments.

Rule ids and reviewer ids only: never a finding's text, even when the ledger has it. The
one exception is opt-in twice over: a recurring miss's title and suggested rule (model
text about the PRs) are printed only when `[ledger] include_text` is on; otherwise the
patterns show as counts.
"""
from __future__ import annotations

from collections import Counter

from ...triage import CAUSES as TRIAGE_CAUSES
from .. import charts as C
from .. import readings as R
from ..data import Context, first_signal, hours_between, median
from ..i18n import plural, texts
from ..settings import JUDGMENTS

ID = "quality"
TITLE = {"en": "Quality", "fr": "Qualité"}
TOP_RULES = 12
CAUSES = ("false_positive", "intended", "unimportant", "question", "other")

_T = {
    "en": {
        "by_reviewer": "By reviewer", "by_severity": "By severity",
        "top_rules": "Most posted rules", "judgment": "Judgment by reaction",
        "j_useful": "useful", "j_wrong": "wrong", "j_off_topic": "off topic",
        "j_saved_me": "saved me", "j_other": "other reactions",
        "judgment_note": "Reactions mapped by [stats.judgment] in project.toml.",
        "signal": "Findings with a human signal", "signal_sub": "reaction or reply",
        "delay": "median wait before a human signal",
        "delay_sub": "over {n} findings", "delay_sub_one": "over {n} finding",
        "causes": "Replies by label",
        "c_false_positive": "false positive", "c_intended": "intended",
        "c_unimportant": "not important", "c_question": "question", "c_other": "other",
        "causes_note": "Keyword labels: a rough sort, to be read as a trend.",
        "contested_rules": "Rules contested 3 times or more",
        "misses": "Approximate misses", "human_comments": "human comments",
        "silent": "where the bot said nothing", "dropped": "on a dropped finding",
        "verification": "Verification yield", "v_confirmed": "confirmed by verification",
        "v_refuted": "refuted by verification",
        "confirmed_contested": "contested among confirmed",
        "none_contested": "No rule contested 3 times yet.",
        "t_causes": "Causes of contested findings (LLM triage)",
        "t_causes_note": "reviewme triage: one cause per finding with a reply or a 👎, "
                         "read by a model from the reply texts. {n} finding(s) classified.",
        "t_by_reviewer": "Causes by reviewer",
        "t_misses": "Recurring misses",
        "t_misses_note": "Human comments where the bot said nothing, grouped by the "
                         "triage ({n} comment(s) read).",
        "t_misses_hidden": "Titles and suggested rules are model text about the PRs: "
                           "shown only with [ledger] include_text = true.",
        "t_misses_none": "No recurring pattern in the last triage.",
        "t_pattern": "Pattern", "t_pattern_n": "Pattern {i}",
        "t_suggested": "Suggested rule",
    },
    "fr": {
        "by_reviewer": "Par relecteur", "by_severity": "Par sévérité",
        "top_rules": "Règles les plus postées", "judgment": "Jugement par réaction",
        "j_useful": "utile", "j_wrong": "fausse", "j_off_topic": "hors sujet",
        "j_saved_me": "m'a sauvé", "j_other": "autres réactions",
        "judgment_note": "Réactions classées par [stats.judgment] du project.toml.",
        "signal": "Remarques avec un retour humain", "signal_sub": "réaction ou réponse",
        "delay": "attente médiane d'un retour humain",
        "delay_sub": "sur {n} remarques", "delay_sub_one": "sur {n} remarque",
        "causes": "Réponses par étiquette",
        "c_false_positive": "faux positif", "c_intended": "voulu",
        "c_unimportant": "pas important", "c_question": "question", "c_other": "autre",
        "causes_note": "Étiquettes par mots-clés : un tri grossier, à lire comme une "
                       "tendance.",
        "contested_rules": "Règles contestées 3 fois ou plus",
        "misses": "Manqués approchés", "human_comments": "commentaires humains",
        "silent": "là où le bot n'a rien dit", "dropped": "sur une remarque écartée",
        "verification": "Rendement de la vérification",
        "v_confirmed": "confirmées par la vérification",
        "v_refuted": "réfutées par la vérification", "confirmed_contested": "contestées parmi les confirmées",
        "none_contested": "Aucune règle contestée 3 fois pour l'instant.",
        "t_causes": "Causes des remarques contestées (tri LLM)",
        "t_causes_note": "reviewme triage : une cause par remarque ayant une réponse ou "
                         "un 👎, lue par un modèle dans le texte des réponses. {n} "
                         "remarque(s) classée(s).",
        "t_by_reviewer": "Causes par relecteur",
        "t_misses": "Manqués récurrents",
        "t_misses_note": "Commentaires humains là où le bot n'a rien dit, regroupés par "
                         "le tri ({n} commentaire(s) lu(s)).",
        "t_misses_hidden": "Les titres et règles suggérées sont du texte de modèle sur "
                           "les PR : affichés seulement avec [ledger] include_text = true.",
        "t_misses_none": "Aucun motif récurrent dans le dernier tri.",
        "t_pattern": "Motif", "t_pattern_n": "Motif {i}",
        "t_suggested": "Règle suggérée",
    },
}


def _first_seen(findings: list[dict]) -> list[dict]:
    """Each finding once, at its first appearance on the PR (same key as calibration)."""
    seen: dict = {}
    for f in sorted(findings, key=lambda f: f.get("ts", "")):
        key = (f.get("pr"), f.get("reviewer"),
               f.get("fingerprint") or f"{f.get('path')}:{f.get('line')}:{f.get('run_id')}")
        seen.setdefault(key, f)
    return list(seen.values())


def _triage(ctx: Context) -> dict | None:
    """Causes and recurring misses from `reviewme triage`, None when it never ran here."""
    if not ctx.triage and not ctx.triage_patterns:
        return None
    rows = list(ctx.triage.values())
    causes = Counter(r.get("cause") for r in rows)
    by_reviewer: dict[str, Counter] = {}
    for r in rows:
        by_reviewer.setdefault(r.get("reviewer") or "?", Counter())[r.get("cause")] += 1
    p = ctx.triage_patterns or {}
    patterns = []
    for x in p.get("patterns") or []:
        item = {"count": x.get("count", 0), "examples": len(x.get("example_comment_ids") or [])}
        # Model prose about the PRs: in the page and in `/api/dashboard` only by opt-in.
        if ctx.settings.include_text:
            item.update(title=x.get("title") or "", suggested_rule=x.get("suggested_rule") or "")
        patterns.append(item)
    return {
        "classified": len(rows),
        "causes": {c: causes.get(c, 0) for c in TRIAGE_CAUSES},
        "by_reviewer": {k: {c: v.get(c, 0) for c in TRIAGE_CAUSES}
                        for k, v in sorted(by_reviewer.items())},
        "patterns": patterns,
        "misses_read": p.get("misses", 0),
        "texts_shown": ctx.settings.include_text,
    }


def compute(ctx: Context) -> dict:
    q, judged = ctx.quality, ctx.judged
    enrich = [ctx.enrich.get(j.get("comment_id")) or {} for j in judged]
    judgment = Counter()
    for e in enrich:
        for r in e.get("reactions", []):
            judgment[ctx.settings.judgment_of(r.get("content")) or "other"] += 1
    causes = Counter(r.get("label") for e in enrich for r in e.get("replies", []))
    verified = Counter((f.get("verification") or {}).get("status")
                       for f in _first_seen(ctx.findings))
    confirmed = [j for j in judged if (j.get("verification") or {}).get("status") == "confirmed"]
    rules = sorted(q["by_rule"].items(), key=lambda kv: (-kv[1]["posted"], kv[0]))[:TOP_RULES]
    answered = sum(1 for j in judged if j["answered"])
    # From the posting run (`ts` of the finding's first appearance) to the first signal.
    delays = [h for j, e in zip(judged, enrich, strict=True)
              if (h := hours_between(j.get("ts"), first_signal(e))) is not None]
    return {
        "by_reviewer": q["by_reviewer"],
        "by_severity": q["by_severity"],
        "top_rules": dict(rules),
        "judgment": {k: judgment.get(k, 0) for k in (*JUDGMENTS, "other")},
        "with_human_signal": answered,
        "human_signal_rate": C.ratio(answered, len(judged)),
        "judgment_delay_median_h": median(delays),
        "judgment_delay_n": len(delays),
        "reply_labels": {k: causes.get(k, 0) for k in CAUSES},
        "contested_rules": q["contested_rules"],
        "misses": q["misses"],
        "verification": {
            "confirmed": verified.get("confirmed", 0),
            "refuted": verified.get("refuted", 0),
            "confirmed_posted": len(confirmed),
            "confirmed_contested": sum(1 for j in confirmed if j["contested"]),
            "confirmed_contested_rate": C.ratio(sum(1 for j in confirmed if j["contested"]),
                                                len(confirmed)),
        },
        "triage": _triage(ctx),
    }


def _tally_table(T: dict, first: str, data: dict) -> str:
    rows = [(k, t["posted"], f'{t["followed"]} · {C.pct(t["followed_rate"])}',
             f'{t["approved"]} · {C.pct(t["approved_rate"])}',
             f'{t["contested"]} · {C.pct(t["contested_rate"])}') for k, t in data.items()]
    return C.table([first, T["posted"], T["followed"], T["approved"], T["contested"]], rows,
                   right=(1, 2, 3, 4), empty_text=T["no_data"])


def render(res: dict, ctx: Context) -> str:
    T = texts(_T, ctx.language)
    v, m = res["verification"], res["misses"]
    judgment = [(T[f"j_{k}"], n, str(n)) for k, n in res["judgment"].items()]
    t = res.get("triage")
    if t:
        causes = [(T[f"cause_{k}"], n, str(n)) for k, n in t["causes"].items()]
        causes_title, causes_note = T["t_causes"], T["t_causes_note"].format(n=t["classified"])
    else:
        causes = [(T[f"c_{k}"], n, str(n)) for k, n in res["reply_labels"].items()]
        causes_title, causes_note = T["causes"], T["causes_note"]
    contested = C.table([T["rule"], T["contested"], T["posted"]],
                        [(r["rule"], r["contested"], r["posted"])
                         for r in res["contested_rules"]], right=(1, 2),
                        empty_text=T["none_contested"])
    return "".join([
        C.tiles([
            (C.pct(res["human_signal_rate"]), T["signal"], T["signal_sub"]),
            (C.hours(res["judgment_delay_median_h"]), T["delay"],
             plural(T, "delay_sub", res["judgment_delay_n"])),
            (str(v["confirmed"]), T["v_confirmed"]),
            (str(v["refuted"]), T["v_refuted"]),
            (C.pct(v["confirmed_contested_rate"]), T["confirmed_contested"],
             f'{v["confirmed_contested"]} / {v["confirmed_posted"]}'),
        ]),
        C.card(_tally_table(T, T["reviewer"], res["by_reviewer"]), T["by_reviewer"],
               reading=_tally_reading(T, res["by_reviewer"], ctx.language)),
        C.card(_tally_table(T, T["severity"], res["by_severity"]), T["by_severity"],
               reading=_tally_reading(T, res["by_severity"], ctx.language)),
        C.card(_tally_table(T, T["rule"], res["top_rules"]), T["top_rules"],
               reading=_tally_reading(T, res["top_rules"], ctx.language)),
        '<div class="grid2"><div>',
        C.card(C.hbars(judgment, T["no_data"]) if any(n for _, n, _ in judgment)
               else C.empty(T["no_data"]), T["judgment"], T["judgment_note"],
               R.top(T["count"], judgment, C.num, ctx.language)),
        "</div><div>",
        C.card(C.hbars(causes, T["no_data"]) if any(n for _, n, _ in causes)
               else C.empty(T["no_data"]), causes_title, causes_note,
               R.top(T["count"], causes, C.num, ctx.language)),
        "</div></div>",
        _triage_by_reviewer(T, t, ctx.language) if t else "",
        C.card(contested, T["contested_rules"], reading=R.top(
            T["contested"], [(r["rule"], r["contested"]) for r in res["contested_rules"]],
            C.num, ctx.language)),
        f'<h3>{C.esc(T["misses"])}</h3>',
        C.tiles([(str(m["human_comments"]), T["human_comments"]),
                 (str(m["on_silent_lines"]), T["silent"]),
                 (str(m["on_dropped_findings"]), T["dropped"])]),
        _recurring_misses(T, t, ctx.language) if t else "",
    ])


def _tally_reading(T: dict, data: dict, language: str) -> str:
    """Which row got the best acted-upon rate: the column a reader looks for first."""
    return R.top(T["followed"], [(k, v["followed_rate"]) for k, v in data.items()],
                 C.pct, language)


def _triage_by_reviewer(T: dict, t: dict, language: str) -> str:
    """Only the causes someone actually got: eight mostly-zero columns read badly."""
    used = [c for c in TRIAGE_CAUSES if t["causes"].get(c)]
    if not used:
        return ""
    rows = [(rid, *(counts[c] for c in used)) for rid, counts in t["by_reviewer"].items()]
    return C.card(C.table([T["reviewer"], *(T[f"cause_{c}"] for c in used)], rows,
                          right=tuple(range(1, len(used) + 1))), T["t_by_reviewer"],
                  reading=R.top(T["count"], [(rid, sum(counts[c] for c in used))
                                             for rid, counts in t["by_reviewer"].items()],
                                C.num, language))


def _recurring_misses(T: dict, t: dict, language: str) -> str:
    if t["texts_shown"]:
        rows = [(p["title"], p["count"], p["suggested_rule"]) for p in t["patterns"]]
        heads, right, note = [T["t_pattern"], T["count"], T["t_suggested"]], (1,), ""
    else:
        rows = [(T["t_pattern_n"].format(i=i), p["count"])
                for i, p in enumerate(t["patterns"], 1)]
        heads, right, note = [T["t_pattern"], T["count"]], (1,), T["t_misses_hidden"]
    body = C.table(heads, rows, right=right, empty_text=T["t_misses_none"])
    if note and rows:
        body += C.empty(note)
    return C.card(body, T["t_misses"], T["t_misses_note"].format(n=t["misses_read"]),
                  R.top(T["count"], [(r[0], r[1]) for r in rows], C.num, language))
