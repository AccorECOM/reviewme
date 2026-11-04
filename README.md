# ReviewMe

**English** · [Français](README.fr.md)

**An AI code review bot for GitHub Pull Requests.** It reads the diff and the repository,
then posts **line-by-line** comments, like a human reviewer. Several specialised
reviewers can run on the same PR: technical, security, translations, ticket criteria…

```text
src/orders/service.py, line 42
[IMPORTANT] `charge()` is called without `await`: the payment runs in the background and
any error is lost. Add `await` and let the exception propagate.
```

## Install

```sh
uv sync && npm install -g @anthropic-ai/claude-code
cp .env.example .env            # GITHUB_TOKEN, GITHUB_REPO, REPO_PATH
uv run reviewme review --repo my-org/my-app --pr 123 --dry-run
```

Python 3.13, [uv](https://docs.astral.sh/uv/) and the
[Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI. `--dry-run` prints what
would be posted without writing to GitHub. Next step: [Getting started](docs/getting-started.md).

## How it works, in short

- **The engine (this repository)** knows *how* to review: the method, the output format,
  the verification pass, security, GitHub. **A reviewer (a config folder)** knows *what*
  to look for in *your* project: a short persona, its rules, when it runs.
- Each reviewer is an independent, **read-only** `claude -p`. A second call then
  **verifies** every finding and must cite the line that proves it: anything unproven is
  not posted.
- `BLOCKER` and `IMPORTANT` go **inline**, the rest in one summary comment. No duplicate
  from one commit to the next. The bot never approves nor blocks a PR.
- **A PR cannot change its own review**: an in-repo config is read from the base branch,
  and only from trusted ones.
- Every finding, posted or not, goes to a **ledger**; a dashboard tells whether the bot
  is worth it.

## Documentation

| Page | What is in it |
|---|---|
| [Getting started](docs/getting-started.md) | first dry run, first project, CI examples |
| [Reviewers](docs/reviewers.md) | core reviewers (`tech`, `us`, `i18n`, `advisor`), adapting them, writing your own |
| [How a review runs](docs/how-it-works.md) | the life of a PR, where the config is read from, **when a PR is not reviewed** |
| [What lands on the PR](docs/on-the-pr.md) | inline vs summary, language, model and score, clean PR note |
| [Watch mode](docs/watch-mode.md) | run it on your machine, no CI needed |
| [Measuring whether it helps](docs/measuring.md) | ledger, `reviewme stats`, the dashboard, `[stats]`, ROI |
| [History, eras and triage](docs/history-and-triage.md) | before/after scan, named eras, why findings get contested |
| [Security](docs/security.md) | what the engine guarantees, what you must still do ([threat model](SECURITY.md)) |
| [Reference](docs/reference.md) | every command, variable and `project.toml` key |

Also: [known limitations](docs/LIMITATIONS.md), [ledger schema](docs/ledger-schema.md),
every `reviewer.toml` option in [`docs/reviewme.html`](docs/reviewme.html), every
variable commented in [`.env.example`](.env.example), [changelog](CHANGELOG.md).

## Contributing and license

Tests, conventions and invariants: [`CONTRIBUTING.md`](CONTRIBUTING.md). Report a
vulnerability: [`SECURITY.md`](SECURITY.md). Apache 2.0, see [`LICENSE`](LICENSE).
