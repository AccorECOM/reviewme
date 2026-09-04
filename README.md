# ReviewMe

Bot de **code review par IA** pour les Pull Requests GitHub. Il poste de **vrais commentaires
inline ligne-par-ligne**, gère le **cycle de vie des threads** (dédup, réponses, re-review sur
nouveau commit), et fait tourner **plusieurs reviewers spécialisés** — technique, couverture
des critères d'acceptation, traductions — chacun avec ses entrées et son format de sortie.

Quatre points d'entrée : CLI one-shot, webhook, poll, ou CI (GitHub Actions / Bitrise).

📖 **Documentation complète : [`docs/reviewme.html`](docs/reviewme.html)**
🧭 Limites connues et reste à faire : [`ROADMAP.md`](ROADMAP.md)

---

## Les deux dépôts

Le **moteur** (ce dépôt) ne contient aucune config de projet. La configuration vit dans un
**dépôt d'instance** séparé — ni fork, ni submodule : juste un chemin.

```
reviewMe/                       ← le moteur, git pull-able sans conflit
  reviewme/                     le code
  config/output-contract.md     l'interface du parseur (jamais surchargée)
  config/templates/             le squelette copié par `init-project`

mon-instance/                   ← une instance (dépôt privé, versionné à part)
  reviewme                      lanceur : localise le moteur et lui passe la main
  .env                          repo cible, token, seuils (prime sur celui du moteur)
  projects/<repo>/
    common/*.md                 consignes communes aux reviewers du projet
    reviewers/<id>/             un dossier = un reviewer
```

Les deux sont reliés par `REVIEWME_CONFIG_HOME` — un simple chemin, donc trois stratégies :

| Stratégie | Où vit la config | Quand |
|---|---|---|
| Dépôt d'instance dédié | un dépôt privé à part | plusieurs repos, config mutualisée et hors de portée des PR |
| Dans le dépôt reviewé | `.reviewme/` à sa racine | un seul repo ; la config évolue dans la même PR que le code |
| Aucune config | — | démarrer, tester la chaîne |

Une instance porte **un ou plusieurs projets** : `projects/api/`, `projects/webapp/`,
`projects/mobile/`… `PROJECT` désigne lequel utiliser. Passer d'un dépôt unique à un
monorepo, c'est ajouter un dossier — rien à migrer.

Config dans le dépôt reviewé : `REVIEWME_CONFIG_HOME=$CLONE/.reviewme`. Rien à créer ni à
cloner en plus — mais protéger `.reviewme/` par `CODEOWNERS`, sinon une PR peut adoucir sa
propre review. Une organisation a son instance, avec ses
consignes internes, sans jamais toucher au moteur.

## Installation

Trois façons de relier le moteur et une instance. La doc HTML détaille chacune
([`docs/reviewme.html`](docs/reviewme.html) § Installation).

Un script interactif vérifie les prérequis et génère un `.env` :

```sh
./setup.sh
```

Sinon, à la main :

**En local — deux clones**

```sh
git clone <moteur> ~/dev/reviewMe && cd ~/dev/reviewMe
uv sync                                   # + `--extra github-app` pour l'auth GitHub App
npm install -g @anthropic-ai/claude-code

git clone <instance> ~/dev/mon-instance && cd ~/dev/mon-instance
cp .env.example .env                      # GITHUB_TOKEN, GITHUB_REPO, REPO_PATH
./reviewme projects                       # le lanceur trouve le moteur tout seul
```

Le lanceur cherche le moteur dans `REVIEWME_CORE`, puis `./.core`, puis un clone voisin.

**En CI — un seul clone.** L'instance monte le moteur en submodule et épingle sa version :

```sh
git submodule add <moteur> .core          # une fois, dans l'instance

git clone --recurse-submodules <instance> config    # en CI
cd config && ./reviewme review --pr "$PR_NUMBER"
```

