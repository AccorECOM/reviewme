# Référence : project.toml et reviewer.toml

Source de vérité : `reviewme/projects.py` du moteur. En cas de doute, lis-le.

## project.toml — `.reviewme/projects/<projet>/project.toml`

| Clé | Rôle |
|---|---|
| `[reviewers] core = [...]` | relecteurs du core activés : `tech`, `us`, `i18n`, `conseil` |
| `[reviewers.<nom>] read` | fichiers/dossiers AJOUTÉS à sa lecture (fichier = contenu, dossier = inventaire des `.md`) |
| `[reviewers.<nom>] paths` | remplace son `when.paths` |
| `[reviewers.<nom>] instructions` | texte ajouté à la fin de sa persona |
| `[reviewers.<nom>] model`, `max_turns`, `priority`, `enabled` | remplacent les valeurs du core |
| `[ledger] include_text` | le registre garde le texte des remarques (défaut `false`) |

Toute autre clé sous `[reviewers.<nom>]` bloque le chargement. `[reviewers.<nom>]` est ignoré
si un dossier `reviewers/<nom>/` existe (le dossier prime).

## reviewer.toml — `.reviewme/projects/<projet>/reviewers/<nom>/reviewer.toml`

| Clé | Valeurs | Défaut |
|---|---|---|
| `output_mode` | `inline` (remarques sur les lignes), `global` (un seul commentaire), `mixed` | `inline` |
| `priority` | entier, petit = d'abord | 100 |
| `[when] paths` | globs ; absent = toute PR | — |
| `[context] read` | chemins dans le dépôt relu | — |
| `requires` | `["jira_ticket"]` seulement ; sinon le relecteur est écarté | — |
| `precheck` | script dans le dossier du relecteur ; reçoit le diff sur stdin, écrit des faits en markdown | — |
| `max_turns` | entier > 0 | 30 |
| `model` | identifiant de modèle | celui du job |
| `enabled` | `false` = en sommeil | `true` |
| `silent_when_clean` | rien posté quand rien à dire ; **mode `global` uniquement** | `false` |
| `advice` | autorise les « Conseil pour la relecture » | `false` |
| `findings` | `false` = ne poste jamais de remarque | `true` |
| `[verification] enabled`, `model`, `max_turns` | passe de vérification des remarques | activée |
| `[mcp] config` | fichier MCP dans le dossier du relecteur | — |

## Quand écrire un precheck

Une règle qui se vérifie mécaniquement (une fonction absente, un motif interdit, une clé de
traduction manquante) coûte moins cher et ne se trompe pas en script. Le precheck écrit des
faits (« `0003_x.py` : `downgrade()` vide ») ; le relecteur garde le jugement. C'est du code
exécuté en CI : à relire comme du code, sans accès réseau ni écriture.
