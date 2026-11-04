[← ReviewMe](../README.md) · **English** · [Français](fr/history-and-triage.md)

# History, eras and triage

## History

The **history** page needs a one-off scan: `reviewme history --since 2024-01-01` reads the
repository's merged and closed PRs since that date (one GraphQL query per 100 PRs: about
40 queries for 4,000 PRs) into `history.jsonl`, with no title, body or comment text. Per
quarter, with a marker where ReviewMe starts: PRs merged per month, median hours to merge and to
the first human review, human reviews and threads per PR (and per 100 changed lines),
share of hotfix or revert branches and, apart, of ordinary `fix/` branches (`fix/…`,
`bugfix/…`, `fix-…`; a weak signal: it reads branch names), and, with
`--jira`, bug tickets created per month. Before/after is not proof (the team and the
product change too): the page says so and points at the control group. Run it again at
any time: the latest row per PR wins.

## Eras

When the bot's story has more than one start (a prototype, a pause, then
ReviewMe), name the periods with `[[stats.eras]]`:

```toml
[[stats.eras]]
name = "prototype"
since = "2026-03-10"
label = "ai-review-poc"
[[stats.eras]]
name = "pause"
since = "2026-10-09"
[[stats.eras]]
name = "ReviewMe"
since = "2026-11-03"
label = "review-me"
```

Each era has a `name`, a `since` date (`YYYY-MM-DD`, required) and an optional `label`:
the GitHub label that marked the PRs the bot of that era reviewed. Eras are consecutive
and sorted by `since` (two eras cannot start the same day): a PR belongs to the era
whose `since` is the latest on or before its creation, and to "before" when it predates
the first one. In an era with a `label`, a PR counts as reviewed when the ledger has a
live run for it or it carries that label. When that era's bot posted under a person's
account (a prototype run with the maintainer's token), add `bot_login = "maria"` to the
era (it needs a `label`): on the era's labelled PRs, that login's reviews and threads
stop counting as human. It reads per-login counts that `reviewme history` stores since
1.4.0; on older rows the page asks for a rescan. The history charts then get one dashed line
per era, captioned with its name, and the before/after table becomes a per-era table
(PRs created and merged, median hours to merge and to the first human review, human
reviews and threads per PR, revert, hotfix and `fix/` shares), with two rows for an era
with a label (reviewed / not reviewed). The ROI page compares "before" with the last
era. Without eras, `bot_since` (or the first live run) is one era named "ReviewMe".

`--jira` uses the Jira variables of the `us` reviewer (`JIRA_BASE_URL`, `JIRA_EMAIL`,
`JIRA_API_TOKEN`, optional `JIRA_KEY_PREFIX`): the ticket key is read from the branch
name, its type tells bug tickets apart (`Bug`, `Bogue`, `Defect`, plus the names in
`JIRA_BUG_TYPES`, default `Bug`, which is also the list counted per month).

## Triage: why findings get contested

**Why** findings get contested is what `reviewme triage --since 14d` answers. The keyword
labels of `enrich` are a rough sort; this optional pass has a model read the replies the
ledger already holds (so it needs `[ledger] include_text = true`) and give each finding
with a reply or a 👎 one cause: false positive, missing context (true, but the team does
it on purpose), pre-existing, duplicate, not important, fixed, question, other. It also
groups the human comments where the bot said nothing into at most 8 recurring patterns,
each with a suggested rule to write down. The quality page then charts the causes (per
reviewer too) and lists the recurring misses; the tuning page shows each contested rule's
main cause. The texts are third-party data: the call has no tool at all, the prompt says
so, and the output only goes to `triage.jsonl`, never to GitHub or the config. Titles and
suggested rules are model text about your PRs: the page shows them only with
`include_text`, the archive strips them otherwise. A pass over ~60 contested findings and
150 comments is one or two calls, about 0.10 to 0.25 $ with Sonnet; `--dry-run` prints the
size without calling anything. Run it weekly after `enrich`.

Each run also records its **trigger** (`REVIEWME_TRIGGER`: `pr_build`, `manual`,
`schedule`, `watch`, or any name; `unknown` when unset), shown per trigger in the usage
page. `reviewme watch` sets `watch` unless the deployment set its own.
