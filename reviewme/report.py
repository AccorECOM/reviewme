"""Rapport HTML autonome à partir des statistiques agrégées.

Une page sans dépendance ni requête réseau : on l'ouvre, on la met en pièce jointe, on
la publie. Elle ne contient que des compteurs — aucun titre de PR, aucun extrait de code
(cf. `stats.py`), donc elle circule sans précaution particulière.

Choix de formes : les totaux sont des **chiffres**, pas des graphiques — un histogramme
à une barre n'apprend rien. Seules les séries (activité par PR, activité par auteur) sont
tracées, à une série chacune : pas de couleur catégorielle, donc pas de légende.

Palette validée sur fond crème (6 checks : bande de clarté, chroma, séparation
daltonisme, plancher vision normale, contraste). Ne pas la modifier sans revalider.
"""
from __future__ import annotations

import html
from datetime import UTC, datetime
from pathlib import Path

from .stats import _charger, resume

ACCENT = "#7C3AED"          # violet — série principale
ENCRE = "#111111"
MUET = "#55504C"
FOND = "#FAF4F0"
CARTE = "#FFFFFF"

_CSS = """
*,*::before,*::after{box-sizing:border-box}
body{margin:0;padding:28px;background:%(fond)s;color:%(encre)s;
  font:14px/1.55 -apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",sans-serif}
.wrap{max-width:960px;margin:0 auto}
h1{font-size:clamp(26px,4vw,36px);font-weight:900;letter-spacing:-.02em;margin:0 0 4px}
h2{font-size:16px;font-weight:800;margin:32px 0 12px}
.sub{color:%(muet)s;margin:0 0 24px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.tile{background:%(carte)s;border:2px solid %(encre)s;border-radius:3px;
  box-shadow:4px 4px 0 %(encre)s;padding:14px 16px}
.tile .n{font-size:26px;font-weight:900;font-variant-numeric:tabular-nums;line-height:1.1}
.tile .l{font-size:10.5px;font-weight:800;text-transform:uppercase;letter-spacing:.09em;
  color:%(muet)s;margin-top:6px}
.card{background:%(carte)s;border:2px solid %(encre)s;border-radius:3px;
  box-shadow:4px 4px 0 %(encre)s;padding:16px;overflow-x:auto}
table{border-collapse:collapse;width:100%%;font-size:13px}
th{text-align:left;font-size:10.5px;text-transform:uppercase;letter-spacing:.09em;
  background:#F3EAE4;padding:8px 10px;border-bottom:2px solid %(encre)s}
td{padding:7px 10px;border-bottom:1px solid #DDD3CC;font-variant-numeric:tabular-nums}
.foot{margin-top:32px;padding-top:14px;border-top:2px solid %(encre)s;
  font-size:11.5px;color:%(muet)s}
""" % {"fond": FOND, "encre": ENCRE, "muet": MUET, "carte": CARTE}


def _taux(r: dict) -> str:
    """Part des remarques encore ouvertes auxquelles un développeur a répondu.

    C'est le signal d'engagement : des remarques auxquelles personne ne répond jamais
    sont, au mieux, ignorées — au pire, du bruit qu'on finit par couper.
    """
    ouvertes = r.get("remarques_ouvertes", 0)
    if not ouvertes:
        return "—"
    return f'{round(100 * r.get("remarques_avec_reponse", 0) / ouvertes)} %'


def _tuile(valeur: str, label: str) -> str:
    return f'<div class="tile"><div class="n">{valeur}</div><div class="l">{html.escape(label)}</div></div>'


def _barres(donnees: list[tuple[str, float]], unite: str, hauteur: int = 190) -> str:
    """Barres verticales, une seule série — donc ni légende ni couleur catégorielle."""
    if not donnees:
        return f'<p style="color:{MUET}">Pas encore de données.</p>'

    largeur, marge_g, marge_b = 900, 44, 34
    plot_h = hauteur - marge_b - 14
    maxi = max(v for _, v in donnees) or 1
    pas = (largeur - marge_g - 12) / len(donnees)
    barre = max(6, min(38, pas - 6))          # 6 px de gouttière : les fills ne se touchent pas

    out = [f'<svg viewBox="0 0 {largeur} {hauteur}" width="100%" role="img" '
           f'style="max-width:{largeur}px;font-family:inherit">']
    # grille : trois repères, volontairement discrets
    for i in range(4):
        y = 14 + plot_h * i / 3
        val = maxi * (3 - i) / 3
        out.append(f'<line x1="{marge_g}" y1="{y:.1f}" x2="{largeur-12}" y2="{y:.1f}" '
                   f'stroke="#DDD3CC" stroke-width="1"/>')
        out.append(f'<text x="{marge_g-8}" y="{y+4:.1f}" text-anchor="end" font-size="10" '
                   f'fill="{MUET}">{val:.2f}'.rstrip("0").rstrip(".") + "</text>")
    for i, (label, val) in enumerate(donnees):
        h = max(2, plot_h * val / maxi)
        x = marge_g + i * pas + (pas - barre) / 2
        y = 14 + plot_h - h
        out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{barre:.1f}" height="{h:.1f}" '
                   f'rx="4" fill="{ACCENT}"><title>{html.escape(label)} — {val}{unite}</title></rect>')
        if len(donnees) <= 16:
            out.append(f'<text x="{x+barre/2:.1f}" y="{hauteur-12}" text-anchor="middle" '
                       f'font-size="10" fill="{MUET}">{html.escape(label)}</text>')
    out.append("</svg>")
    return "".join(out)