Aucune variable à poser ; sans `.venv` pré-construit le lanceur passe par `uv run`, donc `uv`
est la seule dépendance. C'est **l'instance qui épingle le moteur**, jamais l'inverse : le
moteur ne référence aucune instance et reste publiable.

**Sans instance — le mode simple.** Un dépôt, un reviewer, pas de personas spécialisées :

```sh
git clone <moteur> reviewme && cd reviewme && uv sync
cp .env.example .env                      # sans PROJECT ni REVIEWME_CONFIG_HOME
uv run reviewme review --repo OWNER/REPO --pr 1234 --dry-run
```

**Créer une instance depuis zéro**

```sh
mkdir ~/dev/mon-instance && cd ~/dev/mon-instance && git init
export REVIEWME_CONFIG_HOME=$PWD
~/dev/reviewMe/.venv/bin/python -m reviewme.cli init-project mon-repo --reviewers tech,i18n
```

Toujours committer le `.gitignore` en premier (il protège `.env` et `data/`), et lancer la
première review en `--dry-run` : tout est calculé, rien n'est posté.

## Les reviewers

Un reviewer = un dossier dans `projects/<repo>/reviewers/`. Son **nom de dossier est son id**, libre (`tech`, `us`, `i18n` sont des conventions). Sa présence suffit à l'activer
(pas de manifeste à tenir à jour) ; `enabled = false` dans son `reviewer.toml` le met en
sommeil.

| id | Objectif | Entrées | Sortie | Déclenchement |
|---|---|---|---|---|
| `tech` | archi, sécurité, perf, fiabilité | diff + conventions du repo | inline | toute PR |
| `us` | les critères d'acceptation sont-ils couverts ? | ticket Jira + diff | 1 commentaire global | clé de ticket détectée |
| `i18n` | complétude et qualité des traductions | fichiers de locale + diff | mixte | `when.paths` |

Ce sont des **objectifs types** (`config/templates/`), pas des prompts partagés : chaque projet
part du squelette puis écrit sa propre persona. Il n'y a volontairement aucun héritage entre
projets — deux équipes sur deux produits n'ont pas les mêmes exigences.

### `reviewer.toml`

```toml
output_mode = "inline"      # inline | global | mixed
priority    = 10            # ordre d'exécution (petit = prioritaire)
# model     = "..."         # modèle de CE reviewer ; vide = CLAUDE_MODEL, sinon défaut CLI
# requires  = ["jira_ticket"]   # contexte obligatoire, sinon skippé. Seule valeur
                              # possible à ce jour ; une valeur inconnue est refusée.
# precheck  = "precheck.py"     # script déterministe : les faits avant le LLM
# enabled   = false             # en sommeil sans supprimer le dossier

[when]                      # sans cette section, le reviewer tourne sur toute PR
paths = ["**/*.strings", "**/i18n/**/*.json"]

[context]                   # conventions LUES DANS LE DÉPÔT reviewé, jamais recopiées ici
read = ["AGENTS.md", "docs/"]

[plugins]                   # skills/agents Claude Code requis, installés avant la review
marketplaces = ["owner/marketplace-repo"]
install      = ["mon-plugin"]
```

Référence complète des clés (valeurs possibles et défauts) : [`docs/reviewme.html`](docs/reviewme.html) § Projets & instances.

`[context] read` est le point important : les conventions d'un projet vivent déjà dans son
dépôt. Un **fichier** voit son contenu injecté, un **dossier** son inventaire ; un chemin
introuvable déclenche un WARNING — un chemin mort se voit au lieu de priver silencieusement
le reviewer de ses règles.

## Recettes

| Je veux… | Faire |
|---|---|
| Lancer un reviewer seulement si certains fichiers changent | `[when] paths = ["**/*.strings"]` |
| Le désactiver sans supprimer son dossier | `enabled = false` |
| Voir ce qui serait posté, sans rien poster | `--dry-run` |
| Moins de commentaires | `MAX_COMMENTS_PER_PR=5` |
| Moins de remarques incertaines | `CONFIDENCE_THRESHOLD=90` |
| Un modèle moins cher pour un reviewer | `model = "…"` dans son `reviewer.toml` |
| Ne pas payer un LLM pour un fait vérifiable | `precheck = "precheck.py"` |
| Qu'il connaisse nos conventions | `[context] read = ["AGENTS.md", "docs/"]` |
| Qu'il lise le ticket | `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` |
| Passer par une passerelle LLM | `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` |
| Un seul clone en CI | `git submodule add <moteur> .core` |

