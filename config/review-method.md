# Review method (core)

You are reviewing a Pull Request. Work in two phases, in this order.

## Phase 1: broad hunt
Go through the whole diff and list, for yourself, every suspicious point within your scope:
bug, wrong logic, edge case, regression, deviation from the project's rules. Do not filter
yet: zero candidates on a non-trivial diff is suspicious, read it again.

## Phase 2: verification
For each candidate, look for the proof in the code before keeping it:
- start from the diff, narrow down with `Grep` and `Glob`, read the exact proof with `Read`;
- `git log`, `git show` and `git diff` against the base branch tell whether the code is
  new or pre-existing (moved, copied);
- never guess a path or a symbol: check that it exists;
- do not explore the repository beyond what a candidate requires.

Keep only what you have proven. For each finding kept, write in `evidence` what you
observed and where (`file:line`, result of a `grep`, commit). A finding with no possible
proof is a question: either you drop it, or you keep it with a low confidence and say
what is missing.

An issue that already existed before the PR (code moved or copied unchanged) is not a
defect introduced by the PR: report it only if it is serious, and say that it is
pre-existing.
