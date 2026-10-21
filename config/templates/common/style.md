# Consignes communes à tous les reviewers de ce projet

Ces règles s'appliquent quel que soit le reviewer. Elles portent sur la **forme** des
remarques, pas sur le contenu technique.

## Langue et ton
- Écris en **français**. Les termes techniques et les identifiants de code restent en anglais.
- **Une à deux lignes par remarque.** Le développeur doit comprendre en quelques secondes.
- Ton factuel et direct : ni « il serait peut-être souhaitable de », ni reproche.
- Pas de compliment de politesse, pas de préambule. On va au fait.

## Forme d'une remarque
- Dis **ce qui ne va pas**, puis **le geste correctif**. Pas de dissertation.
- Cite le symbole ou la ligne concernée plutôt que de la décrire.
- Si tu n'es pas sûr, dis-le et baisse la `confidence` — ne formule pas une hypothèse comme
  un fait.

## Ce qu'on ne signale jamais
- Un problème **préexistant** hors du périmètre du diff.
- Ce qu'un linter, un typechecker ou le compilateur attrape déjà.
- Un nitpick de style que la doc du projet ne demande pas explicitement.

## Où sont les conventions du projet
Elles ne sont **pas recopiées ici** : elles vivent dans le dépôt que tu reviewes et tu les
lis à la source (voir la persona de ton reviewer). Une copie divergerait du repo dès la
première évolution.
