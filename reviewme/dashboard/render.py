"""The page: header, a menu of the enabled sections, one `<section>` each, a footer.

A single self-contained file: inline CSS, inline SVG, a few lines of inline JS, no
network request — it is opened from a CI artifact, attached to a ticket, served by the
local dashboard. With JavaScript, one section shows at a time and the URL hash selects it
(`dashboard.html#cost` can be shared); without it, every section is visible one under
the other and the menu links are plain anchors.

Privacy: sections only print counters, reviewer and rule ids, PR numbers and people
through `Context.name_of`. Nothing here reads `message`, `suggestion`, reply `text` or a
PR title. The single exception: with `[ledger] include_text = true`, the quality page
prints the titles and suggested rules of the triage's recurring misses (model text about
the PRs), and the footer says so.
"""
from __future__ import annotations

import logging

from . import charts as C
from .data import Context
from .i18n import texts, title

_T = {
    "en": {
        "page_title": "ReviewMe — dashboard", "sections": "Sections",
        "window": "window {w}", "period_week": "per week", "period_sprint": "per sprint",
        "generated": "generated {d}", "language": "language {l}",
        "failed": "This section could not be computed ({e}). The others are unaffected.",
        "foot": "Counters, reviewer ids, rule ids and {who} only: this page contains no "
                "pull request title, no finding message, no code excerpt and no reply text.",
        "foot_triage": "Exception, with [ledger] include_text: the recurring misses of "
                       "the LLM triage are summarised in the model's words.",
        "who_login": "GitHub logins", "who_masked": "masked names",
        "nothing": "No section enabled in [stats] sections.",
    },
    "fr": {
        "page_title": "ReviewMe — dashboard", "sections": "Sections",
        "window": "fenêtre {w}", "period_week": "par semaine", "period_sprint": "par sprint",
        "generated": "généré le {d}", "language": "langue {l}",
        "failed": "Cette section n'a pas pu être calculée ({e}). Les autres ne sont pas "
                  "touchées.",
        "foot": "Compteurs, identifiants de relecteurs et de règles et {who} uniquement : "
                "cette page ne contient aucun titre de pull request, aucun message de "
                "remarque, aucun extrait de code ni aucun texte de réponse.",
        "foot_triage": "Exception, avec [ledger] include_text : les manqués récurrents "
                       "du tri LLM sont résumés avec les mots du modèle.",
        "who_login": "logins GitHub", "who_masked": "noms masqués",
        "nothing": "Aucune section activée dans [stats] sections.",
    },
}

# Before the content: hides the sections only once we know JS runs (no-JS = all visible).
_HEAD_JS = "document.documentElement.className+=' js';"
_JS = """(function(){
var secs=[].slice.call(document.querySelectorAll('main section'));
var links=[].slice.call(document.querySelectorAll('nav a'));
function show(){
  var id=decodeURIComponent(location.hash.slice(1));
  if(!secs.some(function(s){return s.id===id;})&&secs.length){id=secs[0].id;}
  secs.forEach(function(s){s.classList.toggle('on',s.id===id);});
  links.forEach(function(a){a.classList.toggle('on',a.getAttribute('href')==='#'+id);});
}
window.addEventListener('hashchange',function(){show();window.scrollTo(0,0);});
show();
})();"""

_log = logging.getLogger("reviewme")


def run_sections(ctx: Context, modules: list) -> dict[str, dict]:
    """`compute` + `render` per section. One failing section yields an error card, not a
    broken page: the dashboard is a reading aid, it must not fail as a whole."""
    T = texts(_T, ctx.language)
    out: dict[str, dict] = {}
    for m in modules:
        try:
            result = m.compute(ctx)
            body = m.render(result, ctx)
        except Exception as e:          # isolate the section: the others still render
            _log.warning("dashboard: section %s failed (%s: %s)", m.ID, type(e).__name__, e)
            result = {"error": type(e).__name__}
            body = C.card(C.empty(T["failed"].format(e=type(e).__name__)))
        out[m.ID] = {"title": title(m.TITLE, ctx.language), "result": result, "html": body}
    return out


def page(ctx: Context, sections: dict[str, dict]) -> str:
    T = texts(_T, ctx.language)
    s = ctx.settings
    menu = "".join(f'<li><a href="#{C.esc(sid)}">{C.esc(v["title"])}</a></li>'
                   for sid, v in sections.items())
    body = "".join(f'<section id="{C.esc(sid)}"><h2>{C.esc(v["title"])}</h2>{v["html"]}'
                   f"</section>" for sid, v in sections.items()) or C.card(C.empty(T["nothing"]))
    meta = [T["window"].format(w=s.window_label),
            T["period_" + s.period],
            T["generated"].format(d=ctx.now.strftime("%Y-%m-%d %H:%M UTC")),
            T["language"].format(l=ctx.language)]
    heading = f"ReviewMe — {s.project}" if s.project else "ReviewMe"
    foot = T["foot"].format(who=T["who_" + s.names])
    if s.include_text and (ctx.triage_patterns or {}).get("patterns"):
        foot += " " + T["foot_triage"]
    return f"""<!doctype html>
<html lang="{C.esc(ctx.language)}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{C.esc(T["page_title"])}</title>
<style>{C.CSS}</style>
<script>{_HEAD_JS}</script></head>
<body><div class="wrap">
<h1>{C.esc(heading)}</h1>
<p class="sub">{" · ".join(f"<span>{C.esc(m)}</span>" for m in meta)}</p>
<div class="layout">
<nav aria-label="{C.esc(T["sections"])}"><ul>{menu}</ul></nav>
<main>{body}</main>
</div>
<p class="foot">{C.esc(foot)}</p>
</div>
<script>{_JS}</script>
</body></html>
"""
