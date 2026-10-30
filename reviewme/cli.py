"""One-shot entry point: `reviewme review --repo OWNER/REPO --pr N`.

It is the shared building block ("one PR, one review, then exit"), reused by the VPS
(called in a loop/cron), by a webhook, or by a CI step (Bitrise / GitHub Actions).

Additional commands:
  `reviewme watch --repo O/R --every 10m`  poll the open PRs and review each one
  `reviewme init-project <repo>`  creates `config/projects/<repo>/` from the skeleton
  `reviewme projects`             lists projects and their reviewers
  `reviewme enrich --since 14d`   what happened to posted comments (👍/👎, replies, follow-up)
  `reviewme triage --since 14d`   optional LLM sort of contested findings and misses (`triage.py`)
  `reviewme export --since 7d`    analysis archive + readable report (see `export.py`)
  `reviewme history --since 2024-01-01`  the team's merged/closed PRs over years (`pr_history.py`)
  `reviewme stats --html F`       standalone dashboard built from the ledger (`dashboard/`)
  `reviewme doctor`               LLM gateway diagnostics (variables, fingerprints, probes)
  `reviewme demo-data --out DIR`  development aid: synthetic ledger + dashboard (`demo.py`)
"""
from __future__ import annotations

import argparse
import dataclasses
import os
import shutil
import sys
from pathlib import Path

