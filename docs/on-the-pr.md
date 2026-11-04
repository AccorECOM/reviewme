[← ReviewMe](../README.md) · **English** · [Français](fr/on-the-pr.md)

# What lands on the PR

| Finding | Where | Why |
|---|---|---|
| `BLOCKER`, `IMPORTANT` on code introduced by the PR | inline | worth the developer's attention |
| `MINOR` | summary | useful, but not worth a notification per line |
| on code the PR only **moved** | summary, tagged | not a defect of this PR |
| outside the diff lines | summary | GitHub rejects comments outside the diff |
| not confirmed by verification | nowhere (ledger) | a false positive costs the team's trust |
| **💡 Review tip** (`advisor` reviewer): something a human must check themselves (call order changed, file placed outside its usual folder…) | one comment | only when it applies, anchored in files of the PR; 3 at most |
| clean PR | nothing, or one short note with `[review] clean_note = true` | silence beats a "LGTM"; the note only says the review ran |

The language of the posted remarks and of the bot's headings is set per project in
`project.toml`: `[review] language = "fr"` (default `en`).

## Model and score on the comments

Two switches in `project.toml`, both off by default, add a small line under each remark.
They are made for evaluating the bot (which model, which settings, how sure it was), not
for the developer's day-to-day reading.

```toml
[review]
show_model = true   # which model the reviewer ran on
show_score = true   # the score of each finding
```

`show_model` shows **two names**, because they can differ:

- **configured**: the name your setup asked for, the reviewer's `model`, else
  `CLAUDE_MODEL`, else `ANTHROPIC_MODEL` ("CLI default" when none is set). Behind an
  in-house gateway it can be an alias such as `SuperLLM`;
- **answered**: the model the API response reports, read from the CLI envelope
  (`modelUsage`; when the CLI used several, the one that cost the most). This is where
  `SuperLLM` turns out to be, say, `claude-opus-5-5`.

```
score 85/100 · verification 92/100 · model: `SuperLLM` (configured) → `claude-opus-5-5` (answered)
```

The line is on every inline comment, and under the title of each reviewer's section in
the summary. When both names are the same, it is shown once. "Answered" is what the API
declares: a gateway that rewrites the `model` field of its responses can hide the real
one, and the engine has no other way to know.

`show_score` adds the **score** of each finding, inline and in the summary lists:

- **score N/100** is the `confidence` the reviewer gave the finding itself, following
  its persona's scale (`tech`: 91-100 proof seen and failure certain, 76-90 proven but
  impact depends on the callers, 51-75 likely with a part not checked, 50 and below
  unsure). Findings under `CONFIDENCE_THRESHOLD` (default 80) are never posted, so a
  posted score is always at or above it;
- **verification N/100**, when present, is the confidence of the verification pass that
  re-read the finding and confirmed it with a cited line. It is absent when verification
  is off or did not complete.

The score is neither a severity (that is the `[BLOCKER]`/`[IMPORTANT]`/`[MINOR]` label)
nor a measure of quality: it is how sure the model says it is, and a model's
self-assessment is poorly calibrated. Its use is to be compared with what the team did
with the finding: both numbers are the ledger's `confidence` and
`verification.confidence`, so the dashboard's calibration by confidence band tells
whether 90s are really more often right than 80s, and where to set the threshold.

The simple mode (no project) reads `REVIEWME_SHOW_MODEL` and `REVIEWME_SHOW_SCORE`
(`1`/`0`); a project's `project.toml` wins over them.

## Clean PR note

By default a clean PR gets no comment, which cannot be told apart from a bot that did
not run. `[review] clean_note = true` (simple mode: `REVIEWME_CLEAN_NOTE=1`) posts one
short comment instead, when every selected reviewer ran to the end with a readable
answer and none of them has anything to post:

```
✅ Review complete on `abcdef12`: no remarks. Reviewers: `architect`, `tech`.
```

It is a single comment, updated from commit to commit (it takes the place of the
summary comment, and the summary takes it back on a commit with remarks). It is not
counted as a posted remark. A reviewer that crashed or answered off-contract means no
note: the silence was not earned.

The bot only posts comments: never an approval or a change request.
