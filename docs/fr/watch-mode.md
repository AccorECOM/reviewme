[← ReviewMe](../../README.fr.md) · [English](../watch-mode.md) · **Français**

# Le lancer sur sa machine (mode watch)

Pas besoin de CI : `reviewme watch` regarde les PR ouvertes d'un dépôt toutes les
quelques minutes et relit chaque PR, à nouveau après chaque push. Il appelle la CLI Claude Code telle
qu'elle est configurée sur la machine : ReviewMe ne décide pas comment la CLI accède au
modèle. Si `claude -p "bonjour"` marche dans ce terminal, watch marche.

```sh
git clone git@github.com:my-org/my-app.git ~/src/my-app
uv run reviewme watch --repo my-org/my-app --repo-path ~/src/my-app --every 10m
```

Commencez par `--once --dry-run` : un seul tour, rien n'est posté, le log dit quelles PR
seraient relues et pourquoi les autres sont laissées de côté.

| Quelles PR | Par défaut | Pour changer |
|---|---|---|
| PR ouvertes | toutes | `--label review-me` : seulement celles qui portent ce label |
| PR avec le label `no-ai-review` | ignorées | `--skip-label NOM` (`""` le désactive, ou `REVIEWME_SKIP_LABEL`) |
| drafts | ignorées | `--include-drafts` |
| PR venant d'un fork | ignorées | `--include-forks` (auteurs extérieurs au dépôt) |
| commit déjà relu | pas relu à nouveau | un nouveau push est relu |
| commit dont la relecture a échoué | pas retenté | un nouveau push est retenté |

Ce qu'il fait :

- **Votre clone n'est jamais touché.** Chaque PR est extraite dans un `git worktree`
  temporaire, au commit de tête, à côté du clone, puis supprimée. Le travail non commité
  et la branche courante restent tels quels. `--repo-path` (ou `REPO_PATH`) doit être un
  clone de `--repo` : sinon, refus.
- **La même relecture qu'en CI** : mêmes relecteurs, même config (toujours lue sur la
  branche de base), même dédoublonnage. Lancer watch à côté d'un job CI ne double pas
  les commentaires.
- **Une PR à la fois** par défaut (`--parallel N` pour changer), toutes les 10 minutes
  (`--every 90s|10m|1h`, ou `POLL_INTERVAL` ; minimum 1 minute).
- **Limites d'usage** : quand le quota du modèle est atteint, le commit n'est *pas*
  marqué en échec. La boucle attend l'heure de reset donnée par la CLI (sinon elle double
  l'attente, jusqu'à une heure), puis reprend là où elle s'était arrêtée.
- Les commentaires sont postés avec l'identité de `GITHUB_TOKEN` : votre propre compte si
  c'est votre token personnel, un bot si c'est une GitHub App ou le token d'un compte bot.
- Un seul watch par dépôt à la fois (verrou dans `data/`). `--dashboard` sert le tableau
  de bord en lecture seule sur `127.0.0.1:8420`. Arrêt par Ctrl-C : la relecture en
  cours se termine d'abord.

Pour le laisser tourner : un multiplexeur de terminal, ou `--once` depuis `cron`/`launchd`.
`reviewme-poll` marche toujours : c'est la même boucle, réglée par le `.env` seul
(`REVIEW_LABEL`, `REVIEW_ALL_PRS`, `POLL_INTERVAL`).