from .config import load_config
from .logging_ import setup_logging
from .projects import PROJECTS_DIR as PROJECTS_DIR_DEFAULT
from .projects import TEMPLATES_DIR, ProjectConfigError, load_project, projects_dir
from .run import run_review_by_number
from .watch import DEFAULT_SKIP_LABEL


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="reviewme", description="One-shot review of a GitHub PR")
    sub = parser.add_subparsers(dest="command")

    rv = sub.add_parser("review", help="Review a specific PR")
    for p in (parser, rv):  # accepts `reviewme --repo .. --pr ..` and `reviewme review --repo .. --pr ..`
        p.add_argument("--repo", help="OWNER/REPO (otherwise GITHUB_REPO from .env)")
        p.add_argument("--pr", type=int, help="PR number")
        p.add_argument("--force", action="store_true", help="Re-review even if already done for this commit")
        p.add_argument("--dry-run", action="store_true", help="Post nothing, log only")

    wa = sub.add_parser("watch", help="Poll the open PRs and review each one (local loop)",
                        description="Every N minutes, list the repo's open PRs and review "
                        "the commits not reviewed yet, each in its own git worktree. Meant "
                        "for a machine where the Claude Code CLI is logged in.")
    wa.add_argument("--repo", dest="wa_repo", metavar="OWNER/REPO", help="OWNER/REPO (otherwise GITHUB_REPO)")
    wa.add_argument("--repo-path", metavar="PATH", help="Local clone of the repo (otherwise REPO_PATH)")
    wa.add_argument("--every", help="Interval: 600, 90s, 10m, 1h (default: POLL_INTERVAL "
                    "or 10m, minimum 1m)")
    wa.add_argument("--label", default="",
                    help="Only review PRs carrying this label (default: every open PR)")
    wa.add_argument("--skip-label",
                    default=os.environ.get("REVIEWME_SKIP_LABEL", DEFAULT_SKIP_LABEL),
                    help=f"Never review PRs carrying this label (default: {DEFAULT_SKIP_LABEL}"
                    ", REVIEWME_SKIP_LABEL; empty to disable)")
    wa.add_argument("--include-drafts", action="store_true", help="Also review draft PRs")
    wa.add_argument("--include-forks", action="store_true",
                    help="Also review PRs opened from forks (untrusted authors)")
    wa.add_argument("--parallel", type=int, default=1,
                    help="PRs reviewed at the same time (default: 1)")
    wa.add_argument("--once", action="store_true", help="One round, then exit (cron, tests)")
    wa.add_argument("--dashboard", action="store_true",
                    help="Serve the read-only dashboard on 127.0.0.1:8420")
    wa.add_argument("--dry-run", dest="wa_dry_run", action="store_true",
                    help="Post nothing, log only")

    ip = sub.add_parser("init-project", help="Create a project folder from the skeleton")
    ip.add_argument("name", help="Folder name = REPO name (e.g. my-app), never the team's")
    ip.add_argument("--reviewers", default="tech",
                    help="Reviewers to copy, comma-separated (default: tech)")

    sub.add_parser("projects", help="List configured projects and their reviewers")

    fb = sub.add_parser("feedback",
                        help="Developer feedback on a PR, as rules to review")
    fb.add_argument("--pr", type=int, required=True, help="PR number")
    fb.add_argument("--repo", dest="fb_repo", help="OWNER/REPO (otherwise GITHUB_REPO)")

    st = sub.add_parser("stats", help="Aggregated statistics (counters, no content)")
    st.add_argument("--html", metavar="FILE",
                    help="Write the HTML dashboard (ledger, `[stats]` of project.toml) "
                    "instead of printing the summary")
    st.add_argument("--json", action="store_true", help="Raw JSON output")

    en = sub.add_parser("enrich", help="What happened to posted comments (GitHub read-only)")
    en.add_argument("--since", default="14d", help="Window, e.g. 14d or 36h (default: 14d)")
    en.add_argument("--pr", dest="en_pr", type=int, help="A single PR")
    en.add_argument("--repo", dest="en_repo", help="OWNER/REPO (otherwise GITHUB_REPO)")

    hi = sub.add_parser("history", help="Scan the repository's merged and closed PRs since a "
                        "date into the ledger (history.jsonl, GitHub read-only)",
                        description="One-off, re-runnable scan of the PR workflow before "
                        "and after the bot: one GraphQL query per 100 PRs. The most recent "
                        "row per PR wins, so running it again refreshes the rows.")
    hi.add_argument("--since", required=True,
                    help="First PR creation date, YYYY-MM-DD (or a window such as 730d)")
    hi.add_argument("--repo", dest="hi_repo", help="OWNER/REPO (otherwise GITHUB_REPO)")
    hi.add_argument("--jira", action="store_true",
                    help="Also link each PR to its Jira ticket (from the branch name) and "
                    "count bug tickets per month (JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN)")

    tr = sub.add_parser("triage", help="Optional LLM classification of developer replies "
                        "and human comments (ledger only, needs [ledger] include_text)",
                        description="Reads the reply texts already in the ledger and gives "
                        "each contested finding one cause (false positive, missing context, "
                        "pre-existing, ...), then groups the human comments the bot did not "
                        "anticipate into recurring patterns. One tool-less model call per "
                        "batch; writes ledger/triage/ only, never GitHub or the config.")
    tr.add_argument("--since", default="14d", help="Window, e.g. 14d or 36h (default: 14d)")
    tr.add_argument("--repo", dest="tr_repo", default="",
                    help="Only this OWNER/REPO's rows (default: every repo in the ledger)")
    tr.add_argument("--dry-run", dest="tr_dry_run", action="store_true",
                    help="Show what would be sent (counts, calls, size); call nothing, "
                    "write nothing")
    tr.add_argument("--max-budget-usd", dest="tr_budget", type=float, default=None,
                    help="Budget of the whole pass (default: [stats.triage] max_budget_usd "
                    "or 0.50)")

    ex = sub.add_parser("export", help="Analysis archive (JSONL) + weekly-report.md")
    ex.add_argument("--since", default="7d", help="Window, e.g. 7d (default: 7d)")
    ex.add_argument("--out", required=True, help="Output folder")
    ex.add_argument("--repo", dest="ex_repo", help="OWNER/REPO, for the report links")
    dr = sub.add_parser("doctor", help="LLM gateway diagnostics: what the CLI would send, "
                        "and whether each credential is accepted (no secret printed)")
    dr.add_argument("--model", default="", help="Model to request in the probe")
    dr.add_argument("--no-probe", action="store_true", help="Only describe the environment")
    # Development aid, kept out of the help list: synthetic data must never be mistaken
    # for a team's real numbers, so nothing advertises it next to the real commands.
    dd = sub.add_parser("demo-data")
    dd.add_argument("--out", required=True, help="Output folder (ledger + dashboard)")
    dd.add_argument("--seed", type=int, default=1, help="Random seed (default 1)")
    dd.add_argument("--days", type=int, default=90, help="Window in days (default 90)")
    dd.add_argument("--language", default="en", choices=("en", "fr"))
    dd.add_argument("--now", default="", help="Generation date YYYY-MM-DD (default: now)")
    return parser.parse_args(argv)


