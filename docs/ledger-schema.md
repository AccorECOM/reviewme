# ReviewMe ledger: file schema

This document accompanies the archive produced by `reviewme export` (it is copied into it
as `SCHEMA.md`). All `.jsonl` files are append-only: one line = one JSON object. When the
same object appears several times (same `comment_id`, same `pr`), **the most recent line
wins**. `schema_version` is `1`.

On disk, each file is split into parts, one per run or per enrichment pass
(`findings/<timestamp>-<run_id>.jsonl`…), written in one block: two concurrent runs cannot
interleave their lines. The archive merges them into one file per type.

In the archive, GitHub logins (`user`, `author`, `resolved_by`) are replaced by a stable
alias (`u-1a2b3c4d`): you can tell that two reactions come from the same person, and
whether it is the PR author (`role`), but not who contests what.

The text of the remarks, replies and human comments is only present if the project has
enabled `[ledger] include_text = true` (in `project.toml`) or
`REVIEWME_LEDGER_INCLUDE_TEXT=1`. Without it, the archive contains no PR content.

The dashboard (`reviewme stats --html`, `dashboard.html` in the archive) is computed from
these same files, plus `history.jsonl` and `history-bugs.jsonl` when `reviewme history`
wrote them; it reads none of the text fields, except the recurring-miss texts of
`triage.jsonl` when `include_text` is on (see below).

## `findings.jsonl` — one line per finding and per run, posted or not

| Field | Meaning |
|---|---|
| `run_id` | Identifier of the engine's pass on the PR (shared by all its reviewers) |
| `ts` | UTC timestamp of the run |
| `repo`, `pr`, `head_sha`, `base_sha` | PR and commits reviewed |
| `variant` | `live` (evaluated pipeline) or `baseline` (control, always in `dry_run`) |
| `dry_run` | `true`: nothing was posted; `fate = posted` means "would have been posted" |
| `status` | `ok`, or `post_failed` if the GitHub write failed |
| `trigger` | What started the run: `REVIEWME_TRIGGER` of the deployment, a free string. Documented values: `pr_build` (CI on a PR event), `manual`, `schedule`, `watch` (set by `reviewme watch` unless the deployment set its own). `unknown` when not set; absent on rows written before 1.4.0 (read as `unknown`) |
| `reviewer` | Reviewer (`tech`, `architect`…) |
| `model` | Model that **actually** answered (read from the CLI envelope) |
| `model_configured` | Model name the config asked for: the reviewer's `model`, else `CLAUDE_MODEL`, else `ANTHROPIC_MODEL`; may be a gateway alias. Empty = the CLI default. Absent before 1.5.0 |
| `engine_commit` | Engine commit |
| `config_hash` | Fingerprint of the settings; details in `config-snapshot/<hash>/` |
| `fingerprint` | Fingerprint of the line (`sha1(path + normalized content)[:10]`), identical to the posted marker. Empty if the line is not in the diff |
| `path`, `line` | Location, new-file side, after re-anchoring on the diff |
| `severity_raw` | Severity written by the model, before normalization |
| `severity` | `BLOCKER`, `IMPORTANT` or `MINOR` |
| `confidence` | Confidence declared by the model (0-100): the "score" shown on the PR with `[review] show_score` |
| `rule_id` | Rule identifier given by the model (may be empty) |
| `has_evidence` | The model cited a proof (`evidence`) |
| `verification` | `{status, confidence}`: `confirmed`, `refuted` or `skipped` (no verification pass) |
| `fate` | `posted`, `dropped_low` (below the threshold), `dropped_unverified` (refuted by verification), `out_of_diff` (delivered in the summary), `capped` (comment cap), `deduped` (already commented), `invalid` (no usable path or line) |
| `comment_id` | GitHub comment carrying the remark (posted now, or existing if `deduped`) |
| `in_summary` | Delivered in a global comment rather than inline |
| `message`, `suggestion` | Text, only with `include_text` |

## `runs.jsonl` — one line per reviewer and per run

`run_id`, `ts`, `repo`, `pr`, `head_sha`, `base_sha`, `variant`, `dry_run`, `status`,
`trigger`, `reviewer`, `model`, `model_configured`, `engine_commit`, `config_hash`, plus:

