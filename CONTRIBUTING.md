# Contributing

Thanks for helping. The most useful contributions, in order:

1. **A core reviewer or a precheck** in `config/templates/reviewers/`: a persona
   (`system.md`), its options (`reviewer.toml`) and, when a script can establish a
   fact before the model runs, a `precheck.py`. The `skills/reviewme-create-reviewer/`
   skill walks Claude Code through it.
2. **A guard against false positives**: a rule that tells a reviewer what *not* to
   flag is worth as much as a new rule.
3. **A bug with a failing test** in `tests/`.

## Setup

```sh
uv sync --group dev
npm install -g @anthropic-ai/claude-code   # the engine drives the Claude Code CLI
cp .env.example .env                       # GITHUB_TOKEN, GITHUB_REPO, REPO_PATH
```

## Check before the PR

```sh
uv run pytest -q          # 179 tests, under 2 s, no network, no secret
uv run ruff check .       # lint
uv run reviewme review --repo owner/repo --pr 1 --dry-run   # prints what would be posted
```

`REVIEWME_FAKE=1` skips the model and fabricates findings anchored on the diff, which is
enough to exercise the whole pipeline.

## What must not change without a discussion

These are the engine's security invariants (see `SECURITY.md`):

- the tool allowlist given to the model stays read-only: no `Write`, no `Task`,
  never `--dangerously-skip-permissions`;
- the bot only posts `COMMENT` reviews, never `APPROVE` or `REQUEST_CHANGES`;
- a PR cannot change its own review: the project config is read from the base branch;
- everything posted to GitHub goes through `reviewme/scrub.py`.

## Conventions

- Commit messages: `add/update/fix/delete(scope) - Short description`.
- Code, prompts, docs and tests are in English; `README.fr.md` mirrors `README.md`.
- Python 3.13, managed with `uv`; `pyproject.toml` is the single source of settings.