def _init_project(name: str, reviewer_ids: list[str]) -> int:
    """Copies the skeleton. Copy at creation time, no inheritance at runtime."""
    root = projects_dir()
    target = root / name
    if target.exists():
        print(f"Error: {target} already exists.", file=sys.stderr)
        return 1
    if not TEMPLATES_DIR.is_dir():
        print(f"Error: skeleton not found ({TEMPLATES_DIR}).", file=sys.stderr)
        return 1

    missing = [r for r in reviewer_ids if not (TEMPLATES_DIR / "reviewers" / r).is_dir()]
    if missing:
        available = sorted(p.name for p in (TEMPLATES_DIR / "reviewers").iterdir() if p.is_dir())
        print(f"Error: unknown reviewer(s) {missing}. Available: {', '.join(available)}",
              file=sys.stderr)
        return 1

    (target / "reviewers").mkdir(parents=True)
    if (TEMPLATES_DIR / "common").is_dir():
        shutil.copytree(TEMPLATES_DIR / "common", target / "common")
    else:
        (target / "common").mkdir()
    for rid in reviewer_ids:
        shutil.copytree(TEMPLATES_DIR / "reviewers" / rid, target / "reviewers" / rid)

    print(f"Project created: {target}")
    if root != PROJECTS_DIR_DEFAULT:
        print(f"  (external config repo: {root.parent})")
    print(f"  reviewers: {', '.join(reviewer_ids)}")
    print("  Next steps:")
    print(f"    1. adjust the shared guidelines in {target / 'common'}/*.md")
    print(f"    2. adapt the personas in {target / 'reviewers'}/<id>/system.md")
    print("       (code conventions: the agent reads them in the reviewed repo — do not copy them)")
    print(f"    3. enable the project: PROJECT={name} in the .env")
    print("  (add a reviewer later = create its folder in reviewers/; disable")
    print("   it = `enabled = false` in its reviewer.toml)")
    return 0


def _list_projects() -> int:
    root = projects_dir()
    if not root.is_dir():
        print(f"No project configured ({root} does not exist).")
        print("Create the first one: reviewme init-project <repo> --reviewers tech,us")
        return 0
    names = sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
    if not names:
        print("No project configured.")
        return 0
    for name in names:
        try:
            project = load_project(name, root)
        except ProjectConfigError as e:
            print(f"{name}: ⚠️  {e}")
            continue
        for spec in sorted(project.reviewers, key=lambda r: (r.priority, r.id)):
            when = f", when {list(spec.when_paths)}" if spec.when_paths else ""
            needs = f", requires {list(spec.requires)}" if spec.requires else ""
            print(f"{name} · {spec.id} ({spec.output_mode}, priority {spec.priority}{when}{needs})")
    return 0


def _watch(args) -> int:
    from .watch import WatchOptions, default_interval, parse_interval, watch

    if args.repo_path:
        # Before `load_config`: it tells a config in the reviewed repo by REPO_PATH.
        os.environ["REPO_PATH"] = args.repo_path
    config = load_config(require_repo=False)
    overrides: dict = {"dry_run": args.wa_dry_run or config.dry_run}
    if args.wa_repo:
        overrides["github_repo"] = args.wa_repo
    if args.repo_path:
        overrides["repo_path"] = args.repo_path
    config = dataclasses.replace(config, **overrides)
    if not config.github_repo:
        print("Error: --repo required (or GITHUB_REPO in .env)", file=sys.stderr)
        return 2
    try:
        interval = parse_interval(args.every) if args.every else default_interval()
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    if args.parallel < 1:
        print("Error: --parallel must be at least 1", file=sys.stderr)
        return 2
    options = WatchOptions(interval_s=interval, label=args.label.strip(),
                           skip_label=args.skip_label.strip(),
                           include_drafts=args.include_drafts,
                           include_forks=args.include_forks, parallel=args.parallel,
                           once=args.once, dashboard=args.dashboard)
    return watch(config, options, setup_logging())


