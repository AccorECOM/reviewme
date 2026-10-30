# Changelog

## 1.4.0

- Statistics dashboard built from the ledger (`reviewme/dashboard/`): one self-contained
  HTML page, English or French, with the sections `summary` (opens with a generated
  debrief), `quality`, `usage`, `cost`, `reliability`, `tuning`, `history`, `roi` and
  `hall_of_fame`, and a one-line reading under every chart. Written by `reviewme stats
  --html`, `reviewme export` and served by `reviewme-web` on `/dashboard`. It never shows
  a PR title, a finding or a reply.
- `[stats]` in `project.toml`: sections, names (`login` or `masked`), window, period
  (week or sprint), reaction meanings (`[stats.judgment]`), ROI assumptions
  (`[stats.roi]`), named periods (`[[stats.eras]]`, with an optional earlier bot's
  `label` and `bot_login`), `control_group`, `debrief`, `bot_since`.
- `reviewme enrich` keeps every reaction and the first human signal per finding, PR
  cycle fields in `prs.jsonl`, and unreviewed PRs as a control group.
- `reviewme history --since DATE [--jira]`: read-only scan of merged PRs into
  `history.jsonl` (cycle times, human reviews, hotfix and fix branches, Jira bug
  tickets), to compare before and after the bot.
- `reviewme triage --since 14d`: optional tool-less LLM pass classifying contested
  findings and recurring misses into `triage.jsonl` (needs `[ledger] include_text`).
- `trigger` recorded on every ledger row (`REVIEWME_TRIGGER`).
- `reviewme demo-data`: a realistic synthetic ledger to judge the dashboard.
- Fixes: `stats.json` `duree_s` was always 0; replies to open findings were the maximum
  over runs instead of the sum over PRs.

## 1.3.0

- Factual review output. The output contract asks for the defect and its trigger in
  `message` (one sentence) and the fix in `suggestion`, rendered on its own line. The
  `summary` no longer lists or rephrases findings, describes the PR, or says what was
  checked or not reported: it is empty when the findings say it all, otherwise one
  sentence on what no finding carries. `advice.why` is two sentences at most. The core
  `tech` and `i18n` personas and `common/style.md` follow (no per-language recap).
- An inline reviewer with an empty summary no longer adds a bare title to the review
  body, and the review body is signed once instead of once per reviewer.
- Instance personas that ask for a recap in `summary` keep it: align them with the
  contract (e.g. "summary" rules in `common/`).

## 1.2.2

- A reviewer's `[mcp] config` and `[plugins]` now take effect. Their tools were missing
  from `--allowedTools`, so `claude -p` refused them: the MCP servers loaded but every call
  was denied, and the plugins were installed but the `Skill` tool was denied. The reviewer
  that declares them is now allowed `mcp__<server>` for each server under `mcpServers` in
  its own MCP file, and `Skill` when `[plugins] install` is set. The verification pass keeps
  the base list. A skill is still bound by the allowlist: no `Write`, no `Task`, no extra
  Bash. An unreadable MCP file, or one without `mcpServers`, adds nothing.

## 1.2.1

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
