# Changelog

## 1.1.0

- `reviewme watch`: local loop that polls a repository's open PRs and reviews each PR
  (again after every new push), wherever the Claude Code CLI runs (as configured on that machine). Each PR is
  reviewed in its own `git worktree`, so the clone is never touched; config is still read
  from the base branch. Every open PR by default, minus drafts, forks and the `no-ai-review`
  label; `--label` to opt in instead.
- A usage limit (plan quota, 429, overloaded) no longer marks the commit as failed:
  it is retried, and watch waits for the reset.
- Local state keyed by `owner/repo#N` (legacy `N` keys still read).
- `reviewme-poll` is now the same loop as `reviewme watch`, configured from `.env`.
- `reviewme doctor`: diagnoses an authentication failure of the CLI (which variables are set,
  credential fingerprints, one probe per credential), without printing any secret. No
  comment is posted when the CLI fails.

## 1.0.0

First public release.

- Line-by-line review of GitHub pull requests by one or several specialised reviewers
  (`tech`, `us`, `i18n`, `advisor`), configured per project in `project.toml`.
- Deterministic prechecks before the model runs; verification pass on the findings.
- Ledger of findings and runs, `enrich`, `export`, `stats` and `feedback` commands.
- Entry points: one-shot CLI, poll loop, HMAC webhook, read-only dashboard.
- Output scrub, read-only tool allowlist, config read from the base branch.
