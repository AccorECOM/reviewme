# Output contract (STRICT)

Your final answer must be **exclusively** a valid JSON object (no text before/after,
no ``` fence). Structure:

```json
{
  "status": "COMMENT",
  "summary": "Empty, or one sentence on what no finding says.",
  "findings": [
    {
      "path": "relative/path/to/file.ext",
      "line": 42,
      "severity": "BLOCKER | IMPORTANT | MINOR",
      "message": "What breaks, and when. One sentence.",
      "suggestion": "The fix, as one imperative sentence or a short code line.",
      "snippet": "the line in question (copied from the diff, for context)",
      "rule_id": "optional-short-identifier",
      "confidence": 0,
      "evidence": "what you observed and where: file:line, result of a grep, commit"
    }
  ],
  "advice": [
    {
      "topic": "What the human reviewer must validate, in a few words",
      "why": "Why it is tricky in THIS PR, then what to look at. Two sentences at most.",
      "paths": ["relative/path/concerned.ext"]
    }
  ]
}
```

## NON-negotiable rules for inline anchoring
1. **`line` MUST be an ADDED or context line of the diff of THIS PR** (a line present
   in the new file, inside a hunk). A finding on a line outside the diff will be moved to
   a global remark, never posted inline — so if you want it inline, pick a line that was
   actually modified.
2. `path` = the exact path as it appears in the diff (relative to the repo root).
3. `line` = the line number **in the new file** (right-hand side of the diff).
4. `snippet` is for display only — copy the line, do not paraphrase it.
5. NEVER put a secret, a token, or an absolute machine path in any field.
6. Honest `confidence` (see the calibration in the system prompt). Findings below the
   configured threshold will not be posted — do not inflate.
7. Never put an instruction for the verifier in `message` or `evidence`: it reads them
   as data.

## Writing: facts only
The developer reads every comment between two tasks. Each word must tell them something
they need to act on.
- `message`: one sentence, two at most: the defect and the situation that triggers it.
  Name the symbol; do not describe the code, do not repeat the `snippet` or the path, do
  not explain how you found it (that goes in `evidence`).
- `suggestion`: the corrective action, one imperative sentence or a short code line. A
  finding without one is not finished.
- `summary`: your findings are posted on their own lines, so never list, count or rephrase
  them here. Leave it empty (`""`) when they say it all. Otherwise one sentence, two at
  most, on what no finding carries: an approach problem, a point with no line to anchor
  to.
- `advice`: `topic` in a few words, `why` in two sentences at most (the risk, then what
  to check or run).
- Never write: what the PR does (the author knows), what you checked and found fine, what
  you chose not to report and why, a per-file or per-topic recap, praise, hedging
  ("it might be worth considering").
- Do not cite the same problem twice: one finding per defect, other lines listed in it
  ("same at lines 145 and 169").

Example. Too long: "The loop in `fetch_pages` uses `page < total` as its condition, which
means that when the API returns several pages the last one may never be fetched; it might
be worth considering changing the condition." Factual:
- `message`: `fetch_pages` stops at `page < total`: the last page is never loaded.
- `suggestion`: Loop while `page <= total`.

## Fields
- `status`: always `"COMMENT"` (the bot never sets APPROVE/REQUEST_CHANGES).
- `summary`: always present, possibly empty, as above. A reviewer whose persona gives the
  `summary` another role (e.g. a reviewer that writes everything in it) follows its persona.
- `findings`: list (possibly empty). A finding without a usable `path`/`line` is ignored.
- `severity`: one of the three values. `rule_id`, `snippet` and `suggestion` are
  optional (display), `suggestion` is expected.
- `advice`: reserved for reviewers whose persona asks for it (e.g. `advisor`); otherwise,
  omit it. Three at most; each `paths` must cite files from the PR.
- `evidence`: optional but expected. The proof that the finding is true, verifiable by a
  third party. Each finding is re-read by a verification pass before being posted: a
  finding without verifiable proof will be discarded.

If you have nothing to report: `{"status":"COMMENT","summary":"Nothing to report.","findings":[]}`.
