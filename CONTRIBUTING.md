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
uv run pytest -q          # a few seconds, no network, no secret
uv run ruff check .       # lint
uv run reviewme review --repo owner/repo --pr 1 --dry-run   # prints what would be posted
```

`REVIEWME_FAKE=1` skips the model and fabricates findings anchored on the diff, which is
enough to exercise the whole pipeline.

To judge the dashboard before a team has data, `reviewme demo-data --out DIR [--seed N]
[--days 90] [--language fr] [--now YYYY-MM-DD]` writes a realistic synthetic ledger
(about 70 reviewed and 40 unreviewed PRs over 90 days, two years of history across three
eras, fictional `*-demo` logins) into `DIR/ledger` and builds
`DIR/dashboard-demo-<language>.html` from it, through a real `project.toml`. The same seed
and date give the same files. Never point it at a real ledger folder: it replaces
`DIR/ledger`.

## What must not change without a discussion

These are the engine's security invariants (see `SECURITY.md`):

- the tool allowlist given to the model stays read-only: no `Write`, no `Task`,
  never `--dangerously-skip-permissions`;
- the bot only posts `COMMENT` reviews, never `APPROVE` or `REQUEST_CHANGES`;
- a PR cannot change its own review: the project config is read from the base branch;
- everything posted to GitHub goes through `reviewme/scrub.py`.

## Conventions

- Commit messages: `add/update/fix/delete(scope) - Short description`.
- Code, prompts, docs and tests are in English; `README.fr.md` mirrors `README.md`, and
  `docs/fr/` mirrors the pages of `docs/` (same file names).
- Docs: `README.md` stays a short entry point; details go to a page of `docs/`, one topic
  per page, kept short. A new setting is documented on its page and in `docs/reference.md`.
- Python 3.13, managed with `uv`; `pyproject.toml` is the single source of settings.
