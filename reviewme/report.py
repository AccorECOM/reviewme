"""Legacy HTML report built from `stats.json` only (deprecated).

`reviewme stats --html` now writes the ledger-driven dashboard (`reviewme/dashboard/`),
which carries everything this page did and much more. This module stays for callers that
only have `stats.json` (no ledger): same page as before, now drawn with the dashboard's
palette and chart helpers (`dashboard/charts.py`) so the two cannot drift apart.

A page with no dependency and no network request: open it, attach it, publish it. It
only contains counters — no PR title, no code excerpt (see `stats.py`), so it can be
shared without special precautions.
"""
from __future__ import annotations

import html
from datetime import UTC, datetime
from pathlib import Path

from .dashboard import charts as C
from .stats import _charger, resume

ACCENT, ENCRE, MUET, FOND, CARTE = C.ACCENT, C.ENCRE, C.MUET, C.FOND, C.CARTE
_CSS = C.CSS


def _taux(r: dict) -> str:
    """Share of still-open findings that a developer replied to.

    This is the engagement signal: findings nobody ever replies to are, at best,
    ignored — at worst, noise that ends up being switched off.
    """
    ouvertes = r.get("remarques_ouvertes", 0)
    if not ouvertes:
        return "—"
    return f'{round(100 * r.get("remarques_avec_reponse", 0) / ouvertes)} %'


def _tuile(valeur: str, label: str) -> str:
    return C.tile(valeur, label)


def _barres(donnees: list[tuple[str, float]], unite: str, hauteur: int = 190) -> str:
    return C.bars(donnees, lambda v: f"{v:.2f}".rstrip("0").rstrip(".") + unite,
                  height=hauteur)


def _barres_h(donnees: list[tuple[str, float, str]]) -> str:
    return C.hbars(donnees)


def generer(chemin_stats: Path | None = None) -> str:
    r = resume(chemin_stats)
    runs = _charger(chemin_stats or Path("data/stats.json"))["runs"]

    if not r.get("runs"):
        corps = '<div class="card"><p>No review recorded yet.</p></div>'
    else:
        evite = r["remarques_dedupliquees"] + r["remarques_sous_seuil"]
        tuiles = "".join([
            _tuile(str(r["prs"]), "pull requests"),
            _tuile(str(r["remarques_postees"]), "findings posted"),
            _tuile(str(evite), "findings avoided"),
            _tuile(f'{r["cout_total_usd"]:.2f} $', "total cost"),
            _tuile(f'{r["cout_moyen_par_pr"]:.2f} $', "cost per PR"),
            _tuile(str(r["remarques_par_pr"]), "findings per PR"),
            _tuile(_taux(r), "findings that got a reply"),
        ])

        par_pr: dict[int, float] = {}
        for run in runs:
            par_pr[run["pr"]] = par_pr.get(run["pr"], 0) + run.get("cout_usd", 0)
        derniers = list(par_pr.items())[-30:]

        auteurs = sorted(r["par_auteur"].items(), key=lambda kv: -kv[1]["postes"])[:12]
        lignes_auteurs = [(a, d["postes"], f'{d["postes"]} findings · {d["cout"]:.2f} $')
                          for a, d in auteurs]

        lignes_table = "".join(
            f'<tr><td>#{run["pr"]}</td><td>{html.escape(run.get("auteur") or "?")}</td>'
            f'<td>{", ".join(run.get("reviewers", []))}</td><td>{run.get("postes", 0)}</td>'
            f'<td>{run.get("dedupliques", 0)}</td><td>{run.get("cout_usd", 0):.2f} $</td>'
            f'<td>{run.get("duree_s", 0)} s</td></tr>'
            for run in runs[-40:][::-1])

        corps = f"""
        <div class="tiles">{tuiles}</div>

        <h2>Cost per pull request</h2>
        <div class="card">{_barres([(f'#{p}', c) for p, c in derniers], " $")}</div>

        <h2>Findings per author</h2>
        <div class="card">{_barres_h(lignes_auteurs)}</div>

        <h2>Run details</h2>
        <div class="card"><table>
          <tr><th>PR</th><th>Author</th><th>Reviewers</th><th>Posted</th>
              <th>Deduplicated</th><th>Cost</th><th>Duration</th></tr>
          {lignes_table}
        </table></div>"""

    date = datetime.now(UTC).strftime("%d/%m/%Y %H:%M UTC")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ReviewMe — statistics</title>
<style>{_CSS}</style></head>
<body><div class="wrap">
<h1>ReviewMe</h1>
<p class="sub">Review statistics · generated on {date}</p>
{corps}
<p class="foot">Counters only: this page contains no pull request title, no review
message and no code excerpt.</p>
</div></body></html>"""


def ecrire(destination: Path, chemin_stats: Path | None = None) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(generer(chemin_stats), encoding="utf-8")
    return destination