Liste complète : [`docs/reviewme.html`](docs/reviewme.html) § Recettes.

## Comment ça tourne

```
PR ──► sélection des reviewers (activés par le projet, déclenchés par le diff)
         │
         ├─ précheck déterministe (gratuit)  ─┐
         ├─ conventions lues dans le dépôt   ─┤► prompt ─► claude -p ─► findings JSON
         └─ contexte externe (Jira, option.) ─┘
         │
    exécution EN PARALLÈLE, un reviewer = un process
         │
    validation des lignes contre le diff (anti-422)
    dédup par fingerprint (reviewer, contenu de ligne)
    plafond de commentaires par PR
         │
    UNE SEULE écriture GitHub
```

## Commandes

| Commande | Rôle |
|---|---|
| `reviewme review --repo O/R --pr N` | Review one-shot. `--dry-run`, `--force` |
| `reviewme projects` | Liste les projets de l'instance et leurs reviewers |
| `reviewme init-project <repo> --reviewers tech,us` | Crée un projet depuis le squelette |
| `reviewme stats [--json\|--html <fichier>]` | Compteurs agrégés, ou rapport HTML autonome |
| `reviewme feedback --pr N` | Retours des développeurs, en règles attribuées à relire |
| `reviewme-poll` | Boucle de poll (VPS) |
| `reviewme-webhook` | Serveur webhook GitHub (HMAC obligatoire) |
| `reviewme-web` | Dashboard read-only sur `127.0.0.1:8420` |

## Configuration

Tout par variables d'environnement (`.env`), secrets jamais dans le dépôt. Les clés
essentielles :

| Clé | Rôle |
|---|---|
| `REVIEWME_CONFIG_HOME` | Dépôt d'instance. Vide = `config/projects/` du moteur |
| `PROJECT` | Projet à utiliser. Vide = mode simple : un seul reviewer, issu de `config/templates/` |
| `GITHUB_TOKEN` **ou** `GITHUB_APP_ID` + `GITHUB_APP_PRIVATE_KEY_PATH` | Auth |
| `REPO_PATH` | Clone local du repo reviewé (contexte de l'agent) |
| `CONFIDENCE_THRESHOLD` | Findings sous ce seuil non postés (défaut 80) |
| `MAX_COMMENTS_PER_PR` | Plafond par PR, tous reviewers confondus (défaut 10) |
| `MAX_BUDGET_USD` | Plafond de coût **par reviewer** |
| `CLAUDE_BIN` | Binaire de la CLI de review (vide = `claude` du PATH) |
| `ANTHROPIC_BASE_URL` | Passerelle interne — héritée telle quelle par `claude -p` |

Liste complète et commentée dans [`.env.example`](.env.example).

## Sécurité

- Allowlist d'outils **verrouillée dans le code**, jamais surchargeable par une config de
  projet ; pas de `Write`, jamais `--dangerously-skip-permissions`.
- Diff et ticket traités comme des **données non fiables** (prompt injection).
- Scrub des secrets et des chemins absolus avant tout post, avec un garde-fou final.
- Webhook fail-closed sans `WEBHOOK_SECRET` (HMAC).
- Un `precheck` est du **code exécuté** fourni par un projet : à relire comme du code.
- Mitigation de fond attendue : sandbox de déploiement (pas de réseau sortant, FS confiné).

## Tests

```sh
.venv/bin/python tests/test_fanout.py               # fan-out, sélection, plafond, contexte
.venv/bin/python tests/test_fingerprint_migration.py # dédup et migration des marqueurs
# ou : pytest tests/
```
