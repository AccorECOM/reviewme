---
name: reviewme-create-reviewer
description: Creates or adapts a ReviewMe reviewer (AI code review bot) for a repository — by enabling and tuning a core reviewer (tech, us, i18n, advisor) in project.toml, or by writing a custom reviewer (reviewer.toml + system.md). Explores the repository to find docs and conventions, picks the lightest path, writes a short persona, then validates the config. Use it whenever someone wants to plug ReviewMe into a project, "add a reviewer", "a reviewer that checks X", configure `.reviewme/`, `project.toml` or `reviewer.toml`, or have PRs reviewed on a specific topic (security, migrations, accessibility, translations…) — also in French: « ajouter un relecteur », « créer un relecteur », « un reviewer qui vérifie X », « brancher ReviewMe sur ce projet » — even without naming ReviewMe if the repository contains `.reviewme/`. Not for reviewing a PR yourself nor for modifying the ReviewMe engine.
---

# Create a ReviewMe reviewer

ReviewMe separates **the method** (in the engine: hunt then verification, output format,
severities, security) from **the knowledge** (in the project's config: what to look for,
where the docs are, when to run). You only touch the knowledge: anything you copied from
the method would drift from the engine at its next version.

Out of scope: reviewing a PR, modifying the engine, setting up CI. If the repository does
not have ReviewMe in CI yet, point it out and refer to the engine's `deploy/` examples.

## 1. Understand the request and the repository

1. **The project**: the config lives under `.reviewme/projects/<project>/` of the reviewed
   repository (or in the repository pointed to by `REVIEWME_CONFIG_HOME`). If absent: use
   the repository's name.
2. **What the reviewer looks for**, in one sentence. Vague request ("a quality reviewer"):
   propose a precise scope rather than guessing.
3. **Where the docs are**: `CONTRIBUTING.md`, `docs/`, `Documentation/`, ADRs, guides, linter
   configs. Note the **paths**: the reviewer reads these files itself, up to date, on every PR.
4. **Its territory**: the globs that trigger it (`src/api/**`, `migrations/**`, `**/*.tsx`).
   A reviewer without `paths` runs, and costs, on every PR.

The engine already reads `CLAUDE.md`, `AGENTS.md` (root and touched folders) and the
`.claude/rules/*.md` files whose `paths:` match, automatically: do not declare them, do not
copy them.

## 2. Pick the lightest path

| The need | Path |
|---|---|
| General-purpose reviewer (bugs, logic, conventions) that must know the docs | **adapt** `tech` |
| Coverage of a Jira ticket's criteria | **adapt** `us` (requires `JIRA_*`) |
| Missing or dubious translations | **adapt** `i18n` |
| Tell the human reviewer what to validate themselves | **enable** `advisor` |
| Project-specific topic (business rule, in-house security, migrations, accessibility…) | **create** a custom one |

Adapt first when possible: the reviewer follows the engine. Both paths can be combined in
the same project.

## 3a. Adapt a core reviewer

```toml
# .reviewme/projects/<project>/project.toml
[reviewers]
core = ["tech", "advisor"]            # enable: add the name; disable: remove it

[reviewers.tech]
read = ["docs/architecture.md"]       # added to what it reads
paths = ["src/**"]                    # replaces its trigger
instructions = "The legacy/ folder is frozen: only report blocking bugs there."
```

- `instructions` states what **is written nowhere**. A rule already in the docs goes through
  `read`, otherwise you create a copy that will drift.
- Only 7 keys are accepted under `[reviewers.<name>]`: see
  [references/reviewer-toml.md](references/reviewer-toml.md).
- If a `reviewers/<name>/` folder exists, it takes precedence and `[reviewers.<name>]` is
  ignored: modify the folder instead.

## 3b. Create a custom reviewer

A `.reviewme/projects/<project>/reviewers/<name>/` folder with two files; its existence is
enough to enable it.

- `reviewer.toml`: `output_mode` (`inline` for remarks on lines, `global` for a single
  comment), `priority`, `[when] paths`, `[context] read`. All the keys:
  [references/reviewer-toml.md](references/reviewer-toml.md).
- `system.md`: half a page, three sections: **what you look for** (precise list),
  **what is intended in this project** (the false positives to avoid), **where the rule is**
  (the `read` files, without copying them).
- Do not put in it what the engine provides: method, JSON format, severity scale,
  confidence, security guidelines, language of the remarks.
- A rule that can be checked mechanically (missing function, forbidden pattern): propose a
  `precheck` (see the reference); the reviewer keeps the judgement.

Complete examples (API, migrations, accessibility): [references/examples.md](references/examples.md).

## 4. Validate

From the clone of the ReviewMe engine:

```sh
REVIEWME_CONFIG_HOME=<absolute path of the repository>/.reviewme uv run reviewme projects
```

The reviewer must appear with its mode, its priority and its `paths`. With a PR and a
`GITHUB_TOKEN`, also check the pipeline without a model or posting:

```sh
REVIEWME_FAKE=1 REVIEWME_CONFIG_HOME=<repo>/.reviewme PROJECT=<project> REPO_PATH=<repo> \
  uv run reviewme review --repo <org>/<repo> --pr <n> --dry-run
```

| Symptom | Cause and remedy |
|---|---|
| `No project configured` | relative or wrong path in `REVIEWME_CONFIG_HOME`: pass an absolute path |
| `unknown key(s)` under `[reviewers.<name>]` | only the 7 keys of the reference are accepted |
| `unknown core reviewer(s)` | the name is not `tech`, `us`, `i18n` or `advisor` |
| reviewer missing from the dry-run, "not triggered" | no file of the PR matches `paths`, or `requires` not satisfied |
| `context.read: path(s) not found` | fix the path; a missing `AGENTS.md` for the core `tech` is expected and harmless |

The config of a `.reviewme/` versioned in the repository is read from the base branch: the
reviewer only applies once merged. To try it before: a manually triggered build with
`REVIEWME_CONFIG_SOURCE=pr`.

## 5. Report back

```markdown
**Reviewer**: <name> — <adapted from core | custom> — <why this path>
**It looks for**: <2-4 points>
**It runs on**: <paths>; **it reads**: <read files>
**Files**: <created / modified>
**Validation**: <output of reviewme projects>; <dry-run done or what was missing>
**To do**: <merge, CI, secrets, if applicable>
```
