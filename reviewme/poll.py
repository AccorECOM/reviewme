"""Loop entry point (VPS mode): poll GitHub then `run_review` per PR.

Each PR goes through the SAME one-shot function as the CLI and the webhook. The entry
mode is just a wrapper around it. On a VPS, a cron/systemd timer calling the one-shot
CLI is preferable to this resident loop; it remains useful in dev.
"""
from __future__ import annotations

import logging
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import load_config
from .github_client import GitHubClient
from .logging_ import setup_logging
from .run import run_review
from .state import failed_at, is_reviewed

_shutdown = False


def _handle_signal(sig: int, frame) -> None:
    global _shutdown
    _shutdown = True


def poll_once(gh: GitHubClient, config, logger: logging.Logger) -> int:
    mode = "any open PR" if config.review_all_prs else f"label '{config.review_label}'"
    logger.info("Poll (%s)...", mode)
    try:
        prs = gh.list_open_prs() if config.review_all_prs else gh.list_labeled_prs()
    except Exception as e:
        logger.error("Failed to fetch PRs: %s", e)
        return 0

    # Skip PRs already reviewed AND those whose CURRENT commit failed (no infinite paid
    # retry on a commit that fails permanently; a new commit will be retried).
    to_review = [pr for pr in prs
                 if not is_reviewed(pr["number"], pr["head"]["sha"])
                 and not failed_at(pr["number"], pr["head"]["sha"])]
    if not to_review:
        logger.info("%d PR(s) found, nothing new to review", len(prs))
        return 0

    logger.info("%d PR(s) to review (parallel)", len(to_review))
    with ThreadPoolExecutor(max_workers=min(len(to_review), 4)) as pool:
        futures = {pool.submit(run_review, pr, config, gh, logger): pr["number"] for pr in to_review}
        processed = 0
        for fut in as_completed(futures):
            if _shutdown:
                break
            fut.result()
            processed += 1
    return processed


def _start_dashboard(logger: logging.Logger) -> None:
    from http.server import HTTPServer

    from .web.server import DashboardHandler
    try:
        server = HTTPServer(("127.0.0.1", 8420), DashboardHandler)
        server.daemon_threads = True
        logger.info("  Dashboard: http://127.0.0.1:8420")
        server.serve_forever()
    except OSError:
        logger.warning("  Dashboard port 8420 already in use, skipped")


def main() -> None:
    # `--help` must never start the loop: otherwise an exploratory keystroke launches a
    # daemon that burns budget.
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print(__doc__.strip())
        print("\nUsage: reviewme-poll   (settings via .env: GITHUB_REPO, POLL_INTERVAL, "
              "REVIEW_LABEL, MAX_BUDGET_USD)")
        return

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    config = load_config()
    logger = setup_logging()
    logger.info("ReviewMe (poll) starting — repo=%s interval=%ss budget=$%s",
                config.github_repo, config.poll_interval, config.max_budget_usd)

    threading.Thread(target=_start_dashboard, args=(logger,), daemon=True).start()
    gh = GitHubClient(config)
    try:
        core = gh.check_rate_limit()["core"]
        logger.info("  API rate limit: %s/%s", core["remaining"], core["limit"])
        while not _shutdown:
            poll_once(gh, config, logger)
            if _shutdown:
                break
            for _ in range(config.poll_interval):
                if _shutdown:
                    break
                time.sleep(1)
    finally:
        gh.close()
        logger.info("ReviewMe (poll) stopped")


if __name__ == "__main__":
    main()
