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

Python 3.13, [uv](https://docs.astral.sh/uv/) et la CLI
[Claude Code](https://docs.anthropic.com/en/docs/claude-code). `--dry-run` affiche ce qui
serait posté sans rien écrire sur GitHub. Étape suivante : [Démarrer](docs/fr/getting-started.md).

## Le fonctionnement, en bref

- **Le moteur (ce dépôt)** sait *comment* relire : la méthode, le format de sortie, la
  passe de vérification, la sécurité, GitHub. **Un relecteur (un dossier de config)** sait
  *quoi* chercher dans *votre* projet : une persona courte, ses règles, quand il tourne.
- Chaque relecteur est un `claude -p` indépendant, en **lecture seule**. Un second appel
  **vérifie** ensuite chaque remarque et doit citer la ligne qui la prouve : ce qui n'est
  pas prouvé n'est pas posté.
- `BLOCKER` et `IMPORTANT` vont **en ligne**, le reste dans un commentaire de synthèse.
  Pas de doublon d'un commit à l'autre. Le bot n'approuve ni ne bloque jamais une PR.
- **Une PR ne peut pas changer sa propre review** : une config dans le dépôt est lue sur la
  branche base, et seulement sur une branche de confiance.
- Chaque remarque, postée ou non, va dans un **registre** ; un tableau de bord dit si le
  bot vaut le coup.

## Documentation

| Page | Contenu |
|---|---|
| [Démarrer](docs/fr/getting-started.md) | premier dry-run, premier projet, exemples CI |
| [Relecteurs](docs/fr/reviewers.md) | relecteurs du core (`tech`, `us`, `i18n`, `advisor`), les adapter, écrire les vôtres |
| [Comment se déroule une review](docs/fr/how-it-works.md) | le parcours d'une PR, d'où vient la config, **quand une PR n'est pas relue** |
| [Ce qui arrive sur la PR](docs/fr/on-the-pr.md) | en ligne ou en synthèse, langue, modèle et score, note sur PR propre |
| [Mode watch](docs/fr/watch-mode.md) | le lancer sur sa machine, sans CI |
| [Mesurer si c'est utile](docs/fr/measuring.md) | registre, `reviewme stats`, tableau de bord, `[stats]`, ROI |
| [Historique, ères et tri](docs/fr/history-and-triage.md) | scan avant/après, ères nommées, pourquoi les remarques sont contestées |
| [Sécurité](docs/fr/security.md) | ce que le moteur garantit, ce qu'il reste à faire ([modèle de menace](SECURITY.md)) |
| [Référence](docs/fr/reference.md) | toutes les commandes, variables et clés de `project.toml` |

Aussi : [limites connues](docs/LIMITATIONS.md), [schéma du registre](docs/ledger-schema.md),
toutes les options de `reviewer.toml` dans [`docs/reviewme.html`](docs/reviewme.html),
toutes les variables commentées dans [`.env.example`](.env.example),
[changelog](CHANGELOG.md) (en anglais).

## Contribuer et licence

Tests, conventions et invariants : [`CONTRIBUTING.md`](CONTRIBUTING.md). Signaler une
vulnérabilité : [`SECURITY.md`](SECURITY.md). Apache 2.0, voir [`LICENSE`](LICENSE).
