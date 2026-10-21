---
name: reviewme-creer-relecteur
description: Crée ou adapte un relecteur ReviewMe (bot de code review IA) pour un dépôt — en activant et réglant un relecteur du core (tech, us, i18n, conseil) dans project.toml, ou en écrivant un relecteur custom (reviewer.toml + system.md). Explore le dépôt pour trouver doc et conventions, choisit la voie la plus légère, écrit une persona courte, puis valide la config. À utiliser dès qu'on veut brancher ReviewMe sur un projet, « ajouter un relecteur », « un reviewer qui vérifie X », configurer `.reviewme/`, `project.toml` ou `reviewer.toml`, ou faire relire les PR sur un sujet précis (sécurité, migrations, accessibilité, traductions…), même sans citer ReviewMe si le dépôt contient `.reviewme/`. Pas pour relire une PR soi-même ni pour modifier le moteur ReviewMe.
---

# Créer un relecteur ReviewMe

ReviewMe sépare **la méthode** (dans le moteur : chasse puis vérification, format de sortie,
sévérités, sécurité) et **la connaissance** (dans la config du projet : quoi chercher, où est
la doc, quand tourner). Tu ne touches qu'à la connaissance : ce que tu recopierais de la
méthode divergerait du moteur à sa prochaine version.

Hors périmètre : relire une PR, modifier le moteur, installer la CI. Si le dépôt n'a pas
encore ReviewMe en CI, signale-le et renvoie aux exemples `deploy/` du moteur.

## 1. Comprendre la demande et le dépôt

1. **Le projet** : la config vit sous `.reviewme/projects/<projet>/` du dépôt relu (ou dans
   le dépôt pointé par `REVIEWME_CONFIG_HOME`). Absente : prends le nom du dépôt.
2. **Ce que le relecteur cherche**, en une phrase. Demande vague (« un relecteur qualité ») :
   propose une portée précise plutôt que de deviner.
3. **Où est la doc** : `CONTRIBUTING.md`, `docs/`, `Documentation/`, ADR, guides, configs de
   linters. Note les **chemins** : le relecteur lit ces fichiers lui-même, à jour, à chaque PR.
4. **Son terrain** : les globs qui le déclenchent (`src/api/**`, `migrations/**`, `**/*.tsx`).
   Un relecteur sans `paths` tourne, et coûte, sur toutes les PR.

Le moteur lit déjà d'office `CLAUDE.md`, `AGENTS.md` (racine et dossiers touchés) et les
`.claude/rules/*.md` dont le `paths:` correspond : ne les déclare pas, ne les recopie pas.

## 2. Choisir la voie la plus légère

| Le besoin | Voie |
|---|---|
| Relecteur généraliste (bugs, logique, conventions) qui doit connaître la doc | **adapter** `tech` |
| Couverture des critères d'un ticket Jira | **adapter** `us` (demande `JIRA_*`) |
| Traductions manquantes ou douteuses | **adapter** `i18n` |
| Dire au relecteur humain quoi valider lui-même | **activer** `conseil` |
| Sujet propre au projet (règle métier, sécurité maison, migrations, accessibilité…) | **créer** un custom |

Adapter d'abord quand c'est possible : le relecteur suit le moteur. Les deux voies se
combinent dans un même projet.

## 3a. Adapter un relecteur du core

```toml
# .reviewme/projects/<projet>/project.toml
[reviewers]
core = ["tech", "conseil"]            # activer : ajouter le nom ; désactiver : le retirer

[reviewers.tech]
read = ["docs/architecture.md"]       # ajouté à sa lecture
paths = ["src/**"]                    # remplace son déclenchement
instructions = "Le dossier legacy/ est gelé : n'y signale que les bugs bloquants."
```

- `instructions` dit ce qui **n'est écrit nulle part**. Une règle déjà dans la doc passe par
  `read`, sinon tu crées une copie qui divergera.
- Seules 7 clés sont acceptées sous `[reviewers.<nom>]` : voir
  [references/reviewer-toml.md](references/reviewer-toml.md).
- Si un dossier `reviewers/<nom>/` existe, il prime et `[reviewers.<nom>]` est ignoré :
  modifie alors le dossier.

## 3b. Créer un relecteur custom

Un dossier `.reviewme/projects/<projet>/reviewers/<nom>/` avec deux fichiers ; son existence
suffit à l'activer.

- `reviewer.toml` : `output_mode` (`inline` pour des remarques sur les lignes, `global` pour
  un seul commentaire), `priority`, `[when] paths`, `[context] read`. Toutes les clés :
  [references/reviewer-toml.md](references/reviewer-toml.md).
- `system.md` : une demi-page, trois sections : **ce que tu cherches** (liste précise),
  **ce qui est voulu dans ce projet** (les faux positifs à éviter), **où est la règle**
  (les fichiers de `read`, sans les recopier).
- N'y mets pas ce que le moteur apporte : méthode, format JSON, échelle de sévérité,
  confiance, consignes de sécurité, langue des remarques.
- Une règle vérifiable mécaniquement (fonction absente, motif interdit) : propose un
  `precheck` (voir la référence) ; le relecteur garde le jugement.

Exemples complets (API, migrations, accessibilité) : [references/exemples.md](references/exemples.md).

## 4. Valider

Depuis le clone du moteur ReviewMe :

```sh
REVIEWME_CONFIG_HOME=<chemin absolu du dépôt>/.reviewme uv run reviewme projects
```

Le relecteur doit apparaître avec son mode, sa priorité et ses `paths`. Avec une PR et un
`GITHUB_TOKEN`, vérifie aussi la chaîne sans modèle ni post :

```sh
REVIEWME_FAKE=1 REVIEWME_CONFIG_HOME=<dépôt>/.reviewme PROJECT=<projet> REPO_PATH=<dépôt> \
  uv run reviewme review --repo <org>/<dépôt> --pr <n> --dry-run
```

| Symptôme | Cause et remède |
|---|---|
| `Aucun projet configuré` | chemin relatif ou faux dans `REVIEWME_CONFIG_HOME` : passe un chemin absolu |
| `clé(s) inconnue(s)` sous `[reviewers.<nom>]` | seules les 7 clés de la référence sont acceptées |
| `reviewer(s) du core inconnu(s)` | le nom n'est pas `tech`, `us`, `i18n` ou `conseil` |
| relecteur absent du dry-run, « non déclenché » | aucun fichier de la PR ne correspond à `paths`, ou `requires` non satisfait |
| `context.read : chemin(s) introuvable(s)` | corrige le chemin ; `AGENTS.md` absent pour `tech` du core est attendu et sans effet |

La config d'un `.reviewme/` versionné dans le dépôt est lue sur la branche de base : le
relecteur ne s'applique qu'une fois mergé. Pour l'essayer avant : un build lancé à la main
avec `REVIEWME_CONFIG_SOURCE=pr`.

## 5. Rendre compte

```markdown
**Relecteur** : <nom> — <adapté du core | custom> — <pourquoi cette voie>
**Il cherche** : <2-4 points>
**Il tourne sur** : <paths> ; **il lit** : <fichiers de read>
**Fichiers** : <créés / modifiés>
**Validation** : <sortie de reviewme projects> ; <dry-run fait ou ce qui manquait>
**À faire** : <merge, CI, secrets, s'il y a lieu>
```
