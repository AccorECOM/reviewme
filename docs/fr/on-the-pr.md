[← ReviewMe](../../README.fr.md) · [English](../on-the-pr.md) · **Français**

# Ce qui arrive sur la PR

| Remarque | Où | Pourquoi |
|---|---|---|
| `BLOCKER`, `IMPORTANT` sur du code introduit par la PR | en ligne | c'est ce qui mérite l'attention du dev |
| `MINOR` | résumé | utile, mais pas au prix d'une notification par ligne |
| sur du code seulement **déplacé** par la PR | résumé, tagué | ce n'est pas un défaut de la PR |
| hors des lignes du diff | résumé | GitHub refuse un commentaire hors diff |
| non confirmée par la vérification | nulle part (registre) | un faux positif coûte la confiance de l'équipe |
| **💡 Conseil pour la relecture** (relecteur `advisor`) : un point qu'un humain doit valider lui-même (ordre d'appels modifié, fichier rangé hors de son dossier habituel…) | un commentaire | seulement s'il est applicable, ancré dans des fichiers de la PR ; 3 au plus |
| PR propre | rien, ou une courte note avec `[review] clean_note = true` | le silence vaut mieux qu'un « RAS » ; la note dit seulement que la revue a tourné |

La langue des remarques postées et des titres du bot se règle par projet dans
`project.toml` : `[review] language = "fr"` (défaut `en`).

## Modèle et score sur les commentaires

Deux interrupteurs du `project.toml`, désactivés par défaut, ajoutent une petite ligne
sous chaque remarque. Ils servent à évaluer le bot (quel modèle, quels réglages, quelle
assurance), pas à la lecture quotidienne du développeur.

```toml
[review]
show_model = true   # sur quel modèle le relecteur a tourné
show_score = true   # le score de chaque remarque
```

`show_model` affiche **deux noms**, parce qu'ils peuvent différer :

- **configuré** : le nom demandé par votre installation, le `model` du relecteur, sinon
  `CLAUDE_MODEL`, sinon `ANTHROPIC_MODEL` (« défaut du CLI » si aucun n'est défini).
  Derrière une passerelle maison, ce peut être un alias comme `SuperLLM` ;
- **a répondu** : le modèle que déclare la réponse de l'API, lu dans l'enveloppe du CLI
  (`modelUsage` ; si le CLI en a utilisé plusieurs, celui qui a coûté le plus). C'est là
  qu'on voit que `SuperLLM` est, par exemple, `claude-opus-5-5`.

```
score 85/100 · vérification 92/100 · modèle : `SuperLLM` (configuré) → `claude-opus-5-5` (a répondu)
```

La ligne est sur chaque commentaire inline, et sous le titre de la section de chaque
relecteur dans le résumé. Quand les deux noms sont identiques, il n'apparaît qu'une fois.
« A répondu » est ce que l'API déclare : une passerelle qui réécrit le champ `model` de
ses réponses peut masquer le vrai, et le moteur n'a pas d'autre moyen de le savoir.

`show_score` ajoute le **score** de chaque remarque, en inline et dans les listes du
résumé :

- **score N/100** est la `confidence` que le relecteur a lui-même donnée à la remarque,
  selon l'échelle de sa persona (`tech` : 91-100 preuve vue et échec certain, 76-90
  prouvé mais l'impact dépend des appelants, 51-75 probable avec une part non vérifiée,
  50 et moins incertain). Les remarques sous `CONFIDENCE_THRESHOLD` (défaut 80) ne sont
  jamais postées : un score affiché est donc toujours au moins égal au seuil ;
- **vérification N/100**, quand elle apparaît, est la confiance de la passe de
  vérification qui a relu la remarque et l'a confirmée en citant une ligne. Elle est
  absente si la vérification est désactivée ou n'a pas abouti.

Le score n'est ni une sévérité (c'est l'étiquette `[BLOCKER]`/`[IMPORTANT]`/`[MINOR]`)
ni une mesure de qualité : c'est l'assurance que le modèle s'attribue, et
l'auto-évaluation d'un modèle est mal calibrée. Il sert à être comparé à ce que l'équipe
a fait de la remarque : les deux nombres sont la `confidence` et la
`verification.confidence` du registre, donc la calibration par tranche de confiance du
tableau de bord dit si les 90 ont vraiment plus souvent raison que les 80, et où placer
le seuil.

Le mode simple (sans projet) lit `REVIEWME_SHOW_MODEL` et `REVIEWME_SHOW_SCORE`
(`1`/`0`) ; le `project.toml` d'un projet l'emporte sur eux.

## Note sur une PR propre

Par défaut, une PR propre ne reçoit aucun commentaire, ce qu'on ne distingue pas d'un bot
qui n'a pas tourné. `[review] clean_note = true` (mode simple : `REVIEWME_CLEAN_NOTE=1`)
poste à la place un court commentaire, quand chaque relecteur sélectionné est allé au bout
avec une réponse lisible et qu'aucun n'a rien à poster :

```
✅ Revue terminée sur `abcdef12` : aucune remarque. Relecteurs : `architect`, `tech`.
```

C'est un seul commentaire, mis à jour de commit en commit (il prend la place du
commentaire de synthèse, et la synthèse la reprend sur un commit avec remarques). Il ne
compte pas comme une remarque postée. Un relecteur en échec ou hors contrat, et pas de
note : le silence n'est pas mérité.

Le bot ne poste que des commentaires : jamais d'approbation ni de demande de changement.
