[← ReviewMe](../README.md) · **English** · [Français](fr/security.md)

# Security

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
  `REVIEWME_CONFIG_SOURCE=pr`. Details, and when a PR is skipped: [Where the config is read from](how-it-works.md#where-the-config-is-read-from-base-branch).
- Secrets and absolute paths are scrubbed before posting; the webhook rejects unsigned
  requests (HMAC).
- A `precheck` is code supplied by the project: review it like code.
- **On a personal machine** (watch mode), the agent runs with your user's file access:
  the tools are read-only, but a hostile diff could try to make it read files outside the
  repo. That is why PRs from forks are left alone by default; for a repo open to outside
  contributors, prefer `--label` or a dedicated user, container or VM.
