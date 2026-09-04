"""ReviewMe — bot de code review IA : commentaires inline ligne-par-ligne + cycle de vie des threads.

Modules :
  config.py        configuration (.env)
  models.py        Finding / ReviewResult + parsing robuste
  diff_utils.py    parseur de patch -> positions inline valides (anti-422)
  reviewer.py      invocation `claude -p` -> findings structurés (sans Write)
  github_client.py client REST GitHub (diff, files, review inline, replies, list)
  reconciler.py    dédup par fingerprint + validation diff + prepare()/post_all()
  projects.py      config par PROJET + specs de reviewers + sélection (ADR v3)
  precheck.py      script déterministe d'un reviewer (les faits avant le LLM)
  history.py       fils de discussion de la PR, donnés à l'agent avant qu'il juge
  stats.py         compteurs agrégés, sans contenu (transportables par un cache CI)
  feedback.py      retours des développeurs -> règles attribuées, à relire
  report.py        rapport HTML autonome des statistiques
  context/         fournisseurs de contexte externe optionnels (jira.py)
  github_auth.py   PAT ou GitHub App (JWT RS256 -> token d'installation renouvelé)
  run.py           run_review(repo, pr, config) : la fonction commune à toutes les entrées
  state.py         cache last_reviewed_sha (flock inter-process)
  logging_.py      logs JSON + console
  cli.py           entrée one-shot : `reviewme review --repo O/R --pr N`
  poll.py          entrée boucle (mode VPS)
  webhook.py       entrée webhook (stub documenté — v2)
  web/             dashboard (read-only, durci)
"""

__version__ = "1.0.0"
