"""One-shot entry point: `reviewme review --repo OWNER/REPO --pr N`.

It is the shared building block ("one PR, one review, then exit"), reused by the VPS
(called in a loop/cron), by a webhook, or by a CI step (Bitrise / GitHub Actions).

Additional commands:
  `reviewme watch --repo O/R --every 10m`  poll the open PRs and review each one
  `reviewme init-project <repo>`  creates `config/projects/<repo>/` from the skeleton
  `reviewme projects`             lists projects and their reviewers
  `reviewme enrich --since 14d`   what happened to posted comments (👍/👎, replies, follow-up)
  `reviewme export --since 7d`    analysis archive + readable report (see `export.py`)
  `reviewme doctor`               LLM gateway diagnostics (variables, fingerprints, probes)
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
                    help="Write a standalone HTML report instead of printing the summary")
    st.add_argument("--json", action="store_true", help="Raw JSON output")

    en = sub.add_parser("enrich", help="What happened to posted comments (GitHub read-only)")
    en.add_argument("--since", default="14d", help="Window, e.g. 14d or 36h (default: 14d)")
    en.add_argument("--pr", dest="en_pr", type=int, help="A single PR")
    en.add_argument("--repo", dest="en_repo", help="OWNER/REPO (otherwise GITHUB_REPO)")

    ex = sub.add_parser("export", help="Analysis archive (JSONL) + weekly-report.md")
    ex.add_argument("--since", default="7d", help="Window, e.g. 7d (default: 7d)")
    ex.add_argument("--out", required=True, help="Output folder")
    ex.add_argument("--repo", dest="ex_repo", help="OWNER/REPO, for the report links")
    dr = sub.add_parser("doctor", help="LLM gateway diagnostics: what the CLI would send, "
                        "and whether each credential is accepted (no secret printed)")
    dr.add_argument("--model", default="", help="Model to request in the probe")
    dr.add_argument("--no-probe", action="store_true", help="Only describe the environment")
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

    from .report import ecrire
    from .stats import resume

    if args.html:
        chemin = ecrire(Path(args.html))
        print(f"Report written: {chemin}")
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
    from .enrich import enrich, parse_since
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
        with_text = include_text(resolve_project(config))
    except ProjectConfigError:
        with_text = include_text(None)
    logger = setup_logging()
    gh = GitHubClient(config)
    try:
        r = enrich(gh, ledger_dir(config), parse_since(args.since), logger,
                   with_text=with_text, only_pr=args.en_pr, bot_login=config.bot_login)
    finally:
        gh.close()
    print(f"{r['prs']} PR, {r['comments']} comment(s) tracked.")
    return 0


def _export(args) -> int:
    from .enrich import parse_since
    from .export import export
    from .ledger import ledger_dir
    from .projects import ProjectConfigError, resolve_project
    repo = args.ex_repo or os.environ.get("GITHUB_REPO", "")
    # Report language: the project's if known, otherwise REVIEWME_LANGUAGE.
    try:
        language = resolve_project(load_config(require_repo=False)).language
    except (ProjectConfigError, RuntimeError, ValueError):
        language = os.environ.get("REVIEWME_LANGUAGE", "en")
    archive, rapport = export(ledger_dir(), Path(args.out), parse_since(args.since),
                              repo=repo, since_label=f"({args.since})", language=language)
    print(f"Archive: {archive}")
    print(f"Report: {rapport}")
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
