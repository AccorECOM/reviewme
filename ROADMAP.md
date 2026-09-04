# Reste à faire

Ce qui manque, dans l'ordre de ce que ça apporterait. Rien ici n'est bloquant : le moteur
tourne en production, ces points sont des limites connues et assumées.

## Ce qui demande une décision avant du code

**Un plafond sur la taille du diff analysé.** Aujourd'hui rien ne borne une PR de refonte :
`MAX_BUDGET_USD` coupe par reviewer, mais après coup. La question n'est pas technique — que
faire d'une PR trop grosse ? La sauter, tronquer le diff, ou poster « trop volumineuse pour
une relecture utile » ? Chaque réponse a ses partisans.

**`REQUEST_CHANGES` sur les `BLOCKER`.** Le bot ne pose que des `COMMENT`. Bloquer un merge
sur un finding automatique suppose une identité dédiée (GitHub App) et un taux de faux
positifs mesuré — sinon c'est de la friction pure. Voir aussi : le bot ne peut pas approuver
sa propre PR, et une approbation qui compterait dans les règles de branche protégée ferait
de lui un tampon automatique.

## Ce qui n'a pas encore de données

**Le seuil de confiance.** La valeur par défaut écarte une bonne part des remarques. Le
rapport affiche la part de celles qui reçoivent une réponse d'un développeur : c'est ce
chiffre qui doit décider du réglage, pas une intuition.

**Le cycle retour → règle.** `reviewme feedback` propose des règles attribuées à partir des
réponses des développeurs. Tant que `common/regles-terrain.md` est vide, ce n'est qu'une
intention.

## Limites techniques connues

**Pas d'arrêt net au plafond de coût d'une PR.** Les reviewers tournent en parallèle : au
moment où le dépassement est constaté, ils ont déjà tourné. Un arrêt réel supposerait une
exécution séquentielle par priorité.

**Le diff est envoyé en entier à chaque commit**, pas l'incrémental depuis la dernière
review. C'est un choix : un diff partiel ferait juger des lignes sorties de leur contexte.

**Pas de resolve/unresolve des threads.** Le bot répond « ce point n'apparaît plus » quand
un finding disparaît, mais ne ferme pas le fil — cela demanderait l'API GraphQL et le scope
`Contents: R/W`, donc d'élargir les droits du token.

**Pas de sous-agents, volontairement.** Vérifié : un sous-agent n'hérite pas de l'allowlist
de son parent. Autoriser `Task` rouvrirait l'exécution de code arbitraire, puisque la
configuration d'un reviewer peut venir du dépôt reviewé. Pour faire varier le modèle selon
la tâche, créer un reviewer de plus avec son `model`.

**Le sandbox de déploiement reste à câbler à l'infra** (pas de réseau sortant hors GitHub,
système de fichiers confiné au clone). C'est la mitigation de fond contre la prompt
injection, et elle ne peut pas vivre dans ce dépôt.

## Coût

Le contexte stable — personas, consignes, conventions lues dans le dépôt — est refacturé à
chaque review. Le **prompt caching** est le seul levier qui l'attaque vraiment, et il est
neutralisé dès qu'une passerelle rejette les en-têtes de caching. À vérifier auprès de qui
opère la passerelle avant d'optimiser ailleurs.