def _feedback(args) -> int:
    import dataclasses as _dc

    from .feedback import collecter, en_regles
    from .github_client import GitHubClient
    from .logging_ import setup_logging

    config = load_config(require_repo=False)
    if args.fb_repo:
        config = _dc.replace(config, github_repo=args.fb_repo)
    if not config.github_repo:
        print("Error: --repo required (or GITHUB_REPO in .env)", file=sys.stderr)
        return 2

    logger = setup_logging()
    gh = GitHubClient(config)
    try:
        echanges = collecter(gh, args.pr, logger)
    finally:
        gh.close()

    if not echanges:
        print(f"PR #{args.pr}: no comment received a reply.")
        return 0

    print(f"PR #{args.pr}: {len(echanges)} developer reply(ies).\n")
    print(en_regles(echanges, args.pr))
    print("\n---\nReview, then paste into the project's `common/team-rules.md` if the "
          "rule applies beyond this PR.")
    return 0


def _stats(args) -> int:
    import json

    from .stats import resume

    if args.html:
        chemin = _write_dashboard(Path(args.html))
        print(f"Dashboard written: {chemin}")
        return 0

    r = resume()
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0

    if not r.get("runs"):
        print("No review recorded.")
        _print_quality()
        return 0
    print(f"{r['runs']} run(s) on {r['prs']} PR")
    print(f"  comments posted        : {r['remarques_postees']}")
    print(f"  avoided (dedup+thresh.): {r['remarques_dedupliquees'] + r['remarques_sous_seuil']}")
    print(f"  total cost             : {r['cout_total_usd']:.2f} $")
    print(f"  average cost per PR    : {r['cout_moyen_par_pr']:.2f} $")
    _print_quality()
    return 0


def _write_dashboard(dest: Path) -> Path:
    from .dashboard import write
    from .dashboard.settings import active_settings
    from .ledger import ledger_dir
    from .stats import STATS_FILE

    settings, language = active_settings()
    return write(dest, ledger_dir(), settings, language, STATS_FILE)


def _print_quality() -> None:
    """Quality block, separate from operational counters. Nothing if the ledger is empty."""
    from .export import load, quality_of
    from .ledger import ledger_dir

    q = quality_of(load(ledger_dir()))
    if not q["posted_judged"] and not any(c["findings"] for c in q["calibration"].values()):
        return
    print("\nQuality (enriched ledger):")
    for rid, t in q["by_reviewer"].items():
        taux = "—" if t["followed_rate"] is None else f"{round(t['followed_rate'] * 100)} %"
        print(f"  {rid:<12} posted {t['posted']:>3}  acted upon {taux:>5}  "
              f"approved {t['approved']:>3}  contested {t['contested']:>3}")
    print("  calibration (findings / posted / acted upon):")
    for b, c in q["calibration"].items():
        print(f"    {b:>7}: {c['findings']:>4} / {c['fates'].get('posted', 0):>4} / "
              f"{c['followed']:>4}")
    m = q["misses"]
    print(f"  approximate misses: {m['on_silent_lines']} silent line(s), "
          f"{m['on_dropped_findings']} on dropped finding")


def _enrich(args) -> int:
    from .enrich import control_group, enrich, parse_since
    from .github_client import GitHubClient
    from .ledger import include_text, ledger_dir
    from .projects import ProjectConfigError, resolve_project

    config = load_config(require_repo=False)
    if args.en_repo:
        config = dataclasses.replace(config, github_repo=args.en_repo)
    if not config.github_repo:
        print("Error: --repo required (or GITHUB_REPO in .env)", file=sys.stderr)
        return 2
    try:
        project = resolve_project(config)
    except ProjectConfigError:
        project = None
    with_text, control = include_text(project), control_group(project)
    logger = setup_logging()
    gh = GitHubClient(config)
    try:
        r = enrich(gh, ledger_dir(config), parse_since(args.since), logger,
                   with_text=with_text, only_pr=args.en_pr, bot_login=config.bot_login,
                   control_group=control)
    finally:
        gh.close()
    print(f"{r['prs']} PR, {r['comments']} comment(s) tracked, "
          f"{r['unreviewed']} unreviewed PR(s) recorded.")
    return 0


