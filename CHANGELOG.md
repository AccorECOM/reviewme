# Changelog

## Unreleased

- A reviewer with nothing to report no longer posts its summary when another reviewer
  speaks (e.g. a `global` `advisor` tip): only the reviewers with inline comments,
  out-of-diff, in-summary or advice items contribute to the summary comment.

## 1.2.0

- Core review method rewritten in three steps (understand the change, broad hunt, verify):
  explicit questions on intent, edges, silent failures, access and dead code; every finding
  carries its fix; severity from the consequence in the project; an edge case counts only
  if a caller can reach it; a flawed approach is said once in the summary.
- Core `tech` reviewer rewritten from a field-tested Tech Lead review agent: priorities
  (correctness, security, reliability, data and performance, design, tests, conventions),
  severity and confidence scales, where to find the project's rules. `AGENTS.md` is no
  longer listed in its `context.read` (read automatically).
- `silent_when_clean` now follows the reviewer's own signal, an empty `summary`: `us`
  (which writes everything in `summary`) could never post before. `us` and `advisor` leave
  `summary` empty when there is nothing to report.

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
