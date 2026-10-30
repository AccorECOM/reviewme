# Bitrise deployment

Bitrise **orchestrates**, ReviewMe does the one-shot review. The workflow is outside the `trigger_map`
and is started through the Build Trigger API, or on a `pull_request` trigger depending on what
the team wants.

## A Linux runner, even for an iOS app

The review **compiles nothing**: it reads a diff and text files. It needs neither
Xcode, nor a simulator, nor the Swift toolchain. A macOS stack would bring nothing and costs
noticeably more per minute.

Bitrise lets you override the stack **per workflow**, including in an iOS app whose
default stack is macOS:

```yaml
  ReviewMe:
    summary: AI review of the current PR (inline comments)
    meta:
      bitrise.io:
        stack: ubuntu-noble-24.04-bitrise-2025    # no Xcode needed
        machine_type_id: g2.linux.medium
    steps:
    - activate-ssh-key@4: {}
    - git-clone@8: {}                             # clones the repo to review
    - script@1:
        title: ReviewMe
        inputs:
        - content: |
            #!/usr/bin/env bash
            set -eo pipefail        # NO `set -x`: this step handles secrets

            # 1. Tools, in PINNED and verified versions — this job carries secrets.
            npm install -g "@anthropic-ai/claude-code@<version>"   # npm checks integrity
            # uv: release archive + SHA256 published by the vendor (see your app's bitrise.yml)
            export PATH="$HOME/.local/bin:$PATH"
            echo '{"hasCompletedOnboarding": true}' > ~/.claude.json

            # 2. The config: a single clone, the engine comes with it (.core submodule)
            git clone --recurse-submodules "$REVIEWME_INSTANCE_URL" /tmp/reviewme-config

            # 3. Review
            cd /tmp/reviewme-config
            ./reviewme review --repo "$GITHUB_REPO" --pr "$PR_NUMBER"
        # Secrets: never in clear text here, declared in the app's Bitrise Secrets
```

## Simplest variant: the config lives in the reviewed repository

If the project carries its own configuration (`.reviewme/` at its root), there is **nothing
extra to clone**: the repository is already there, just point into it.

```bash
# 1. Tools
curl -fsSL https://claude.ai/install.sh | bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

# 2. The engine (public repository, no credentials)
# Pin a COMMIT, not a branch or a tag: a tag can be moved.
git clone --quiet --filter=blob:none --no-checkout <engine-url> /tmp/reviewme
git -C /tmp/reviewme checkout --quiet <full-sha>
cd /tmp/reviewme && uv sync --frozen

# 3. The config comes from the reviewed repository
export REVIEWME_CONFIG_HOME="$BITRISE_SOURCE_DIR/.reviewme"
export PROJECT=my-project
export REPO_PATH="$BITRISE_SOURCE_DIR"
export REVIEWME_TRIGGER=pr_build      # recorded in the ledger
uv run reviewme review --pr "$PR"
```

⚠️ Protect `.reviewme/` with a `CODEOWNERS` entry: without it, a PR can modify the
configuration that judges it.

## Variables

| Variable | Source | Role |
|---|---|---|
| `GITHUB_TOKEN` | Bitrise Secret | PR R/W on the target repo (or GitHub App, see `GITHUB_APP_*`) |
| `ANTHROPIC_API_KEY` | Bitrise Secret | Review engine. Through an internal gateway: `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` |
| `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS` | `1` | Internal proxies often reject the prompt caching headers |
| `REVIEWME_INSTANCE_URL` | Workflow env | The config repository (with the engine as the `.core` submodule) |
| `PROJECT` | Workflow env | Which project of the instance to use |
| `REPO_PATH` | `$BITRISE_SOURCE_DIR` | The clone to explore to put findings in context |
| `PR_NUMBER` | `$BITRISE_PULL_REQUEST` | The PR to review |
| `REVIEWME_TRIGGER` | `pr_build` | Recorded in the ledger: separates PR builds from manual (`manual`) and scheduled (`schedule`) runs |

## Ephemeral runner: what it implies

| | Effect |
|---|---|
| **Comment dedup** | None. GitHub is the source of truth: the markers live in the PR comments, not on the runner's disk. |
| **`data/state.json`** | Lost on every build. Consequence: a **rerun on the same commit** pays again for a review that will post nothing (everything deduplicated). On a new commit, no impact. |
| **Dashboard / hall of fame** | Does not work: it reads `data/reviews/*.json`, wiped with the runner. It needs a shared store — a separate project. |

Do **not** export `data/` as a build artifact to work around it: artifacts can be
downloaded by anyone with access to the build, and `data/` contains the content of real PRs.

A scheduled workflow that runs `reviewme enrich --since 14d` (nightly) can also run
`reviewme triage --since 7d` once a week, after `enrich`: it needs `[ledger] include_text
= true` and a persisted ledger, calls the model with no tool, and costs at most
`[stats.triage] max_budget_usd` (0.50 $ by default) per pass.

## First hookup

Run with `--dry-run` on a few PRs, read what *would* have been posted, tune
`CONFIDENCE_THRESHOLD` and `MAX_COMMENTS_PER_PR`, and only then remove the flag.
