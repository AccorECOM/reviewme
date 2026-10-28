"""ReviewMe — AI code review bot: line-by-line inline comments + thread lifecycle.

Modules:
  config.py        configuration (.env)
  models.py        Finding / ReviewResult + robust parsing
  diff_utils.py    patch parser -> valid inline positions (anti-422)
  reviewer.py      `claude -p` invocation -> structured findings (no Write)
  github_client.py GitHub REST client (diff, files, inline review, replies, list)
  reconciler.py    fingerprint dedup + diff validation + prepare()/post_all()
  projects.py      per-PROJECT config + reviewer specs + selection
  precheck.py      a reviewer's deterministic script (facts before the LLM)
  history.py       PR discussion threads, given to the agent before it judges
  stats.py         aggregated counters, no content (portable via a CI cache)
  feedback.py      developer replies -> attributed rules, to be reviewed
  report.py        standalone HTML stats report
  fake.py          simulated mode: the whole pipeline, without calling the LLM
  context/         optional external context providers (jira.py)
  github_auth.py   PAT or GitHub App (JWT RS256 -> renewed installation token)
  run.py           run_review(repo, pr, config): the function shared by all entry points
  state.py         last_reviewed_sha cache (inter-process flock)
  logging_.py      JSON + console logs
  cli.py           one-shot entry point: `reviewme review --repo O/R --pr N`
  poll.py          loop entry point (VPS mode)
  webhook.py       webhook entry point (documented stub — v2)
  web/             dashboard (read-only, hardened)
"""

__version__ = "1.3.0"
