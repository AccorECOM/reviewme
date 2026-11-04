[← ReviewMe](../../README.fr.md) · [English](../history-and-triage.md) · **Français**

# Historique, ères et tri

## Historique

La page **historique** demande un passage ponctuel : `reviewme history --since 2024-01-01`
lit les PR mergées et fermées du dépôt depuis cette date (une requête GraphQL pour 100 PR :
une quarantaine de requêtes pour 4 000 PR) dans `history.jsonl`, sans titre, description
ni texte de commentaire. Par trimestre, avec un repère au démarrage de ReviewMe : PR
mergées par mois, heures médianes avant le merge et avant la première relecture humaine,
relectures et fils humains par PR (et pour 100 lignes modifiées), part de branches hotfix
ou revert et, à part, de branches de correction ordinaires `fix/` (`fix/…`, `bugfix/…`,
`fix-…` ; un signal faible : il lit les noms de branche), et, avec `--jira`, les tickets
de bug créés par mois. Avant/après n'est pas une preuve (l'équipe et le produit changent
aussi) : la page le dit et renvoie au groupe témoin. Relançable à tout moment : la ligne
la plus récente de chaque PR l'emporte.

## Ères

Quand l'histoire du bot a plus d'un début (un prototype, une pause, puis
ReviewMe), nommez les périodes avec `[[stats.eras]]` :

```toml
[[stats.eras]]
name = "prototype"
since = "2026-03-10"
label = "ai-review-poc"
[[stats.eras]]
name = "pause"
since = "2026-10-09"
[[stats.eras]]
name = "ReviewMe"
since = "2026-11-03"
label = "review-me"
```

Chaque ère a un `name`, une date `since` (`AAAA-MM-JJ`, obligatoire) et un `label`
facultatif : le label GitHub qui marquait les PR relues par le bot de cette ère. Les ères
se suivent et sont triées par `since` (deux ères ne peuvent pas commencer le même jour) :
une PR appartient à l'ère dont le `since` est le plus récent à sa date de création, et à
« avant » quand elle précède la première. Dans une ère avec `label`, une PR compte comme
relue quand le registre a un passage live pour elle ou qu'elle porte ce label. Quand le
bot de cette ère publiait sous le compte d'une personne (un prototype lancé avec le jeton
du mainteneur), ajoutez `bot_login = "maria"` à l'ère (il lui faut un `label`) : sur les PR
labellisées de l'ère, les relectures et fils de ce login ne comptent plus comme humains.
Cela lit les comptes par login que `reviewme history` enregistre depuis la 1.4.0 ; sur des
lignes plus anciennes, la page demande de relancer la lecture. Les
graphiques de l'historique ont alors une ligne pointillée par ère, légendée de son nom, et
le tableau avant/après devient un tableau par ère (PR ouvertes et mergées, heures
médianes avant le merge et avant la première relecture humaine, relectures et fils
humains par PR, parts de revert, hotfix et `fix/`), avec deux lignes pour une ère à label
(relues / non relues). La page ROI compare « avant » à la dernière ère. Sans ères,
`bot_since` (ou le premier passage live) vaut une ère nommée « ReviewMe ».

`--jira` utilise les variables Jira du relecteur `us` (`JIRA_BASE_URL`, `JIRA_EMAIL`,
`JIRA_API_TOKEN`, `JIRA_KEY_PREFIX` facultatif) : la clé du ticket est lue dans le nom de
branche, son type distingue les bugs (`Bug`, `Bogue`, `Defect`, plus les noms de
`JIRA_BUG_TYPES`, défaut `Bug`, qui est aussi la liste comptée par mois).

## Tri : pourquoi une remarque est contestée

**Pourquoi** une remarque est contestée, c'est ce que répond `reviewme triage --since 14d`.
Les étiquettes par mots-clés d'`enrich` sont un tri grossier ; ce passage facultatif fait
lire à un modèle les réponses que le registre contient déjà (il faut donc `[ledger]
include_text = true`) et donne à chaque remarque ayant une réponse ou un 👎 une cause :
faux positif, contexte manquant (vrai, mais l'équipe le fait exprès), préexistant,
doublon, pas important, corrigé, question, autre. Il regroupe aussi les commentaires
humains là où le bot n'a rien dit en 8 motifs récurrents au plus, chacun avec une règle
suggérée à écrire. La page qualité trace alors les causes (aussi par relecteur) et liste
les manqués récurrents ; la page réglages donne la cause principale de chaque règle
contestée. Les textes sont des données de tiers : l'appel n'a aucun outil, le prompt le
dit, et le résultat ne va que dans `triage.jsonl`, jamais sur GitHub ni dans la config.
Les titres et règles suggérées sont du texte de modèle sur vos PR : la page ne les
affiche qu'avec `include_text`, l'archive les retire sinon. Un passage sur ~60 remarques
contestées et 150 commentaires, c'est un ou deux appels, environ 0,10 à 0,25 $ avec
Sonnet ; `--dry-run` affiche la taille sans rien appeler. À lancer chaque semaine après
`enrich`.

Chaque passage note aussi son **déclencheur** (`REVIEWME_TRIGGER` : `pr_build`, `manual`,
`schedule`, `watch`, ou tout autre nom ; `unknown` s'il n'est pas renseigné), affiché par
déclencheur dans la page usage. `reviewme watch` met `watch` sauf si le déploiement a mis
le sien.