| Field | Meaning |
|---|---|
| `model_reported` | The model that did most of the work (highest cost in the envelope's `modelUsage`), the "answered" name shown with `[review] show_model`. Absent before 1.5.0 |
| `parsed_ok` | The answer complied with the output contract |
| `num_turns`, `max_turns` | Turns used and cap |
| `subtype`, `is_error` | CLI ending: `success`, `error_max_turns`… |
| `duration_s`, `cost_usd` | Duration and cost of this reviewer |
| `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_create_tokens` | Tokens (input includes the cache) |
| `diff_files`, `diff_lines` | Size of the reviewed diff |
| `context_files` | `[{path, bytes, truncated, source}]`: what the reviewer actually received (`context.read`, `precheck`, `historique` (history), `context`) |
| `verification` | Summary of the verification pass, or `null` |
| `threshold` | Confidence threshold applied |
| `findings`, `fates` | Number of findings and breakdown by fate |
| `advice`, `advice_topics` | Review advice displayed (anchored in the PR); their topics only with `include_text` |

## `enrich.jsonl` — what happened to a posted remark (`reviewme enrich`)

| Field | Meaning |
|---|---|
| `comment_id`, `pr`, `reviewer`, `fingerprint`, `rule_id` | Remark tracked |
| `head_sha` | PR head at enrichment time |
| `fingerprint_gone_at` | First head commit observed where the commented line no longer exists in the file, while the file still exists: **acted upon** |
| `file_gone_at` | The file no longer exists at this commit (deleted or renamed): proves nothing, not counted as acted upon |
| `reactions` | `[{content, user, role: "pr_author"\|"other", ts}]`. `content` is any GitHub reaction: `+1`, `-1`, `laugh`, `confused`, `heart`, `hooray`, `rocket`, `eyes` (lines written before 1.4.0 only hold `+1` / `-1`, without `ts`). What each one means is the dashboard's `[stats.judgment]`, not the collector's. Reactions from other bots and from `BOT_LOGIN` are ignored |
| `first_signal_at` | Earliest human signal on the remark: first reaction or reply (GitHub `created_at`). Kept across passes, so a reaction removed later still counts. The dashboard derives the judgment delay from it (signal minus the `ts` of the run that posted the remark) |
| `replies` | `[{user, role, ts, label, contests, remember?, text?}]`. `label` (keyword-based, to be reviewed): `fixed`, `false_positive`, `intended`, `unimportant`, `question`, `other`. `contests` = `false_positive` or `intended`. `remember`: `{trusted, rule?}` for a `@reviewme remember …` reply, `trusted` if the author has write access, `rule` only with `include_text`. Reactions from other bots and from `BOT_LOGIN` are ignored |
| `thread_resolved`, `resolved_by` | Thread resolved on GitHub, and by whom |
| `human_comments_nearby` | Human comments 10 lines away or less, same file |

## `prs.jsonl` — state of a PR (`reviewme enrich`)

One line per PR the bot reviewed (`reviewed = true`, a live run outside dry-run, with or
without findings) and, with the control group on (`[stats] control_group`, default
`true`; `REVIEWME_CONTROL_GROUP` overrides it), one line per PR updated in the window that
has **no run at all** in the ledger (`reviewed = false`). PRs seen only in dry-run or by
the baseline are in neither group; PRs opened by a bot account (dependency updates…) are
left out of the control group.

| Field | Meaning |
|---|---|
| `reviewed` | `true`: the bot reviewed it; `false`: control group. Absent on lines written before 1.4.0 (they are all reviewed PRs) |
| `control_group` | The enrichment pass that wrote the line also recorded the unreviewed PRs: only then can a coverage ratio be read from the file |
| `state`, `merged_at`, `author`, `head_sha` | State of the PR (`state` = `open`, `closed` or `merged`) |
| `created_at` | PR opened (GitHub) |
| `first_human_review_at`, `human_reviews` | Earliest submitted review, and number of submitted reviews, by a person other than the PR author and the bot (the bot is recognized by `BOT_LOGIN`, or by its review carrying a bot comment; other bots excluded). Replies typed in a review thread are `COMMENTED` reviews and count |
| `first_bot_run_at` | `ts` of the PR's first live run in the whole ledger (`null` in the control group) |
| `additions`, `deletions`, `changed_files` | Size of the PR. `null` in the control group: the list endpoint does not carry them, and one more call per PR was not worth it |
| `commits_after_first_review` | Commits pushed after the first run in the ledger (reviewed PRs only) |
| `human_comments`, `human_replies` | Human review comments (roots) and replies; other bots (Copilot, Sonar…) are excluded |
| `human_comments_on_silent_lines` | … on lines where the bot had nothing, not even dropped: **approximate misses** |
| `human_comments_on_dropped_findings` | … where the bot had a dropped finding (`dropped_low`, `dropped_unverified`) and nothing posted: **recall cost of the filter** |
| `human_comments_text` | Details, only with `include_text` (reviewed PRs only) |

The `human_comments_on_*` fields are only written for reviewed PRs; approximate misses
(`quality.json` `misses`) are computed on reviewed PRs only.

## `history.jsonl` — the team's PRs over years (`reviewme history`)

One line per merged or closed PR created since `--since`, written by a one-off scan
(re-runnable: the most recent line per (`repo`, `pr`) wins). It is not windowed and not
part of the `export` archive: the dashboard's `history` and `roi` pages read it from the
ledger folder. No title, no body, no comment text; review bodies are read during the scan
only to recognise the bot's own reviews.

| Field | Meaning |
|---|---|
| `ts` | Time of the scan |
| `repo`, `pr` | Repository and PR number |
| `author`, `author_is_bot` | PR author (`?` for a deleted account); `true` for a bot account (GitHub App, `[bot]` login). The dashboard leaves bot PRs and drafts out |
| `created_at`, `merged_at`, `closed_at` | GitHub dates |
| `state` | `merged` or `closed` (closed without merge) |
| `is_draft`, `base_ref`, `head_ref` | Draft flag and branch names |
| `additions`, `deletions`, `changed_files`, `commits` | Size of the PR |
| `human_reviews`, `first_human_review_at` | Submitted reviews (up to 100 per PR) by someone other than the author, `BOT_LOGIN` and bots, and whose body does not carry the bot's marker or signature; earliest one. With a PAT and no `BOT_LOGIN`, the bot's replies in threads (empty `COMMENTED` reviews under the PAT owner's name) may count |
| `review_threads` | All review threads, the bot's included |
| `bot_threads`, `human_threads` | Threads the bot opened (its posted inline findings in the ledger, live, outside dry-run) and the difference |
| `review_authors` | `{login: count}` of submitted reviews (not pending), every author except the PR's own and `[bot]` / GitHub App accounts. The bot's marker, signature and `BOT_LOGIN` are NOT filtered here: the counts exist to recognise, later, a bot of an earlier era that posted under a person's account (`[[stats.eras]] bot_login`). Logins only, as elsewhere in the ledger; absent on rows scanned before 1.4.0 |
| `thread_authors` | `{login: count}` of review threads (the first 100) by the login of their first comment (`?` for a deleted account). Same purpose and same caveat as `review_authors` |
| `review_first_at` | `{login: earliest submittedAt}` of the same reviews as `review_authors`: lets the dashboard recompute `first_human_review_at` without an earlier era's `bot_login` |
| `issue_comments` | Conversation comments, every author (bots included) |
| `labels` | Label names |
| `is_revert` | Head branch contains `revert` (GitHub's revert button names it `revert-<n>-…`) |
| `is_hotfix_branch` | Head branch contains `hotfix` (any case): an emergency fix. Weak proxy: titles are not stored, and naming habits vary. Rows written before 1.4.0's split also set it for `fix/` branches; the dashboard recomputes both flags from `head_ref` |
| `is_fix_branch` | Head branch is an ordinary fix: contains `fix/` (so `bugfix/` too) or starts with the word `fix` (`fix-123`, `fix_login`; not `fixture/`), and is not a hotfix branch. Counted apart from hotfixes: where `fix/` is the normal prefix for any bug fix, mixing both made a third of the PRs "hotfixes" |
| `bot_reviewed` | A live run outside dry-run exists for the PR in `runs.jsonl` (same repo), or the PR carries `REVIEW_LABEL`. The dashboard also counts a live run recorded after the scan |
| `jira_key`, `jira_issue_type`, `jira_created_at`, `is_bug_ticket` | Only with `--jira`: key read from the head branch (`JIRA_KEY_PREFIX` filter), the ticket's type and creation date, and `true` when the type is `Bug`, `Bogue`, `Defect` or a name in `JIRA_BUG_TYPES` (`null` when the ticket was not found) |

## `history-bugs.jsonl` — bug tickets created per month (`reviewme history --jira`)

| Field | Meaning |
|---|---|
| `ts` | Time of the scan |
| `projects` | Jira projects counted: `JIRA_KEY_PREFIX`, else every project whose keys appear in the scanned branches (comma-separated) |
| `month` | `YYYY-MM`; every month from `--since` to the scan, zeros included |
| `bugs` | Tickets of a type in `JIRA_BUG_TYPES` (default `Bug`) created that month |

The most recent line per (`projects`, `month`) wins; the dashboard sums the project sets.

The **bot start** used by both pages is `[stats] bot_since` when set, else the `ts` of
the first live run outside dry-run in the whole `runs.jsonl`: one implicit era named
"ReviewMe". With `[[stats.eras]]` (`name`, `since`, optional `label`), each PR belongs
to the era whose `since` is the latest on or before its `created_at` ("before" when
earlier than the first one); `bot_reviewed` and `REVIEW_LABEL` are then not read: a PR
is reviewed when a live run exists for it, or its era has a `label` that is among the
PR's `labels`. When the era also has a `bot_login`, that login's entries of
`review_authors` and `thread_authors` are subtracted from `human_reviews` and
`human_threads` (floor 0) on the era's labelled PRs, which count as reviewed; on rows
without those fields the stored totals are kept and the history page says a rescan is
needed. `first_human_review_at` becomes the earliest `review_first_at` of another login
(`null` when that login was the only reviewer). The ROI page compares "before" with
the last era.

## `triage.jsonl` — LLM classification of the feedback (`reviewme triage`)

Optional, written by `reviewme triage --since 14d` and only when the ledger has text
(`include_text`): a model reads the reply texts of contested findings and the human
comments on reviewed PRs, with no tool, and nothing else. One part per pass
(`triage/<timestamp>-<run_id>.jsonl`). In the archive, `reason` and the pattern texts are
removed unless `include_text` is on.

Rows with `kind = "finding"`, one per classified finding (the most recent per
`comment_id` wins):

| Field | Meaning |
|---|---|
| `ts`, `run_id` | Time and id of the triage pass |
| `repo`, `pr`, `comment_id`, `reviewer`, `rule_id` | The posted finding, from `findings.jsonl` |
| `cause` | One of `false_positive` (factually wrong), `missing_context` (true in isolation, but intended or a team convention), `preexisting` (not introduced by the PR), `duplicate` (already reported by a human or a tool), `unimportant` (true, not worth a comment), `fixed` (accepted and fixed), `question` (asked, no verdict), `other` |
| `confidence` | 0-100, the model's confidence in the cause from the texts alone (`null` when unreadable) |
| `reason` | One sentence in the project language (`[review] language`): text about the PR |
| `model` | Model(s) that answered (`simulated` with `REVIEWME_FAKE`) |

Input: every inline finding posted (live, outside dry-run) on a PR reviewed in the window
that has a human reply or a "wrong" reaction (`[stats.judgment] wrong`, 👎 by default),
with its reply texts (600 characters each, 8 replies at most), its message when the
ledger has it, its keyword labels and its 👎 count; no login.

One row with `kind = "patterns"` per pass:

| Field | Meaning |
|---|---|
| `ts`, `run_id`, `repo` | The pass (`repo` = the `--repo` filter, else `null`) |
| `patterns` | At most 8 `{title, count, example_comment_ids, suggested_rule}`: recurring themes among the human root comments of reviewed PRs that were not next to a spoken finding (`near_bot = false` in `prs.jsonl`, at most 150 comments, shortened to fit) |
| `misses`, `threads` | Comments and findings sent |
| `calls`, `cost_usd`, `model` | CLI calls (batches of ~60,000 characters), total cost of the pass, model |

## `quality.json` — computed quality block

`by_reviewer`, `by_rule` (`reviewer:rule_id`), `by_severity`: `posted`, `followed`,
`approved`, `contested`, `answered` and their rates, on INLINE posted remarks only
(`posted_in_summary` separately counts those delivered in a global comment).
A remark is **acted upon** if its line disappeared or if a reply says
"fixed"; **approved** if 👍 or a `fixed` reply, without contestation; **contested** if
👎 or a `false_positive` / `intended` reply.
`calibration`: per confidence band (0-50, 51-70, 71-85, 86-100), each `live` finding
counted once (its first appearance on the PR), with its breakdown by fate
(`would_post` = posted in dry-run), and the follow-up of posted remarks.
`contested_rules` (≥ 3 contestations), `remembered_rules`, `misses`, `cost_by_pr` and
`cost_by_reviewer` (keys `variant:…`), `baseline_vs_live` (per reviewer, on the same
PRs and commits: remarks each pipeline would have posted, and those shared by fingerprint),
`weekly`.

## `config-snapshot/<config_hash>/`

`manifest.json` (fingerprint of each settings file) and a copy of the `*.toml` files. The
prompts (`*.md`) are not copied: their fingerprint says they changed, the configuration
repository says how. The dashboard's "file" column for contested rules does not read the
snapshot: it searches the CURRENT project folder (`common/`, `guidelines/`,
`reviewers/*/`) for the rule id as a whole word, since rule ids come from the model's
output after the snapshot was taken.
