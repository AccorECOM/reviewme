# Registre ReviewMe : schéma des fichiers

Ce document accompagne l'archive produite par `reviewme export` (il y est copié sous le
nom `SCHEMA.md`). Tous les fichiers `.jsonl` sont en ajout seul : une ligne = un objet
JSON. Quand un même objet apparaît plusieurs fois (même `comment_id`, même `pr`), **la
ligne la plus récente fait foi**. `schema_version` vaut `1`.

Sur disque, chaque fichier est découpé en parts, une par run ou par passe d'enrichissement
(`findings/<horodatage>-<run_id>.jsonl`…), écrites d'un bloc : deux runs concurrents ne
peuvent pas entrelacer leurs lignes. L'archive les fusionne en un fichier par type.

Dans l'archive, les logins GitHub (`user`, `author`, `resolved_by`) sont remplacés par un
alias stable (`u-1a2b3c4d`) : on sait que deux réactions viennent de la même personne, et
si c'est l'auteur de la PR (`role`), pas qui conteste quoi.

Le texte des remarques, des réponses et des commentaires humains n'est présent que si le
projet a activé `[ledger] include_text = true` (dans `project.toml`) ou
`REVIEWME_LEDGER_INCLUDE_TEXT=1`. Sans lui, l'archive ne contient aucun contenu de PR.

## `findings.jsonl` — une ligne par finding et par run, posté ou non

