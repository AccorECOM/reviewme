# Déploiement Bitrise

Bitrise **orchestre**, ReviewMe fait la review one-shot. Le workflow est hors `trigger_map`
et se déclenche par l'API Build Trigger, ou sur un trigger `pull_request` selon ce que
veut l'équipe.

## Un runner Linux, même sur une app iOS

La review **ne compile rien** : elle lit un diff et des fichiers texte. Elle n'a besoin ni de
Xcode, ni d'un simulateur, ni du toolchain Swift. Un stack macOS n'apporterait rien et coûte
nettement plus cher à la minute.

Bitrise permet de surcharger le stack **par workflow**, y compris dans une app iOS dont le
stack par défaut est macOS :

```yaml
  ReviewMe:
    summary: Review IA de la PR courante (commentaires inline)
    meta:
      bitrise.io:
        stack: ubuntu-noble-24.04-bitrise-2025    # pas de Xcode nécessaire
        machine_type_id: g2.linux.medium
    steps:
    - activate-ssh-key@4: {}
    - git-clone@8: {}                             # clone le repo à reviewer
    - script@1:
        title: ReviewMe
        inputs:
        - content: |
            #!/usr/bin/env bash
            set -eo pipefail        # PAS de `set -x` : ce step manipule des secrets

            # 1. Outils, en versions ÉPINGLÉES et vérifiées — ce job porte des secrets.
            npm install -g "@anthropic-ai/claude-code@<version>"   # npm vérifie l'intégrité
            # uv : archive de release + SHA256 publié par l'éditeur (voir bitrise-ah-ios.yml)
            export PATH="$HOME/.local/bin:$PATH"
            echo '{"hasCompletedOnboarding": true}' > ~/.claude.json

            # 2. La config : un seul clone, le moteur vient avec (submodule .core)
            git clone --recurse-submodules "$REVIEWME_INSTANCE_URL" /tmp/reviewme-config

            # 3. Review
            cd /tmp/reviewme-config
            ./reviewme review --repo "$GITHUB_REPO" --pr "$PR_NUMBER"
        # Secrets : jamais en clair ici, déclarés dans les Secrets Bitrise de l'app
```

## Variante la plus simple : la config dans le dépôt reviewé

Si le projet porte sa propre configuration (`.reviewme/` à sa racine), il n'y a **rien à
cloner en plus** : le dépôt est déjà là, on pointe dedans.

```bash
# 1. Outils
curl -fsSL https://claude.ai/install.sh | bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

# 2. Le moteur (dépôt public, aucun credential)
# Épingler un COMMIT, pas une branche ni un tag : un tag est déplaçable.
git clone --quiet --filter=blob:none --no-checkout <url-du-moteur> /tmp/reviewme
git -C /tmp/reviewme checkout --quiet <sha-complet>
cd /tmp/reviewme && uv sync --frozen

# 3. La config vient du dépôt reviewé
export REVIEWME_CONFIG_HOME="$BITRISE_SOURCE_DIR/.reviewme"
export PROJECT=mon-projet
export REPO_PATH="$BITRISE_SOURCE_DIR"
uv run reviewme review --pr "$PR"
```

⚠️ Protéger `.reviewme/` par une entrée `CODEOWNERS` : sans ça, une PR peut modifier la
configuration qui la juge.

## Variables

| Variable | Origine | Rôle |
|---|---|---|
| `GITHUB_TOKEN` | Secret Bitrise | PR R/W sur le repo cible (ou GitHub App, cf. `GITHUB_APP_*`) |
| `ANTHROPIC_API_KEY` | Secret Bitrise | Moteur de review. Via une passerelle interne : `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` |
| `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS` | `1` | Les proxies internes rejettent souvent les en-têtes de prompt caching |
| `REVIEWME_INSTANCE_URL` | Env du workflow | Le dépôt de config (avec le moteur en submodule `.core`) |
| `PROJECT` | Env du workflow | Quel projet de l'instance utiliser |
| `REPO_PATH` | `$BITRISE_SOURCE_DIR` | Le clone à explorer pour contextualiser les findings |
| `PR_NUMBER` | `$BITRISE_PULL_REQUEST` | La PR à reviewer |

## Runner éphémère : ce que ça implique

| | Effet |
|---|---|
| **Dédup des commentaires** | Aucun. La vérité est GitHub : les marqueurs vivent dans les commentaires de la PR, pas sur le disque du runner. |
| **`data/state.json`** | Perdu à chaque build. Conséquence : une **relance sur le même commit** repaie une review qui ne postera rien (tout dédupliqué). Sur un nouveau commit, aucun impact. |
| **Dashboard / hall of fame** | Ne fonctionne pas : il lit `data/reviews/*.json`, effacé avec le runner. Il faut un store partagé — chantier séparé. |

Ne **pas** exporter `data/` en artifact de build pour contourner : les artifacts sont
téléchargeables par quiconque a accès au build, et `data/` contient le contenu de PR réelles.

## Premier branchement

Lancer avec `--dry-run` sur quelques PR, lire ce qui *aurait* été posté, ajuster
`CONFIDENCE_THRESHOLD` et `MAX_COMMENTS_PER_PR`, et seulement ensuite retirer le flag.