def _history(args) -> int:
    from .github_client import GitHubClient
    from .ledger import ledger_dir
    from .pr_history import HistoryScanError, parse_date, scan

    config = load_config(require_repo=False)
    if args.hi_repo:
        config = dataclasses.replace(config, github_repo=args.hi_repo)
    if not config.github_repo:
        print("Error: --repo required (or GITHUB_REPO in .env)", file=sys.stderr)
        return 2
    try:
        since = parse_date(args.since)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    jira = None
    if args.jira:
        from .context.jira_stats import JiraClient
        jira = JiraClient.from_env()
        if jira is None:
            print("Error: --jira needs JIRA_BASE_URL, JIRA_EMAIL and JIRA_API_TOKEN "
                  "(JIRA_KEY_PREFIX and JIRA_BUG_TYPES optional). Run without --jira to "
                  "scan GitHub only.", file=sys.stderr)
            return 2
    logger = setup_logging()
    gh = GitHubClient(config)
    try:
        r = scan(gh, ledger_dir(config), since, logger, repo=config.github_repo,
                 bot_login=config.bot_login, review_label=config.review_label, jira=jira)
    except HistoryScanError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    finally:
        gh.close()
        if jira is not None:
            jira.close()
    print(f"{r['prs']} PR(s) since {since.date()} ({r['merged']} merged, "
          f"{r['bot_reviewed']} reviewed by the bot), {r['pages']} GraphQL page(s).")
    if "jira" in r:
        j = r["jira"]
        print(f"Jira: {j['linked_prs']} PR(s) linked to {j['found']} ticket(s) "
              f"({j['bug_prs']} on a bug), {r['bug_months']} month(s) of bug counts, "
              f"{r['jira_calls']} call(s).")
    if r.get("message"):
        print(f"Stopped early: {r['message']}", file=sys.stderr)
        return 1
    return 0


def _demo_data(args) -> int:
    from datetime import UTC, datetime

    from .demo import build_demo
    try:
        now = datetime.fromisoformat(args.now).replace(tzinfo=UTC) if args.now else None
    except ValueError:
        print("Error: --now expects YYYY-MM-DD", file=sys.stderr)
        return 2
    if args.days < 14:
        print("Error: --days must be at least 14", file=sys.stderr)
        return 2
    r = build_demo(Path(args.out), seed=args.seed, days=args.days, language=args.language,
                   now=now)
    print(f"Synthetic ledger: {r['prs_reviewed']} reviewed and {r['prs_unreviewed']} "
          f"unreviewed PR(s), {r['runs']} run(s), {r['findings']} finding(s), "
          f"{r['history']} history row(s). Dashboard: {r['page']}")
    return 0


def _triage(args) -> int:
    from .enrich import parse_since
    from .ledger import ledger_dir
    from .projects import resolve_project
    from .triage import triage

    try:
        config = load_config(require_repo=False)
    except RuntimeError:
        # No GitHub credentials: fine, the triage reads the ledger only. A placeholder lets
        # `load_config` read the rest of the environment the usual way, and is removed
        # before anything runs so it never reaches the CLI subprocess.
        os.environ["GITHUB_TOKEN"] = "unused-by-triage"
        try:
            config = dataclasses.replace(load_config(require_repo=False), github_token="")
        finally:
            os.environ.pop("GITHUB_TOKEN", None)
    try:
        project = resolve_project(config)
    except ProjectConfigError as e:
        # Unlike `enrich`, this command is driven by `[stats.triage]`: a broken table must
        # not silently run with another model or budget.
        print(f"Error: {e}", file=sys.stderr)
        return 2
    if not project.stats_triage_enabled:
        print("triage disabled by config ([stats.triage] enabled = false).")
        return 0
    try:
        since = parse_since(args.since)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    budget = project.stats_triage_max_budget_usd if args.tr_budget is None else args.tr_budget
    if budget <= 0:
        print("Error: --max-budget-usd must be > 0", file=sys.stderr)
        return 2
    model = project.stats_triage_model or os.environ.get("CLAUDE_MODEL", "")
    wrong = dict(project.stats_judgment).get("wrong", ("-1",))
    r = triage(ledger_dir(config), since, config, language=project.language, model=model,
               budget=budget, repo=args.tr_repo.strip(), dry_run=args.tr_dry_run,
               project=project.name, wrong_reactions=tuple(wrong), logger=setup_logging())
    status = r["status"]
    if status == "nothing":
        print(f"nothing to triage: no contested finding and no human comment in {args.since}.")
        return 0
    if status == "no_text":
        print(f"nothing to triage: enable [ledger] include_text ({r['without_text']} "
              f"contested finding(s) or PR(s) without text in {args.since}; the texts are "
              f"collected by the next `reviewme enrich`).")
        return 0
    if status == "dry_run":
        print(f"dry run: {r['threads']} contested finding(s) and {r['misses']} human "
              f"comment(s) in {r['batches']} call(s), {r['prompt_chars']} characters "
              f"(~{r['prompt_chars'] // 4} input tokens), budget {budget:.2f} $. Nothing "
              f"sent, nothing written.")
        return 0
    if status in ("parse_error", "error"):
        print(f"triage failed ({status}) after {r['calls']} call(s), {r['cost_usd']:.4f} $: "
              f"nothing written. REVIEWME_DEBUG=1 shows the end of the answer.",
              file=sys.stderr)
        return 1
    causes = ", ".join(f"{k} {n}" for k, n in r["causes"].items()) or "none"
    print(f"{r['classified']} finding(s) classified"
          + (f" ({r['unclassified']} without an answer)" if r["unclassified"] else "")
          + f", {r['patterns']} recurring miss pattern(s) from {r['misses']} comment(s), "
          f"{r['calls']} call(s), {r['cost_usd']:.4f} $ ({r['model']}).")
    print(f"  causes: {causes}")
    if status == "partial":
        print(f"  budget of {budget:.2f} $ reached: part of the window is not classified.")
    print(f"  written: {r['path']}")
    return 0


