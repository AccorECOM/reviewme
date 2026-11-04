[← ReviewMe](../README.md) · **English** · [Français](fr/getting-started.md)

# Getting started

Requirements: Python 3.13, [uv](https://docs.astral.sh/uv/), the
[Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI on the `PATH`, and a
fine-grained GitHub token scoped to the reviewed repository. `--dry-run` prints what would
be posted without writing to GitHub; `REVIEWME_FAKE=1` skips the model entirely.

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

In CI: a GitHub Actions example lives in [`deploy/github-actions/`](../deploy/github-actions/),
a Bitrise example in [`deploy/bitrise-step.md`](../deploy/bitrise-step.md).

With the config in the reviewed repository, CI must be able to see the base branch: read
[Where the config is read from](how-it-works.md#where-the-config-is-read-from-base-branch)
before the first build, especially for PRs that do not target the default branch.