| Champ | Sens |
|---|---|
| `run_id` | Identifiant du passage du moteur sur la PR (commun à tous ses reviewers) |
| `ts` | Horodatage UTC du run |
| `repo`, `pr`, `head_sha`, `base_sha` | PR et commits relus |
| `variant` | `live` (chaîne évaluée) ou `baseline` (témoin, toujours en `dry_run`) |
| `dry_run` | `true` : rien n'a été posté ; `fate = posted` veut dire « aurait été posté » |
| `status` | `ok`, ou `post_failed` si l'écriture GitHub a échoué |
| `reviewer` | Relecteur (`tech`, `architect`…) |
| `model` | Modèle qui a **réellement** répondu (lu dans l'enveloppe de la CLI) |
| `engine_commit` | Commit du moteur |
| `config_hash` | Empreinte des réglages ; détail dans `config-snapshot/<hash>/` |
| `fingerprint` | Empreinte de la ligne (`sha1(chemin + contenu normalisé)[:10]`), identique au marqueur posté. Vide si la ligne n'est pas dans le diff |
| `path`, `line` | Emplacement, côté nouveau fichier, après ré-ancrage sur le diff |
| `severity_raw` | Sévérité écrite par le modèle, avant normalisation |
| `severity` | `BLOCKER`, `IMPORTANT` ou `MINOR` |
| `confidence` | Confiance déclarée par le modèle (0-100) |
| `rule_id` | Identifiant de règle donné par le modèle (peut être vide) |
| `has_evidence` | Le modèle a cité une preuve (`evidence`) |
| `verification` | `{status, confidence}` : `confirmed`, `refuted` ou `skipped` (pas de passe de vérification) |
| `fate` | `posted`, `dropped_low` (sous le seuil), `dropped_unverified` (infirmé par la vérification), `out_of_diff` (délivré dans le résumé), `capped` (plafond de commentaires), `deduped` (déjà commenté), `invalid` (sans chemin ou ligne exploitable) |
| `comment_id` | Commentaire GitHub qui porte la remarque (posté maintenant, ou existant si `deduped`) |
| `in_summary` | Délivré dans un commentaire global plutôt qu'en ligne |
| `message`, `suggestion` | Texte, seulement avec `include_text` |

## `runs.jsonl` — une ligne par reviewer et par run

`run_id`, `ts`, `repo`, `pr`, `head_sha`, `base_sha`, `variant`, `dry_run`, `reviewer`,
`model`, `engine_commit`, `config_hash`, plus :

| Champ | Sens |
|---|---|
| `parsed_ok` | La réponse respectait le contrat de sortie |
| `num_turns`, `max_turns` | Tours utilisés et plafond |
| `subtype`, `is_error` | Fin de la CLI : `success`, `error_max_turns`… |
| `duration_s`, `cost_usd` | Durée et coût de ce reviewer |
| `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_create_tokens` | Jetons (l'entrée inclut le cache) |
| `diff_files`, `diff_lines` | Taille du diff relu |
| `context_files` | `[{path, bytes, truncated, source}]` : ce que le reviewer a réellement reçu (`context.read`, `precheck`, `historique`, `context`) |
| `verification` | Bilan de la passe de vérification, ou `null` |
| `threshold` | Seuil de confiance appliqué |
| `findings`, `fates` | Nombre de findings et répartition par sort |
| `advice`, `advice_topics` | Conseils pour la relecture affichés (ancrés dans la PR) ; leurs sujets seulement avec `include_text` |

## `enrich.jsonl` — devenir d'une remarque postée (`reviewme enrich`)

| Champ | Sens |
|---|---|
| `comment_id`, `pr`, `reviewer`, `fingerprint`, `rule_id` | Remarque suivie |
| `head_sha` | Tête de la PR au moment de l'enrichissement |
| `fingerprint_gone_at` | Premier commit de tête observé où la ligne commentée n'existe plus dans le fichier, alors que le fichier existe : **suivi d'effet** |
| `file_gone_at` | Le fichier n'existe plus à ce commit (supprimé ou renommé) : ne prouve rien, n'est pas compté comme suivi |
| `reactions` | `[{content: "+1"\|"-1", user, role: "pr_author"\|"other"}]` |
| `replies` | `[{user, role, ts, label, contests, remember?, text?}]`. `label` (par mots-clés, à relire) : `fixed`, `false_positive`, `intended`, `unimportant`, `question`, `other`. `contests` = `false_positive` ou `intended`. `remember` : `{trusted, rule?}` pour une réponse `@reviewme remember …`, `trusted` si l'auteur a le droit d'écriture, `rule` seulement avec `include_text`. Les réactions d'autres bots et celles de `BOT_LOGIN` sont ignorées |
| `thread_resolved`, `resolved_by` | Fil résolu sur GitHub, et par qui |
| `human_comments_nearby` | Commentaires humains à 10 lignes ou moins, même fichier |

## `prs.jsonl` — état d'une PR (`reviewme enrich`)

| Champ | Sens |
|---|---|
| `state`, `merged_at`, `author`, `head_sha` | État de la PR |
| `commits_after_first_review` | Commits poussés après le premier run du registre |
| `human_comments`, `human_replies` | Commentaires de review humains (racines) et réponses ; les autres bots (Copilot, Sonar…) sont exclus |
| `human_comments_on_silent_lines` | … sur des lignes où le bot n'avait rien, même écarté : **manqués approchés** |
| `human_comments_on_dropped_findings` | … là où le bot avait un finding écarté (`dropped_low`, `dropped_unverified`) et rien de posté : **coût du filtre en rappel** |
| `human_comments_text` | Détail, seulement avec `include_text` |

## `quality.json` — bloc qualité calculé

`by_reviewer`, `by_rule` (`reviewer:rule_id`), `by_severity` : `posted`, `followed`,
`approved`, `contested`, `answered` et leurs taux, sur les seules remarques postées EN
LIGNE (`posted_in_summary` compte à part celles délivrées dans un commentaire global).
Une remarque est **suivie d'effet** si sa ligne a disparu ou si une réponse dit
« corrigé » ; **approuvée** si 👍 ou réponse `fixed`, sans contestation ; **contestée** si
👎 ou réponse `false_positive` / `intended`.
`calibration` : par tranche de confiance (0-50, 51-70, 71-85, 86-100), chaque finding
`live` compté une fois (sa première apparition sur la PR), avec sa répartition par sort
(`would_post` = posté en dry-run), et le suivi des remarques postées.
`contested_rules` (≥ 3 contestations), `remembered_rules`, `misses`, `cost_by_pr` et
`cost_by_reviewer` (clés `variant:…`), `baseline_vs_live` (par reviewer, sur les mêmes
PR et commits : remarques que chaque chaîne aurait postées, et communes par empreinte),
`weekly`.

## `config-snapshot/<config_hash>/`

`manifest.json` (empreinte de chaque fichier de réglage) et une copie des `*.toml`. Les
prompts (`*.md`) ne sont pas recopiés : leur empreinte dit qu'ils ont changé, le dépôt de
configuration dit comment.