def _export(args) -> int:
    from .dashboard.settings import active_settings
    from .enrich import parse_since
    from .export import DASHBOARD_FILE, export
    from .ledger import ledger_dir
    repo = args.ex_repo or os.environ.get("GITHUB_REPO", "")
    # Report language and dashboard settings: the project's if known, else the defaults.
    settings, language = active_settings()
    out = Path(args.out)
    archive, rapport = export(ledger_dir(), out, parse_since(args.since),
                              repo=repo, since_label=f"({args.since})", language=language,
                              settings=settings)
    print(f"Archive: {archive}")
    print(f"Report: {rapport}")
    print(f"Dashboard: {out / DASHBOARD_FILE}")
    return 0


def _doctor(args: argparse.Namespace) -> int:
    from .config import load_config
    from .llm_diag import describe_env, probe
    try:
        load_config(require_repo=False)   # loads .env the same way the review does
    except Exception as e:
        print(f"(config not loaded: {e})")
    print("=" * 72)
    print("ReviewMe doctor: LLM gateway check (no secret printed)")
    print("=" * 72)
    for line in describe_env():
        print(f"  {line}")
    if not args.no_probe:
        for line in probe(model=args.model):
            print(line)
    print("=" * 72)
    return 0


def main() -> None:
    args = _parse_args(sys.argv[1:])

    if args.command == "doctor":
        raise SystemExit(_doctor(args))

    if args.command == "watch":
        raise SystemExit(_watch(args))
    if args.command == "init-project":
        raise SystemExit(_init_project(args.name, [r.strip() for r in args.reviewers.split(",") if r.strip()]))
    if args.command == "projects":
        raise SystemExit(_list_projects())
    if args.command == "stats":
        raise SystemExit(_stats(args))
    if args.command == "feedback":
        raise SystemExit(_feedback(args))
    if args.command == "enrich":
        raise SystemExit(_enrich(args))
    if args.command == "export":
        raise SystemExit(_export(args))
    if args.command == "triage":
        raise SystemExit(_triage(args))
    if args.command == "history":
        raise SystemExit(_history(args))
    if args.command == "demo-data":
        raise SystemExit(_demo_data(args))

    if args.pr is None:
        print("Usage: reviewme review --repo OWNER/REPO --pr N [--dry-run] [--force]", file=sys.stderr)
        print("       reviewme init-project <repo> [--reviewers tech,us,i18n]", file=sys.stderr)
        print("       reviewme projects", file=sys.stderr)
        raise SystemExit(2)

    config = load_config(require_repo=False)
    overrides: dict = {"dry_run": args.dry_run or config.dry_run}
    if args.repo:
        overrides["github_repo"] = args.repo
    if not (args.repo or config.github_repo):
        print("Error: --repo required (or GITHUB_REPO in .env)", file=sys.stderr)
        raise SystemExit(2)
    config = dataclasses.replace(config, **overrides)

    logger = setup_logging()
    logger.info("ReviewMe one-shot: %s PR #%s%s",
                config.github_repo, args.pr, " [DRY-RUN]" if config.dry_run else "")
    result = run_review_by_number(args.pr, config, logger, force=args.force)
    if result.get("error"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
