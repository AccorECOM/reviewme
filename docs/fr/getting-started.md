[← ReviewMe](../../README.fr.md) · [English](../getting-started.md) · **Français**

# Démarrer

Prérequis : Python 3.13, [uv](https://docs.astral.sh/uv/), la CLI
[Claude Code](https://docs.anthropic.com/en/docs/claude-code) dans le `PATH`, et un token
GitHub fine-grained limité au dépôt relu. `--dry-run` affiche ce qui serait posté sans rien
écrire sur GitHub ; `REVIEWME_FAKE=1` se passe complètement du modèle.

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

En CI : un exemple GitHub Actions est dans [`deploy/github-actions/`](../../deploy/github-actions/),
un exemple Bitrise dans [`deploy/bitrise-step.md`](../../deploy/bitrise-step.md).

Avec la config dans le dépôt relu, la CI doit pouvoir voir la branche base : lire
[D'où vient la config](how-it-works.md#doù-vient-la-config-branche-base) avant le premier
build, surtout pour les PR qui ne visent pas la branche par défaut.
