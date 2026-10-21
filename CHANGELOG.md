# Changelog

## 1.0.0

First public release.

- Line-by-line review of GitHub pull requests by one or several specialised reviewers
  (`tech`, `us`, `i18n`, `advisor`), configured per project in `project.toml`.
- Deterministic prechecks before the model runs; verification pass on the findings.
- Ledger of findings and runs, `enrich`, `export`, `stats` and `feedback` commands.
- Entry points: one-shot CLI, poll loop, HMAC webhook, read-only dashboard.
- Output scrub, read-only tool allowlist, config read from the base branch.
