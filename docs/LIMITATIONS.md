# Known limitations

What is missing, in order of what it would bring. Nothing here is blocking: the engine
runs in production, these points are known and accepted limitations.

## What needs a decision before any code

**A cap on the size of the analyzed diff.** Today nothing bounds a rewrite PR:
`MAX_BUDGET_USD` cuts per reviewer, but after the fact. The question is not technical — what
should happen to a PR that is too big? Skip it, truncate the diff, or post "too large for
a useful review"?

**`REQUEST_CHANGES` on `BLOCKER`s.** The bot only posts `COMMENT`s. Blocking a merge
on an automated finding requires a dedicated identity (GitHub App) and a measured false
positive rate — otherwise it is pure friction. See also: the bot cannot approve
its own PR, and an approval that counted toward protected branch rules would turn
it into an automatic rubber stamp.

## What has no data yet

**The confidence threshold.** The default value drops a good share of the remarks. The
report shows the share of those that get a reply from a developer: that is the
number that should drive the setting, not a hunch.

**The feedback → rule loop.** `reviewme feedback` proposes attributed rules based on
developers' replies. As long as `common/team-rules.md` is empty, it is only an
intention.

## Known technical limitations

**The statistics history is bounded and transient.** `stats.json` lives in a CI cache,
caps at `MAX_ENTREES = 5000` runs (~1.5 MB) and truncates the oldest ones without
warning. The report also only shows the last 30 PRs and the last 40
runs — so the board cannot blow up, but there is no long-term memory.

Over time, the issue is not size: it is that nobody can answer
"is the bot getting better?". There is no time-based view, and the data that
would make it possible to build one disappears as it goes. A natural next step is
external storage that keeps the full history outside the cache, with a report aggregated
per week rather than per PR. Clearing the CI cache would then only cost
the current counters.


**No hard stop at a PR's cost cap.** Reviewers run in parallel: by the time
the overrun is detected, they have already run. A real stop would require
sequential execution by priority.

**The diff is sent in full on every commit**, not incrementally since the last
review. This is deliberate: a partial diff would have lines judged out of their context.

**No resolve/unresolve of threads.** The bot replies "this point no longer appears" when
a finding disappears, but does not close the thread — that would require the GraphQL API and the
`Contents: R/W` scope, hence broadening the token's permissions.

**No subagents, on purpose.** Verified: a subagent does not inherit its parent's
allowlist. Allowing `Task` would reopen arbitrary code execution, since a
reviewer's configuration can come from the reviewed repository. To vary the model by
task, create one more reviewer with its own `model`.

**The deployment sandbox still has to be wired into the infrastructure** (no outbound network
except GitHub, file system confined to the clone). It is the fundamental mitigation against prompt
injection, and it cannot live in this repository.

## Cost

The stable context — personas, instructions, conventions read from the repository — is billed
again on every review. **Prompt caching** is the only lever that really tackles it, and it is
neutralized as soon as a gateway rejects the caching headers. Check with whoever
operates the gateway before optimizing elsewhere.
