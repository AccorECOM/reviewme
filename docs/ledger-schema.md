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

## `findings.jsonl` — one line per finding and per run, posted or not

| Field | Meaning |
|---|---|
| `run_id` | Identifier of the engine's pass on the PR (shared by all its reviewers) |
| `ts` | UTC timestamp of the run |
| `repo`, `pr`, `head_sha`, `base_sha` | PR and commits reviewed |
| `variant` | `live` (evaluated pipeline) or `baseline` (control, always in `dry_run`) |
| `dry_run` | `true`: nothing was posted; `fate = posted` means "would have been posted" |
| `status` | `ok`, or `post_failed` if the GitHub write failed |
| `reviewer` | Reviewer (`tech`, `architect`…) |
| `model` | Model that **actually** answered (read from the CLI envelope) |
| `engine_commit` | Engine commit |
| `config_hash` | Fingerprint of the settings; details in `config-snapshot/<hash>/` |
| `fingerprint` | Fingerprint of the line (`sha1(path + normalized content)[:10]`), identical to the posted marker. Empty if the line is not in the diff |
| `path`, `line` | Location, new-file side, after re-anchoring on the diff |
| `severity_raw` | Severity written by the model, before normalization |
| `severity` | `BLOCKER`, `IMPORTANT` or `MINOR` |
| `confidence` | Confidence declared by the model (0-100) |
| `rule_id` | Rule identifier given by the model (may be empty) |
| `has_evidence` | The model cited a proof (`evidence`) |
| `verification` | `{status, confidence}`: `confirmed`, `refuted` or `skipped` (no verification pass) |
| `fate` | `posted`, `dropped_low` (below the threshold), `dropped_unverified` (refuted by verification), `out_of_diff` (delivered in the summary), `capped` (comment cap), `deduped` (already commented), `invalid` (no usable path or line) |
| `comment_id` | GitHub comment carrying the remark (posted now, or existing if `deduped`) |
| `in_summary` | Delivered in a global comment rather than inline |
| `message`, `suggestion` | Text, only with `include_text` |

## `runs.jsonl` — one line per reviewer and per run

`run_id`, `ts`, `repo`, `pr`, `head_sha`, `base_sha`, `variant`, `dry_run`, `reviewer`,
`model`, `engine_commit`, `config_hash`, plus:

| Field | Meaning |
|---|---|
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
| `reactions` | `[{content: "+1"\|"-1", user, role: "pr_author"\|"other"}]` |
| `replies` | `[{user, role, ts, label, contests, remember?, text?}]`. `label` (keyword-based, to be reviewed): `fixed`, `false_positive`, `intended`, `unimportant`, `question`, `other`. `contests` = `false_positive` or `intended`. `remember`: `{trusted, rule?}` for a `@reviewme remember …` reply, `trusted` if the author has write access, `rule` only with `include_text`. Reactions from other bots and from `BOT_LOGIN` are ignored |
| `thread_resolved`, `resolved_by` | Thread resolved on GitHub, and by whom |
| `human_comments_nearby` | Human comments 10 lines away or less, same file |

## `prs.jsonl` — state of a PR (`reviewme enrich`)

| Field | Meaning |
|---|---|
| `state`, `merged_at`, `author`, `head_sha` | State of the PR |
| `commits_after_first_review` | Commits pushed after the first run in the ledger |
| `human_comments`, `human_replies` | Human review comments (roots) and replies; other bots (Copilot, Sonar…) are excluded |
| `human_comments_on_silent_lines` | … on lines where the bot had nothing, not even dropped: **approximate misses** |
| `human_comments_on_dropped_findings` | … where the bot had a dropped finding (`dropped_low`, `dropped_unverified`) and nothing posted: **recall cost of the filter** |
| `human_comments_text` | Details, only with `include_text` |

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
repository says how.
