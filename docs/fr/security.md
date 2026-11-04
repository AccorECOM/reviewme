[← ReviewMe](../../README.fr.md) · [English](../security.md) · **Français**

# Sécurité

Le diff, le ticket et les commentaires sont écrits par des tiers : ce sont des
**données**, jamais des instructions.

- **Outils en lecture seule**, liste verrouillée dans le code : ni `Write`, ni sous-agents,
  ni `--dangerously-skip-permissions`. Ni les hooks ni les serveurs MCP du dépôt relu ne
  sont chargés. Un relecteur qui déclare ses propres serveurs MCP (`[mcp] config`) ou
  plugins (`[plugins] install`) a aussi droit à leurs outils (`mcp__<serveur>`, `Skill`) ;
  la passe de vérification, non. Un skill n'a pas plus de droits que la liste.
- **Une PR ne change pas sa propre relecture** : si la config vit dans le dépôt relu
  (`.reviewme/`), elle est lue sur la branche de base, tout comme `CLAUDE.md` et
  `.claude/rules/`. Pour tester un nouveau relecteur dans sa PR, on lance un build à la
  main avec `REVIEWME_CONFIG_SOURCE=pr`. Le détail, et quand une PR est sautée : [D'où vient la config](how-it-works.md#doù-vient-la-config-branche-base).
- Secrets et chemins absolus masqués avant tout post ; webhook refusé sans signature HMAC.
- Un `precheck` est du code fourni par le projet : il se relit comme du code.
- **Sur une machine personnelle** (mode watch), l'agent a les accès fichiers de votre
  utilisateur : les outils sont en lecture seule, mais un diff hostile pourrait tenter de
  lui faire lire des fichiers hors du dépôt. C'est pourquoi les PR de forks sont ignorées
  par défaut ; pour un dépôt ouvert aux contributions extérieures, préférez `--label` ou
  un utilisateur, un conteneur ou une VM dédiés.
