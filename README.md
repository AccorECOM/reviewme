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

Requirements: Python 3.13, [uv](https://docs.astral.sh/uv/), the
[Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI on the `PATH`, and a
fine-grained GitHub token scoped to the reviewed repository. `--dry-run` prints what would
be posted without writing to GitHub; `REVIEWME_FAKE=1` skips the model entirely. The full
walkthrough is in [Start in 5 minutes](#start-in-5-minutes).

---

## The idea: method in the engine, knowledge in the config

ReviewMe keeps two things apart:

- **the engine (this repository)** knows *how* to review: the method, the output format,
  the verification pass, security, talking to GitHub. It is the same for every project;
- **a reviewer (a config folder)** knows *what* to look for in *this* project: its persona,
  its rules, when it runs. A team writes as many as it needs.

```mermaid
flowchart LR
    subgraph Engine["Engine (this repo): how to review"]
        M1[Two-phase method]
        M2[JSON output contract]
        M3[Verification pass]
        M4[Security and GitHub]
    end
    subgraph Config["Project config: what to look for"]
        R1["reviewers/tech/"]
        R2["reviewers/security/"]
        R3["reviewers/i18n/"]
        C["common/ (team rules)"]
    end
    Config --> Engine --> PR[(Pull Request)]
```

A project combines two kinds of reviewers, enabled in different ways:

| | Core reviewer | Custom reviewer |
|---|---|---|
| Defined | in the engine | in your repository, `reviewers/<name>/` |
| How to enable | its name in `core = [...]` in `project.toml` | its folder exists: nothing else to declare |
| How to disable | remove its name from the list | `enabled = false` in its `reviewer.toml` |
| Updates | follows the engine | yours |

Full example of a project using both:

```text
.reviewme/projects/shop/
├── project.toml              [reviewers] core = ["tech", "advisor"]
└── reviewers/
    └── security/             custom reviewer, active because the folder exists
        ├── reviewer.toml
        └── system.md
```

Three reviewers run on its PRs: `tech` and `advisor` (core), `security` (custom).

### Core reviewers: enable them by name

Shipped with the engine (`config/templates/reviewers/`), they follow its updates. Enable
them in the project's `project.toml`; removing a name disables it.

```toml
# .reviewme/projects/shop/project.toml
[reviewers]
core = ["tech", "advisor"]
```

| Name | Role | Output | Requires |
|---|---|---|---|
| `tech` | bugs, logic, security, repository conventions | inline | — |
| `us` | are the ticket's acceptance criteria covered? | one comment, silent when all are covered | Jira (`JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`) |
| `i18n` | missing or doubtful translations | inline and summary | translation files in the PR (`*.strings`, `values*/strings.xml`…) |
| `advisor` | what a human must check themselves on this PR | one comment, silent when nothing applies | — |

**Adapt them without copying**: the project can tell a core reviewer where its docs
are, when to run and what it should know. Everything else (method, output, verification)
stays the engine's.

```toml
[reviewers.tech]
read = ["Documentation/ARCHITECTURE.md", "Documentation/"]   # added to what it reads
paths = ["src/**"]                                            # when it runs
instructions = "Business services live in src/services; no network calls from views."
# model = "…"   max_turns = 50   priority = 10   enabled = false
```

**Let an AI do it**: the [`skills/reviewme-create-reviewer`](skills/reviewme-create-reviewer/SKILL.md)
skill guides Claude Code to explore the repository, choose between adapting a
core reviewer and creating one, write a short persona and validate the config. Copy it into
the project's `.claude/skills/`, then ask for instance "add a reviewer that checks our
migrations".

### Custom reviewers: a folder in the project

For what is specific to **your** code, a reviewer is a folder under
`.reviewme/projects/<project>/reviewers/<name>/`, in the reviewed repository (or in a
separate config repository, via `REVIEWME_CONFIG_HOME`). The name is free; a folder named
after a core reviewer replaces it for that project.

```text
.reviewme/projects/shop/
├── project.toml                 project settings (optional)
├── common/
│   └── team-rules.md            team rules, shared by every reviewer
└── reviewers/
    └── security/
        ├── reviewer.toml        when it runs, what it reads
        └── system.md            its persona: what it looks for in THIS project
```

```toml
# reviewers/security/reviewer.toml
output_mode = "inline"                 # inline | global | mixed
priority    = 10

[when]                                 # only runs when these files change
paths = ["src/api/**", "src/auth/**"]

[context]                              # read from the reviewed repo, never copied here
read = ["docs/security.md"]

```

```markdown
<!-- reviewers/security/system.md -->
# Shop security reviewer
You look for: routes without access control, SQL built by string concatenation,
hard-coded secrets, customer data written to logs. `@public` decorators are intentional.
```

The engine adds the method, the output format and the repository's instruction files
(`CLAUDE.md`, `AGENTS.md`, `.claude/rules/`) on its own. The persona stays short.

---

## The life of a PR

```mermaid
flowchart TD
    A[PR opened or updated] --> B{Which reviewers?}
    B -->|"when.paths, ticket found…"| C[Context: repo rules,<br/>ticket, precheck facts]
    C --> D["Phase 1 + 2: claude -p<br/>hunt, then evidence per finding"]
    D --> E["Verification: a 2nd claude -p<br/>confirms or refutes each finding"]
    E -->|refuted| X[(Ledger only)]
    E -->|confirmed| F{Filters}
    F -->|"BLOCKER, IMPORTANT"| G[Inline comment]
    F -->|"MINOR, moved code, outside diff"| H[Single summary]
    F -->|"already said, cap reached"| X
    G --> R[(Ledger)]
    H --> R
```

- **Each reviewer is an independent, read-only `claude -p`** (`Read`, `Grep`, `Glob`,
  `git log/show/diff`), run in parallel.
- **Verification replaces the confidence threshold as the main filter.** A second call
  receives the findings and must cite the line that proves each one; anything unproven
  is not posted.
- **No duplicates across commits**: every comment carries a fingerprint of its line. When
  the line changes, the bot replies in the thread instead of posting again.

---

## Start in 5 minutes

```sh
git clone https://github.com/AccorECOM/reviewme.git && cd reviewme
uv sync
npm install -g @anthropic-ai/claude-code
cp .env.example .env            # GITHUB_TOKEN, GITHUB_REPO, REPO_PATH

uv run reviewme review --repo my-org/my-app --pr 123 --dry-run
```

`--dry-run` computes everything and prints what **would** be posted, without writing to
GitHub. Without `PROJECT`, a single generic reviewer runs (simple mode). For a real project, the
shortest path is a `project.toml` enabling core reviewers (see above):

```sh
mkdir -p .reviewme/projects/my-app
printf '[reviewers]\ncore = ["tech", "advisor"]\n' > .reviewme/projects/my-app/project.toml
REVIEWME_CONFIG_HOME=$PWD/.reviewme PROJECT=my-app uv run reviewme projects
```

To start from an editable copy of a core reviewer: `reviewme init-project my-app --reviewers tech`.

In CI: a GitHub Actions example lives in [`deploy/github-actions/`](deploy/github-actions/),
a Bitrise example in [`deploy/bitrise-step.md`](deploy/bitrise-step.md).

---

## Run it on your machine (watch mode)

No CI needed: `reviewme watch` checks a repository's open PRs every few minutes and
reviews each PR, again after every new push. It calls the Claude Code CLI exactly as it is set up on the
machine: ReviewMe does not choose how the CLI reaches the model. If `claude -p "hello"`
works in that terminal, watch works.

```sh
git clone git@github.com:my-org/my-app.git ~/src/my-app
uv run reviewme watch --repo my-org/my-app --repo-path ~/src/my-app --every 10m
```

Start with `--once --dry-run`: one round, nothing posted, the log shows which PRs would be
reviewed and why the others are left alone.

| Which PRs | Default | Change it with |
|---|---|---|
| open PRs | all of them | `--label review-me`: only PRs carrying this label |
| PRs labelled `no-ai-review` | left alone | `--skip-label NAME` (`""` disables it, or `REVIEWME_SKIP_LABEL`) |
| drafts | left alone | `--include-drafts` |
| PRs from forks | left alone | `--include-forks` (authors outside the repo) |
| a commit already reviewed | not reviewed again | a new push is reviewed |
| a commit whose review failed | not retried | a new push is retried |

How it behaves:

- **Your clone is never touched.** Each PR is checked out in a temporary `git worktree` at
  its head commit, next to the clone, and removed afterwards. Uncommitted work and the
  current branch stay as they are. `--repo-path` (or `REPO_PATH`) must be a clone of
  `--repo`: anything else is refused.
- **Same review as CI**: same reviewers, same config (still read from the base branch),
  same dedup, so running watch next to a CI job does not double the comments.
- **One PR at a time** by default (`--parallel N` to change it), every 10 minutes
  (`--every 90s|10m|1h`, or `POLL_INTERVAL`; minimum 1 minute).
- **Usage limits**: when the model's quota is reached, the commit is *not* marked
  as failed. The loop waits until the reset time the CLI gives (otherwise it doubles the
  wait, up to one hour), then resumes where it stopped.
- Comments are posted with the identity of `GITHUB_TOKEN`: your own account if it is
  your personal token, a bot if it is a GitHub App or a bot account's token.
- One watch per repository at a time (lock in `data/`). `--dashboard` serves the read-only
  dashboard on `127.0.0.1:8420`. Stop with Ctrl-C: the current review finishes first.

To keep it running, use a terminal multiplexer, or `--once` from `cron`/`launchd`.
`reviewme-poll` still works: it is the same loop configured from `.env` only
(`REVIEW_LABEL`, `REVIEW_ALL_PRS`, `POLL_INTERVAL`).

---

## What lands on the PR

| Finding | Where | Why |
|---|---|---|
| `BLOCKER`, `IMPORTANT` on code introduced by the PR | inline | worth the developer's attention |
| `MINOR` | summary | useful, but not worth a notification per line |
| on code the PR only **moved** | summary, tagged | not a defect of this PR |
| outside the diff lines | summary | GitHub rejects comments outside the diff |
| not confirmed by verification | nowhere (ledger) | a false positive costs the team's trust |
| **💡 Review tip** (`advisor` reviewer): something a human must check themselves (call order changed, file placed outside its usual folder…) | one comment | only when it applies, anchored in files of the PR; 3 at most |
| clean PR | nothing | silence beats a "LGTM" |

The language of the posted remarks and of the bot's headings is set per project in
`project.toml`: `[review] language = "fr"` (default `en`).

The bot only posts comments: never an approval or a change request.

---

## Measuring whether it helps

```mermaid
flowchart LR
    R[review] -->|every finding,<br/>posted or not| L[(ledger)]
    N["enrich (nightly)"] -->|"reactions, replies,<br/>line fixed?"| L
    L --> S[reviewme stats]
    L --> D["reviewme stats --html<br/>dashboard.html"]
    L --> X["reviewme export"]
    X --> A[JSONL archive<br/>for analysis]
    X --> W[weekly-report.md<br/>+ dashboard.html]
```

The ledger keeps **every** finding with its fate and confidence, including the dropped
ones, so you can answer "is this threshold costing us real bugs?". `reviewme stats` shows,
per reviewer, the share of findings acted upon, approved and contested, and calibration by
confidence band. The schema is in [`docs/ledger-schema.md`](docs/ledger-schema.md).
Finding text is only stored when the project asks for it
(`[ledger] include_text = true`).

`reviewme stats --html dashboard.html` writes a standalone dashboard from the ledger: one
page per section (summary, quality, usage, cost, ROI, reliability, tuning, history, Hall
of Fame), no
network request, readable on a phone. `reviewme export` writes the same page next to
`weekly-report.md`, and `reviewme-web` serves it fresh on `/dashboard` (its numbers on
`/api/dashboard`). It only shows counters, reviewer and rule ids, PR numbers and logins
(or masked names): never a PR title, a finding, a code excerpt or a reply, even with
`include_text` (the one exception: the recurring misses of `reviewme triage`, below).
Every chart and table has a one-line reading under it (trend, comparison, top item),
generated from its numbers. It works without a project; with one, `[stats]` in `project.toml` shapes it:

```toml
[stats]
sections = ["summary", "quality", "usage", "cost", "roi", "reliability", "tuning",
            "history", "hall_of_fame"]
names = "login"             # or "masked": dev_1, dev_2… instead of logins
window = "90d"              # 14d, 36h…
period = "week"             # or "sprint"
sprint_anchor = "2026-01-05"
sprint_anchor_number = 1
sprint_length_days = 14
control_group = true        # enrich also records the PRs the bot never reviewed
debrief = true              # the summary opens with a few generated sentences
bot_since = "2026-10-01"    # optional: the day the bot started (default: first live run)

[stats.triage]              # optional: `reviewme triage` (LLM sort of the feedback)
enabled = true              # default true
model = "claude-sonnet-5-5" # default: CLAUDE_MODEL, else the CLI default
max_budget_usd = 0.50       # budget of one pass, default 0.50

[stats.judgment]            # GitHub reactions -> judgment of a finding
useful = ["+1", "hooray"]
wrong = ["-1"]
off_topic = ["confused"]
saved_me = ["rocket"]

[stats.roi]                 # optional: the assumptions of the ROI estimate
hourly_cost = 80            # cost of one developer hour, in `currency` (required)
currency = "EUR"            # default EUR
usd_rate = 0.92             # 1 USD in `currency` (the API cost is in USD); default 1.0
minutes_per_finding = 12    # saved per finding acted upon; default 12
minutes_per_blocker = 240   # saved per BLOCKER fixed before merge; default 240
human_first_review_hours = 20   # used when the control group does not measure it
```

| `[stats]` key | Default | Purpose |
|---|---|---|
| `sections` | all of them | pages and menu order: `summary`, `quality`, `usage`, `cost`, `roi`, `reliability`, `tuning`, `history`, `hall_of_fame` |
| `names` | `"login"` | `"masked"` numbers people `dev_1`, `dev_2`… by first appearance |
| `window` | `"90d"` | period covered (`export` uses its own `--since`) |
| `period` | `"week"` | chart buckets: ISO `week` or `sprint` |
| `sprint_anchor`, `sprint_anchor_number`, `sprint_length_days` | `2026-01-05`, `1`, `14` | first day and number of a known sprint, and sprint length |
| `[stats.judgment]` | as above | reaction contents counted as `useful`, `wrong`, `off_topic`, `saved_me` |
| `[stats.roi]` | absent | assumptions of the ROI estimate (above). Absent: the ROI page shows its two measured levels and says why the estimate is hidden; present: the estimate, and an "hours saved (estimated)" tile in the summary. `hourly_cost` is required, every number must be > 0 |
| `bot_since` | first live run | `YYYY-MM-DD` the bot started, for the before/after of the history and ROI pages, when the ledger does not go back that far |
| `[[stats.eras]]` | none | named periods of the history and ROI pages (below); when present, they replace `bot_since` |
| `[stats.triage]` | enabled, CLI model, 0.50 $ | `reviewme triage`: `enabled = false` turns the command off, `model` picks the model of that call only, `max_budget_usd` caps one pass (`--max-budget-usd` overrides it) |
| `debrief` | `true` | the summary opens with a "Debrief" card: up to seven short sentences generated from the numbers (no LLM), each only when its data exists: volume and coverage, quality, cost, ROI (control group, else the estimate), history (before vs the last era, not causal), incidents, the most contested rule. `false` hides it |
| `control_group` | `true` | `reviewme enrich` also writes a `prs.jsonl` line (`reviewed = false`) for every PR updated in the window that the bot never reviewed: two read-only GitHub calls per such PR. It gives the dashboard its coverage (merged PRs reviewed) and the comparison group for cycle times. `REVIEWME_CONTROL_GROUP=0` turns it off for one run |

An unknown section or key fails at load time, like the rest of `project.toml`. For
the dashboard, an invalid project is never silent: `reviewme stats --html` (and `export`,
`reviewme-web`) prints a warning naming the error. When only the reviewers are at fault
(a folder that holds the `[stats]` of a repository but no reviewer yet), the page still
follows that folder's `[stats]`; when `project.toml` itself is invalid, it uses the
defaults.

The **ROI** page answers "is it worth it?" three ways, from the most to the least solid,
each labelled as such: (1) *measured* on the control group, reviewed vs unreviewed PRs of
the same window (time to first human review, to merge, human reviews and comments per
PR, how soon the bot answers); (2) *measured over time*, the before/after of the history
page; (3) *estimated* from `[stats.roi]`: findings acted upon and BLOCKERs fixed before
merge, valued in time and money against the API cost, with the break-even (findings
acted upon per PR needed to cover the cost per PR), every assumption printed next to the
numbers.

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

**Eras.** When the bot's story has more than one start (a prototype, a pause, then
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

---

## Security

The diff, the ticket and the comments are written by third parties: they are **data**,
never instructions.

- **Read-only tools**, allowlist locked in code: no `Write`, no sub-agents, never
  `--dangerously-skip-permissions`. Hooks and MCP servers of the reviewed repo are not
  loaded. A reviewer that declares its own MCP servers (`[mcp] config`) or plugins
  (`[plugins] install`) is also allowed their tools (`mcp__<server>`, `Skill`); the
  verification pass is not. A skill gets no more rights than the allowlist.
- **A PR cannot change its own review**: when the config lives in the reviewed repo
  (`.reviewme/`), it is read from the base branch, as are `CLAUDE.md` and `.claude/rules/`.
  To try a new reviewer inside its own PR, start a build by hand with
  `REVIEWME_CONFIG_SOURCE=pr`.
- Secrets and absolute paths are scrubbed before posting; the webhook rejects unsigned
  requests (HMAC).
- A `precheck` is code supplied by the project: review it like code.
- **On a personal machine** (watch mode), the agent runs with your user's file access:
  the tools are read-only, but a hostile diff could try to make it read files outside the
  repo. That is why PRs from forks are left alone by default; for a repo open to outside
  contributors, prefer `--label` or a dedicated user, container or VM.

---

## Quick reference

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
| `REVIEWME_TRIGGER` | what started the run, recorded in the ledger (`pr_build`, `manual`, `schedule`, `watch`) |
| `REVIEWME_CONTROL_GROUP` | `enrich`: `0`/`1` overrides `[stats] control_group` |
| `JIRA_BUG_TYPES` | `history --jira`: issue type names counted as bugs (default `Bug`) |
| `REVIEWME_FAKE`, `REVIEWME_DEBUG` | no model call; verbose log |
| `ANTHROPIC_BASE_URL`, `CLAUDE_MODEL` | LLM gateway, model |
| `POLL_INTERVAL`, `REVIEWME_SKIP_LABEL` | watch: default interval, opt-out label (default `no-ai-review`) |

| `project.toml` key | Purpose |
|---|---|
| `[review] language` | language of the posted remarks and of the bot's headings (default `en`, e.g. `"fr"`) |
| `[ledger] include_text` | keep finding and reply texts in the ledger (default `false`) |
| `[stats]` | dashboard sections, names, window, period, `bot_since`, `[stats.roi]`, `[stats.triage]` (see [Measuring whether it helps](#measuring-whether-it-helps)) |

Every key, commented: [`.env.example`](.env.example). Every `reviewer.toml`
option: [`docs/reviewme.html`](docs/reviewme.html). Known limits:
[`docs/LIMITATIONS.md`](docs/LIMITATIONS.md).

## Tests

```sh
uv sync --group dev
uv run pytest -q
uv run ruff check .
```

To judge the dashboard before a team has data, `reviewme demo-data --out DIR [--seed N]
[--days 90] [--language fr] [--now YYYY-MM-DD]` writes a realistic synthetic ledger
(about 70 reviewed and 40 unreviewed PRs over 90 days, two years of history across three
eras, fictional `*-demo` logins) into `DIR/ledger` and builds
`DIR/dashboard-demo-<language>.html` from it, through a real `project.toml`. The same seed
and date give the same files. Never point it at a real ledger folder: it replaces
`DIR/ledger`.

## Contributing and security

Guidelines in [`CONTRIBUTING.md`](CONTRIBUTING.md). Threat model and how to report a
vulnerability in [`SECURITY.md`](SECURITY.md).

## License

Apache 2.0 — free to use, modify and share. See [`LICENSE`](LICENSE).
