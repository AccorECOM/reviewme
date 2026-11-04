[← ReviewMe](../README.md) · **English** · [Français](fr/watch-mode.md)

# Run it on your machine (watch mode)

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
