# Exemples

## Adapter `tech` du core à une API

```toml
# .reviewme/projects/boutique/project.toml
[reviewers]
core = ["tech", "conseil"]

[reviewers.tech]
read = ["docs/architecture.md", "docs/conventions.md"]
paths = ["src/**"]
instructions = "Le dossier legacy/ est gelé : n'y signale que les bugs bloquants."
```

`instructions` ne répète pas la doc (elle est dans `read`) : elle dit ce qui n'est écrit
nulle part.

## Relecteur custom : migrations

```toml
# reviewers/migrations/reviewer.toml
output_mode = "inline"
priority = 20

[when]
paths = ["migrations/**"]

[context]
read = ["docs/migrations.md"]
```

```markdown
<!-- reviewers/migrations/system.md -->
# Rôle — relecteur des migrations

Tu vérifies les migrations de base de données de cette PR.

## Ce que tu cherches
- une migration de schéma dont `downgrade()` manque, est vide, ou ne défait pas `upgrade()` ;
- une opération qui verrouille une grande table (voir `docs/migrations.md` pour lesquelles).

## Ce qui est voulu dans ce projet
- les migrations de données (`migrations/data/`) n'ont pas de downgrade.
```

## Relecteur custom : accessibilité d'un front

```toml
# reviewers/accessibilite/reviewer.toml
output_mode = "inline"
priority = 30

[when]
paths = ["src/**/*.tsx"]
```

La persona liste ce qu'il cherche (noms accessibles, ARIA valide, focus clavier) et ce qui
est voulu ici (« les composants du kit gèrent eux-mêmes leur ARIA »). Les règles de
`CLAUDE.md` et `.claude/rules/` sont déjà lues d'office : ne pas les recopier.
