[← ReviewMe](../README.md) · **English** · [Français](fr/reference.md)

# Reference

| Command | Purpose |
|---|---|
| `reviewme review --repo O/R --pr N` | review one PR (`--dry-run`, `--force`) |
| `reviewme init-project <name> --reviewers tech,us` | create a project from the skeleton |
| `reviewme projects` | list projects and their reviewers |
| `reviewme enrich --since 14d` | record what happened to posted findings (nightly) |
| `reviewme export --since 7d --out DIR` | analysis archive and readable report |
| `reviewme triage --since 14d [--dry-run]` | optional: a model sorts why findings were contested and groups recurring misses (needs `include_text`, `[stats.triage]`) |
| `reviewme history --since 2024-01-01 [--jira]` | one-off scan of the merged and closed PRs since a date, for the before/after pages (re-runnable) |
| `reviewme stats [--html F]` | counters and quality block; `--html` writes the dashboard |
| `reviewme feedback --pr N` | developer replies, as rules to review |
| `reviewme watch --repo O/R --every 10m` | poll the open PRs and review each one (`--once`, `--dry-run`, `--label`) |
| `reviewme-webhook` · `reviewme-poll` · `reviewme-web` | webhook server, poll loop (legacy name of watch), dashboard |

| Variable | Purpose |
|---|---|
| `GITHUB_TOKEN` (or `GITHUB_APP_ID` + key) | GitHub access |
| `REPO_PATH` | local clone of the reviewed repo |
| `REVIEWME_CONFIG_HOME`, `PROJECT` | where the config lives, which project |
| `CONFIDENCE_THRESHOLD` | confidence threshold (default 80) |
| `MAX_COMMENTS_PER_PR` | comment cap per PR (default 10) |
| `REVIEWME_INLINE_MIN_SEVERITY` | lowest severity posted inline (default `IMPORTANT`) |
| `REVIEWME_VERIFY` | `0` turns verification off |
| `REVIEWME_CONFIG_SOURCE` | `base` (default) or `pr` |
| `REVIEWME_TRUSTED_BASES` | extra trusted base branches for the config, e.g. `develop,release/*` (see [Where the config is read from](how-it-works.md#where-the-config-is-read-from-base-branch)) |
| `FETCH_BASE_TIMEOUT` | timeout of the base branch fetch, seconds (default 30; never `0` in CI with an in-repo config) |
| `REVIEWME_TRIGGER` | what started the run, recorded in the ledger (`pr_build`, `manual`, `schedule`, `watch`) |
| `REVIEWME_CONTROL_GROUP` | `enrich`: `0`/`1` overrides `[stats] control_group` |
| `JIRA_BUG_TYPES` | `history --jira`: issue type names counted as bugs (default `Bug`) |
| `REVIEWME_FAKE`, `REVIEWME_DEBUG` | no model call; verbose log |
| `ANTHROPIC_BASE_URL`, `CLAUDE_MODEL` | LLM gateway, model |
| `REVIEWME_SHOW_MODEL`, `REVIEWME_SHOW_SCORE`, `REVIEWME_CLEAN_NOTE` | simple mode: `[review] show_model` / `show_score` / `clean_note` |
| `POLL_INTERVAL`, `REVIEWME_SKIP_LABEL` | watch: default interval, opt-out label (default `no-ai-review`) |

| `project.toml` key | Purpose |
|---|---|
| `[review] language` | language of the posted remarks and of the bot's headings (default `en`, e.g. `"fr"`) |
| `[review] show_model`, `[review] show_score` | model (configured and answered) and score under each remark (default `false`, see [Model and score on the comments](on-the-pr.md#model-and-score-on-the-comments)) |
| `[review] clean_note` | one short "review complete, no remarks" comment on a clean PR (default `false`, see [Clean PR note](on-the-pr.md#clean-pr-note)) |
| `[ledger] include_text` | keep finding and reply texts in the ledger (default `false`) |
| `[stats]` | dashboard sections, names, window, period, `bot_since`, `[stats.roi]`, `[stats.triage]` (see [Measuring whether it helps](measuring.md)) |

Every key, commented: [`.env.example`](../.env.example). Every `reviewer.toml`
option: [`docs/reviewme.html`](reviewme.html). Known limits:
[`docs/LIMITATIONS.md`](LIMITATIONS.md).
