# Security

ReviewMe reads untrusted content (pull request diffs, ticket text, developer replies)
and hands it to a language model that can read the repository. Treat every input as
data, never as instructions.

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use GitHub's private
vulnerability reporting on this repository ("Security" tab, "Report a vulnerability").
You will get an acknowledgement, then a fix or a workaround before any public disclosure.

## Threat model and mitigations

| Risk | Mitigation in the engine |
|---|---|
| Prompt injection from the diff | Read-only tool allowlist (`Read`, `Glob`, `Grep`, a few `git` commands); no `Write`, no sub-agents, no hooks or MCP servers from the reviewed repo |
| A PR rewriting its own review | Project config, `CLAUDE.md` and `.claude/rules/` are read from the base branch, never from the PR |
| Exfiltration through a posted comment | `reviewme/scrub.py` redacts tokens, keys, PEM blocks and local paths before anything reaches GitHub |
| Forged webhook events | HMAC signature required; the webhook refuses to start without `WEBHOOK_SECRET` |
| Runaway cost | `MAX_BUDGET_USD` per reviewer, `MAX_COMMENTS_PER_PR` per pull request |
| Code supplied by a project (`precheck.py`) | Runs without the job's credentials in its environment (`GITHUB_TOKEN`, `ANTHROPIC_*`, `JIRA_*` are not passed); the file system stays readable, so review it like any other code |

## What the engine cannot do for you

The model can still read any file the process can read. Run ReviewMe in a sandbox with
no outbound network except GitHub (and your model gateway), on a clone that contains
only the reviewed repository, with a token scoped to that repository
("Pull requests: Read and write", plus "Contents: Read" on a private repository so the
engine can read files at a commit). Never run it with a classic `repo` PAT.