def _barres_h(donnees: list[tuple[str, float, str]]) -> str:
    """Barres horizontales : le libellé est lisible, la valeur est écrite au bout."""
    if not donnees:
        return f'<p style="color:{MUET}">Pas encore de données.</p>'
    largeur, ligne = 900, 30
    hauteur = len(donnees) * ligne + 10
    maxi = max(v for _, v, _ in donnees) or 1
    label_w, place_valeur = 160, 200   # le libellé de valeur tient toujours à droite
    out = [f'<svg viewBox="0 0 {largeur} {hauteur}" width="100%" role="img" '
           f'style="max-width:{largeur}px;font-family:inherit">']
    for i, (label, val, suffixe) in enumerate(donnees):
        y = i * ligne + 6
        w = max(2, (largeur - label_w - place_valeur) * val / maxi)
        out.append(f'<text x="0" y="{y+14}" font-size="12" fill="{ENCRE}">{html.escape(label[:24])}</text>')
        out.append(f'<rect x="{label_w}" y="{y+3}" width="{w:.1f}" height="16" rx="4" '
                   f'fill="{ACCENT}"><title>{html.escape(label)} — {suffixe}</title></rect>')
        out.append(f'<text x="{label_w+w+8:.1f}" y="{y+16}" font-size="11.5" '
                   f'fill="{MUET}" font-variant-numeric="tabular-nums">{html.escape(suffixe)}</text>')
    out.append("</svg>")
    return "".join(out)


def generer(chemin_stats: Path | None = None) -> str:
    r = resume(chemin_stats)
    runs = _charger(chemin_stats or Path("data/stats.json"))["runs"]

    if not r.get("runs"):
        corps = '<div class="card"><p>Aucune review enregistrée pour le moment.</p></div>'
    else:
        evite = r["remarques_dedupliquees"] + r["remarques_sous_seuil"]
        tuiles = "".join([
            _tuile(str(r["prs"]), "pull requests"),
            _tuile(str(r["remarques_postees"]), "remarques postées"),
            _tuile(str(evite), "remarques évitées"),
            _tuile(f'{r["cout_total_usd"]:.2f} $', "coût total"),
            _tuile(f'{r["cout_moyen_par_pr"]:.2f} $', "coût par PR"),
            _tuile(str(r["remarques_par_pr"]), "remarques par PR"),
            _tuile(_taux(r), "remarques ayant reçu une réponse"),
        ])

        par_pr: dict[int, float] = {}
        for run in runs:
            par_pr[run["pr"]] = par_pr.get(run["pr"], 0) + run.get("cout_usd", 0)
        derniers = list(par_pr.items())[-30:]

        auteurs = sorted(r["par_auteur"].items(), key=lambda kv: -kv[1]["postes"])[:12]
        lignes_auteurs = [(a, d["postes"], f'{d["postes"]} remarques · {d["cout"]:.2f} $')
                          for a, d in auteurs]

        lignes_table = "".join(
            f'<tr><td>#{run["pr"]}</td><td>{html.escape(run.get("auteur") or "?")}</td>'
            f'<td>{", ".join(run.get("reviewers", []))}</td><td>{run.get("postes", 0)}</td>'
            f'<td>{run.get("dedupliques", 0)}</td><td>{run.get("cout_usd", 0):.2f} $</td>'
            f'<td>{run.get("duree_s", 0)} s</td></tr>'
            for run in runs[-40:][::-1])

        corps = f"""
        <div class="tiles">{tuiles}</div>

        <h2>Coût par pull request</h2>
        <div class="card">{_barres([(f'#{p}', c) for p, c in derniers], " $")}</div>

        <h2>Remarques par auteur</h2>
        <div class="card">{_barres_h(lignes_auteurs)}</div>

        <h2>Détail des runs</h2>
        <div class="card"><table>
          <tr><th>PR</th><th>Auteur</th><th>Reviewers</th><th>Postées</th>
              <th>Dédupliquées</th><th>Coût</th><th>Durée</th></tr>
          {lignes_table}
        </table></div>"""

    date = datetime.now(UTC).strftime("%d/%m/%Y %H:%M UTC")
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ReviewMe — statistiques</title>
<style>{_CSS}</style></head>
<body><div class="wrap">
<h1>ReviewMe</h1>
<p class="sub">Statistiques de review · généré le {date}</p>
{corps}
<p class="foot">Compteurs uniquement : cette page ne contient aucun titre de pull request,
aucun message de review ni extrait de code.</p>
</div></body></html>"""


def ecrire(destination: Path, chemin_stats: Path | None = None) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(generer(chemin_stats), encoding="utf-8")
    return destination
