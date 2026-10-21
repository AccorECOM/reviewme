# ReviewMe

[English](README.md) · **Français**

**Un bot de code review par IA pour les Pull Requests GitHub.** Il lit le diff et le
dépôt, puis poste des commentaires **ligne par ligne**, comme un relecteur humain.
Plusieurs relecteurs spécialisés peuvent tourner sur la même PR : technique, sécurité,
traductions, critères du ticket…

```text
src/orders/service.py, ligne 42
[IMPORTANT] `charge()` est appelée sans `await` : le paiement part en tâche de fond et
l'erreur éventuelle est perdue. Ajouter `await` et laisser l'exception remonter.
```

## Installation

```sh
uv sync && npm install -g @anthropic-ai/claude-code
cp .env.example .env            # GITHUB_TOKEN, GITHUB_REPO, REPO_PATH
uv run reviewme review --repo mon-org/mon-app --pr 123 --dry-run
```

Prérequis : Python 3.13, [uv](https://docs.astral.sh/uv/), la CLI
[Claude Code](https://docs.anthropic.com/en/docs/claude-code) dans le `PATH`, et un token
GitHub fine-grained limité au dépôt relu. `--dry-run` affiche ce qui serait posté sans rien
écrire sur GitHub ; `REVIEWME_FAKE=1` se passe complètement du modèle. Le parcours complet
est dans [Démarrer en 5 minutes](#démarrer-en-5-minutes).

---

## Le principe : la méthode dans le moteur, la connaissance dans la config

ReviewMe sépare deux choses :

- **le moteur (ce dépôt)** sait *comment* relire : la méthode, le format de sortie, la
  vérification, la sécurité, le dialogue avec GitHub. Il est le même pour tous les projets ;
- **un relecteur (un dossier de config)** sait *quoi* chercher dans *ce* projet : sa
  persona, ses règles, quand il se déclenche. Une équipe en écrit autant qu'elle veut.

```mermaid
flowchart LR
    subgraph Moteur["Moteur (ce dépôt) : comment relire"]
        M1[Méthode en 2 phases]
        M2[Contrat de sortie JSON]
        M3[Passe de vérification]
        M4[Sécurité et GitHub]
    end
    subgraph Config["Config d'un projet : quoi chercher"]
        R1["reviewers/tech/"]
        R2["reviewers/securite/"]
        R3["reviewers/i18n/"]
        C["common/ (règles d'équipe)"]
    end
    Config --> Moteur --> PR[(Pull Request)]
```

Un projet combine deux sortes de relecteurs, qui s'activent différemment :

| | Relecteur du core | Relecteur custom |
|---|---|---|
| Où il est défini | dans le moteur | dans votre dépôt, `reviewers/<nom>/` |
| Comment l'activer | son nom dans `core = [...]` du `project.toml` | son dossier existe : rien d'autre à déclarer |
| Comment le couper | retirer son nom de la liste | `enabled = false` dans son `reviewer.toml` |
| Mises à jour | suit le moteur | les vôtres |

Exemple complet d'un projet qui utilise les deux :

```text
.reviewme/projects/boutique/
├── project.toml              [reviewers] core = ["tech", "advisor"]
└── reviewers/
    └── securite/             relecteur custom, actif parce que le dossier existe
        ├── reviewer.toml
        └── system.md
```

Trois relecteurs tournent sur ses PR : `tech` et `advisor` (core), `securite` (custom).

### Relecteurs du core : à activer par leur nom

Fournis par le moteur (`config/templates/reviewers/`), ils suivent ses mises à jour. On les
active dans le `project.toml` du projet ; retirer un nom les désactive.

```toml
# .reviewme/projects/boutique/project.toml
[reviewers]
core = ["tech", "advisor"]
```

| Nom | Rôle | Sortie | Prérequis |
|---|---|---|---|
| `tech` | bugs, logique, sécurité, conventions du dépôt | en ligne | — |
| `us` | les critères d'acceptation du ticket sont-ils couverts ? | un commentaire, muet si tout est couvert | Jira (`JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`) |
| `i18n` | traductions manquantes ou douteuses | en ligne et résumé | fichiers de traduction dans la PR (`*.strings`, `values*/strings.xml`…) |
| `advisor` | ce qu'un humain doit valider lui-même sur cette PR | un commentaire, muet si rien d'applicable | — |

**Les adapter sans les copier** : le projet peut indiquer à un relecteur du core où est
sa doc, quand tourner et ce qu'il doit savoir. Le reste (méthode, sortie, vérification)
reste celui du moteur.

```toml
[reviewers.tech]
read = ["Documentation/ARCHITECTURE.md", "Documentation/"]   # ajouté à sa lecture
paths = ["src/**"]                                            # quand il tourne
instructions = "Les services métier sont dans src/services ; pas d'appel réseau dans les vues."
# model = "…"   max_turns = 50   priority = 10   enabled = false
```

**Laisser une IA le faire** : le skill [`skills/reviewme-create-reviewer`](skills/reviewme-create-reviewer/SKILL.md)
guide Claude Code pour explorer le dépôt, choisir entre adapter un relecteur du core et en
créer un, écrire une persona courte et valider la config. Copiez-le dans `.claude/skills/`
du projet, puis demandez par exemple « ajoute un relecteur qui vérifie nos migrations ».

### Relecteurs custom : un dossier dans le projet

Pour ce qui est propre à **votre** code, un relecteur est un dossier sous
`.reviewme/projects/<projet>/reviewers/<nom>/`, dans le dépôt relu (ou dans un dépôt de
config à part, via `REVIEWME_CONFIG_HOME`). Son nom est libre ; un dossier qui porte le nom
d'un relecteur du core le remplace pour ce projet.

```text
.reviewme/projects/boutique/
├── project.toml                 réglages du projet (optionnel)
├── common/
│   └── team-rules.md            règles de l'équipe, communes à tous les relecteurs
└── reviewers/
    └── securite/
        ├── reviewer.toml        quand il tourne, ce qu'il lit
        └── system.md            sa persona : ce qu'il cherche dans CE projet
```

```toml
# reviewers/securite/reviewer.toml
output_mode = "inline"                 # inline | global | mixed
priority    = 10

[when]                                 # ne tourne que si ces fichiers changent
paths = ["src/api/**", "src/auth/**"]

[context]                              # lus dans le dépôt relu, jamais recopiés ici
read = ["docs/securite.md"]

```

```markdown
<!-- reviewers/securite/system.md -->
# Relecteur sécurité de la boutique
Tu cherches : routes sans contrôle d'accès, SQL construit par concaténation, secrets en
dur, données client écrites dans les logs. Les décorateurs `@public` sont volontaires.
```

Le moteur ajoute tout seul la méthode, le format de sortie et les fichiers d'instructions
du dépôt (`CLAUDE.md`, `AGENTS.md`, `.claude/rules/`). La persona reste courte.

---

## Le parcours d'une PR

```mermaid
flowchart TD
    A[PR ouverte ou mise à jour] --> B{Quels relecteurs ?}
    B -->|"when.paths, ticket présent…"| C[Contexte : règles du dépôt,<br/>ticket, faits du precheck]
    C --> D["Phase 1 + 2 : claude -p<br/>chasse, puis preuve par remarque"]
    D --> E["Vérification : un 2e claude -p<br/>confirme ou infirme chaque remarque"]
    E -->|infirmée| X[(Registre seulement)]
    E -->|confirmée| F{Filtres}
    F -->|"BLOCKER, IMPORTANT"| G[Commentaire en ligne]
    F -->|"MINOR, code déplacé, hors diff"| H[Résumé unique]
    F -->|"déjà dit, plafond atteint"| X
    G --> R[(Registre)]
    H --> R
```

- **Chaque relecteur est un `claude -p` indépendant**, en lecture seule (`Read`, `Grep`,
  `Glob`, `git log/show/diff`), lancé en parallèle.
- **La vérification remplace le seuil de confiance comme filtre principal.** Un second
  appel reçoit les remarques et doit citer la ligne qui prouve chacune ; ce qui n'est pas
  prouvé n'est pas posté.
- **Pas de doublon d'un commit à l'autre** : chaque commentaire porte une empreinte de sa
  ligne. Si la ligne change, le bot répond dans le fil au lieu de reposter.

---

## Démarrer en 5 minutes

```sh
git clone https://github.com/AccorECOM/reviewme.git && cd reviewme
uv sync
npm install -g @anthropic-ai/claude-code
cp .env.example .env            # GITHUB_TOKEN, GITHUB_REPO, REPO_PATH

uv run reviewme review --repo mon-org/mon-app --pr 123 --dry-run
```

`--dry-run` calcule tout et affiche ce qui **serait** posté, sans rien écrire sur GitHub.
Sans `PROJECT`, un seul relecteur générique tourne (mode simple). Pour un vrai projet, le
plus court est un `project.toml` qui active des relecteurs du core (voir plus haut) :

```sh
mkdir -p .reviewme/projects/mon-app
printf '[reviewers]\ncore = ["tech", "advisor"]\n' > .reviewme/projects/mon-app/project.toml
REVIEWME_CONFIG_HOME=$PWD/.reviewme PROJECT=mon-app uv run reviewme projects
```

Pour partir d'une copie modifiable d'un relecteur du core :
`reviewme init-project mon-app --reviewers tech`.

En CI : un exemple GitHub Actions est dans [`deploy/github-actions/`](deploy/github-actions/),
un exemple Bitrise dans [`deploy/bitrise-step.md`](deploy/bitrise-step.md).

---

## Ce qui arrive sur la PR

| Remarque | Où | Pourquoi |
|---|---|---|
| `BLOCKER`, `IMPORTANT` sur du code introduit par la PR | en ligne | c'est ce qui mérite l'attention du dev |
| `MINOR` | résumé | utile, mais pas au prix d'une notification par ligne |
| sur du code seulement **déplacé** par la PR | résumé, tagué | ce n'est pas un défaut de la PR |
| hors des lignes du diff | résumé | GitHub refuse un commentaire hors diff |
| non confirmée par la vérification | nulle part (registre) | un faux positif coûte la confiance de l'équipe |
| **💡 Conseil pour la relecture** (relecteur `advisor`) : un point qu'un humain doit valider lui-même (ordre d'appels modifié, fichier rangé hors de son dossier habituel…) | un commentaire | seulement s'il est applicable, ancré dans des fichiers de la PR ; 3 au plus |
| PR propre | rien | le silence vaut mieux qu'un « RAS » |

La langue des remarques postées et des titres du bot se règle par projet dans
`project.toml` : `[review] language = "fr"` (défaut `en`).

Le bot ne poste que des commentaires : jamais d'approbation ni de demande de changement.

---

## Mesurer si c'est utile

```mermaid
flowchart LR
    R[review] -->|chaque remarque,<br/>postée ou non| L[(registre)]
    N["enrich (chaque nuit)"] -->|"👍/👎, réponses,<br/>ligne corrigée ?"| L
    L --> S[reviewme stats]
    L --> X["reviewme export"]
    X --> A[archive JSONL<br/>pour l'analyse]
    X --> W[weekly-report.md<br/>pour l'équipe]
```

Le registre garde **toutes** les remarques avec leur sort et leur confiance, y compris
celles écartées. On peut donc répondre à « ce seuil nous coûte-t-il des vrais bugs ? ».
`reviewme stats` affiche, par relecteur, la part de remarques suivies d'effet, approuvées
et contestées, et la calibration par tranche de confiance. Le schéma est décrit dans
[`docs/ledger-schema.md`](docs/ledger-schema.md) (en anglais). Le texte des remarques n'y est écrit
que si le projet le demande (`[ledger] include_text = true`).

---

## Sécurité

Le diff, le ticket et les commentaires sont écrits par des tiers : ce sont des
**données**, jamais des instructions.

- **Outils en lecture seule**, liste verrouillée dans le code : ni `Write`, ni sous-agents,
  ni `--dangerously-skip-permissions`. Ni les hooks ni les serveurs MCP du dépôt relu ne
  sont chargés.
- **Une PR ne change pas sa propre relecture** : si la config vit dans le dépôt relu
  (`.reviewme/`), elle est lue sur la branche de base, tout comme `CLAUDE.md` et
  `.claude/rules/`. Pour tester un nouveau relecteur dans sa PR, on lance un build à la
  main avec `REVIEWME_CONFIG_SOURCE=pr`.
- Secrets et chemins absolus masqués avant tout post ; webhook refusé sans signature HMAC.
- Un `precheck` est du code fourni par le projet : il se relit comme du code.

---

## Référence rapide

| Commande | Rôle |
|---|---|
| `reviewme review --repo O/R --pr N` | relire une PR (`--dry-run`, `--force`) |
| `reviewme init-project <nom> --reviewers tech,us` | créer un projet depuis le squelette |
| `reviewme projects` | lister les projets et leurs relecteurs |
| `reviewme enrich --since 14d` | relever le devenir des remarques (job de nuit) |
| `reviewme export --since 7d --out DIR` | archive d'analyse et rapport lisible |
| `reviewme stats [--html F]` | compteurs et bloc qualité |
| `reviewme feedback --pr N` | réponses des devs, en règles à relire |
| `reviewme-webhook` · `reviewme-poll` · `reviewme-web` | serveur webhook, boucle de poll, tableau de bord |

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
| `REVIEWME_FAKE`, `REVIEWME_DEBUG` | sans appel au modèle ; journal détaillé |
| `ANTHROPIC_BASE_URL`, `CLAUDE_MODEL` | passerelle LLM, modèle |

| Clé du `project.toml` | Rôle |
|---|---|
| `[review] language` | langue des remarques postées et des titres du bot (défaut `en`, ex. `"fr"`) |

Toutes les clés, commentées : [`.env.example`](.env.example). Toutes les options de
`reviewer.toml` : [`docs/reviewme.html`](docs/reviewme.html). Limites connues :
[`docs/LIMITATIONS.md`](docs/LIMITATIONS.md).

## Tests

```sh
uv sync --group dev
uv run pytest -q
uv run ruff check .
```

## Contribuer et sécurité

Guide dans [`CONTRIBUTING.md`](CONTRIBUTING.md). Modèle de menace et signalement d'une
vulnérabilité dans [`SECURITY.md`](SECURITY.md).

## Licence

Apache 2.0 — libre d'utilisation, de modification et de partage. Voir [`LICENSE`](LICENSE).
