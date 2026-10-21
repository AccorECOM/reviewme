# Examples

## Adapt the core `tech` to an API

```toml
# .reviewme/projects/shop/project.toml
[reviewers]
core = ["tech", "advisor"]

[reviewers.tech]
read = ["docs/architecture.md", "docs/conventions.md"]
paths = ["src/**"]
instructions = "The legacy/ folder is frozen: only report blocking bugs there."
```

`instructions` does not repeat the docs (they are in `read`): it states what is written
nowhere.

## Custom reviewer: migrations

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
# Role — migrations reviewer

You check the database migrations of this PR.

## What you look for
- a schema migration whose `downgrade()` is missing, empty, or does not undo `upgrade()`;
- an operation that locks a large table (see `docs/migrations.md` for which ones).

## What is intended in this project
- data migrations (`migrations/data/`) have no downgrade.
```

## Custom reviewer: accessibility of a front end

```toml
# reviewers/accessibility/reviewer.toml
output_mode = "inline"
priority = 30

[when]
paths = ["src/**/*.tsx"]
```

The persona lists what it looks for (accessible names, valid ARIA, keyboard focus) and what
is intended here ("the UI kit components handle their own ARIA"). The rules in
`CLAUDE.md` and `.claude/rules/` are already read automatically: do not copy them.
