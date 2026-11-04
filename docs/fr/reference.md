[← ReviewMe](../../README.fr.md) · [English](../reference.md) · **Français**

# Référence

| Commande | Rôle |
|---|---|
| `reviewme review --repo O/R --pr N` | relire une PR (`--dry-run`, `--force`) |
| `reviewme init-project <nom> --reviewers tech,us` | créer un projet depuis le squelette |
| `reviewme projects` | lister les projets et leurs relecteurs |
| `reviewme enrich --since 14d` | relever le devenir des remarques (job de nuit) |
| `reviewme export --since 7d --out DIR` | archive d'analyse et rapport lisible |
| `reviewme triage --since 14d [--dry-run]` | facultatif : un modèle trie pourquoi les remarques sont contestées et regroupe les manqués récurrents (`include_text`, `[stats.triage]`) |
| `reviewme history --since 2024-01-01 [--jira]` | lecture ponctuelle des PR mergées et fermées depuis une date, pour les pages avant/après (relançable) |
| `reviewme stats [--html F]` | compteurs et bloc qualité ; `--html` écrit le tableau de bord |
| `reviewme feedback --pr N` | réponses des devs, en règles à relire |
| `reviewme watch --repo O/R --every 10m` | surveille les PR ouvertes et relit chacune (`--once`, `--dry-run`, `--label`) |
| `reviewme-webhook` · `reviewme-poll` · `reviewme-web` | serveur webhook, boucle de poll (ancien nom de watch), tableau de bord |

| Variable | Rôle |
|---|---|
| `GITHUB_TOKEN` (ou `GITHUB_APP_ID` + clé) | accès GitHub |
| `REPO_PATH` | clone local du dépôt relu |
| `REVIEWME_CONFIG_HOME`, `PROJECT` | où est la config, et quel projet |
| `CONFIDENCE_THRESHOLD` | seuil de confiance (défaut 80) |
| `MAX_COMMENTS_PER_PR` | plafond de commentaires par PR (défaut 10) |
| `REVIEWME_INLINE_MIN_SEVERITY` | sévérité minimale en ligne (défaut `IMPORTANT`) |
| `REVIEWME_VERIFY` | `0` coupe la vérification |
| `REVIEWME_CONFIG_SOURCE` | `base` (défaut) ou `pr` |
| `REVIEWME_TRUSTED_BASES` | branches base de confiance supplémentaires pour la config, ex. `develop,release/*` (voir [D'où vient la config](how-it-works.md#doù-vient-la-config-branche-base)) |
| `FETCH_BASE_TIMEOUT` | timeout du fetch de la branche base, secondes (défaut 30 ; jamais `0` en CI avec une config dans le dépôt) |
| `REVIEWME_TRIGGER` | ce qui a lancé le passage, noté dans le registre (`pr_build`, `manual`, `schedule`, `watch`) |
| `REVIEWME_CONTROL_GROUP` | `enrich` : `0`/`1` remplace `[stats] control_group` |
| `JIRA_BUG_TYPES` | `history --jira` : noms de types de ticket comptés comme bugs (défaut `Bug`) |
| `REVIEWME_FAKE`, `REVIEWME_DEBUG` | sans appel au modèle ; journal détaillé |
| `ANTHROPIC_BASE_URL`, `CLAUDE_MODEL` | passerelle LLM, modèle |
| `REVIEWME_SHOW_MODEL`, `REVIEWME_SHOW_SCORE`, `REVIEWME_CLEAN_NOTE` | mode simple : `[review] show_model` / `show_score` / `clean_note` |
| `POLL_INTERVAL`, `REVIEWME_SKIP_LABEL` | watch : intervalle par défaut, label d'exclusion (défaut `no-ai-review`) |

| Clé du `project.toml` | Rôle |
|---|---|
| `[review] language` | langue des remarques postées et des titres du bot (défaut `en`, ex. `"fr"`) |
| `[review] show_model`, `[review] show_score` | modèle (configuré et ayant répondu) et score sous chaque remarque (défaut `false`, voir [Modèle et score sur les commentaires](on-the-pr.md#modèle-et-score-sur-les-commentaires)) |
| `[review] clean_note` | un court commentaire « revue terminée, aucune remarque » sur une PR propre (défaut `false`, voir [Note sur une PR propre](on-the-pr.md#note-sur-une-pr-propre)) |
| `[ledger] include_text` | garder le texte des remarques et des réponses dans le registre (défaut `false`) |
| `[stats]` | sections, noms, fenêtre, période, `bot_since`, `[stats.roi]` et `[stats.triage]` du tableau de bord (voir [Mesurer si c'est utile](measuring.md)) |

Toutes les clés, commentées : [`.env.example`](../../.env.example). Toutes les options de
`reviewer.toml` : [`docs/reviewme.html`](../reviewme.html). Limites connues :
[`docs/LIMITATIONS.md`](../LIMITATIONS.md).
