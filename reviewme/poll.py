"""Entrée boucle (mode VPS) : poll GitHub puis `run_review` par PR.

Chaque PR passe par la MÊME fonction one-shot que le CLI et le webhook. Le mode d'entrée
est un simple wrapper autour d'elle. Sur un VPS, un cron/systemd-timer appelant le CLI
one-shot est préférable à cette boucle résidente ; elle reste utile en dev.
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
    mode = "toute PR ouverte" if config.review_all_prs else f"label '{config.review_label}'"
    logger.info("Poll (%s)...", mode)
    try:
        prs = gh.list_open_prs() if config.review_all_prs else gh.list_labeled_prs()
    except Exception as e:
        logger.error("Échec récupération des PR : %s", e)
        return 0

    # On saute les PR déjà reviewées ET celles dont CE commit a échoué (pas de retry infini
    # payant sur un commit qui échoue en permanence ; un nouveau commit sera bien retenté).
    to_review = [pr for pr in prs
                 if not is_reviewed(pr["number"], pr["head"]["sha"])
                 and not failed_at(pr["number"], pr["head"]["sha"])]
    if not to_review:
        logger.info("%d PR trouvée(s), rien de nouveau à reviewer", len(prs))
        return 0

    logger.info("%d PR à reviewer (parallèle)", len(to_review))
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
        logger.info("  Dashboard : http://127.0.0.1:8420")
        server.serve_forever()
    except OSError:
        logger.warning("  Port dashboard 8420 déjà utilisé, ignoré")


def main() -> None:
    # `--help` ne doit jamais démarrer la boucle : sans ça, une frappe d'exploration
    # lance un daemon qui consomme du budget.
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print(__doc__.strip())
        print("\nUsage : reviewme-poll   (réglages par .env : GITHUB_REPO, POLL_INTERVAL, "
              "REVIEW_LABEL, MAX_BUDGET_USD)")
        return

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    config = load_config()
    logger = setup_logging()
    logger.info("ReviewMe (poll) démarre — repo=%s intervalle=%ss budget=$%s",
                config.github_repo, config.poll_interval, config.max_budget_usd)

    threading.Thread(target=_start_dashboard, args=(logger,), daemon=True).start()
    gh = GitHubClient(config)
    try:
        core = gh.check_rate_limit()["core"]
        logger.info("  Rate limit API : %s/%s", core["remaining"], core["limit"])
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
        logger.info("ReviewMe (poll) arrêté")


if __name__ == "__main__":
    main()
