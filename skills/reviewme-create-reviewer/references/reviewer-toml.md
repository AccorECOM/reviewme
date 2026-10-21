# Reference: project.toml and reviewer.toml

Source of truth: the engine's `reviewme/projects.py`. When in doubt, read it.

## project.toml — `.reviewme/projects/<project>/project.toml`

| Key | Role |
|---|---|
| `[reviewers] core = [...]` | enabled core reviewers: `tech`, `us`, `i18n`, `advisor` |
| `[reviewers.<name>] read` | files/folders ADDED to what it reads (file = content, folder = inventory of the `.md` files) |
| `[reviewers.<name>] paths` | replaces its `when.paths` |
| `[reviewers.<name>] instructions` | text appended to the end of its persona |
| `[reviewers.<name>] model`, `max_turns`, `priority`, `enabled` | override the core values |
| `[review] language` | language of the posted remarks and of the bot's headings (default `en`) |
| `[ledger] include_text` | the ledger keeps the text of the remarks (default `false`) |

Any other key under `[reviewers.<name>]` blocks loading. `[reviewers.<name>]` is ignored
if a `reviewers/<name>/` folder exists (the folder takes precedence).

## reviewer.toml — `.reviewme/projects/<project>/reviewers/<name>/reviewer.toml`

| Key | Values | Default |
|---|---|---|
| `output_mode` | `inline` (remarks on lines), `global` (a single comment), `mixed` | `inline` |
| `priority` | integer, smaller = first | 100 |
| `[when] paths` | globs; absent = every PR | — |
| `[context] read` | paths in the reviewed repository | — |
| `requires` | `["jira_ticket"]` only; otherwise the reviewer is skipped | — |
| `precheck` | script in the reviewer's folder; receives the diff on stdin, writes facts in markdown | — |
| `max_turns` | integer > 0 | 30 |
| `model` | model identifier | the job's one |
| `enabled` | `false` = asleep | `true` |
| `silent_when_clean` | nothing posted when there is nothing to say; **`global` mode only** | `false` |
| `findings` | `false` = advice-only reviewer: never a remark, its review advice is displayed (this is the role of the core `advisor`) | `true` |
| `[verification] enabled`, `model`, `max_turns` | verification pass on the remarks | enabled |
| `[mcp] config` | MCP file in the reviewer's folder | — |

## When to write a precheck

A rule that can be checked mechanically (a missing function, a forbidden pattern, a missing
translation key) costs less and makes no mistakes as a script. The precheck writes facts
("`0003_x.py`: empty `downgrade()`"); the reviewer keeps the judgement. It is code run
in CI: review it like code, with no network access or writes.
