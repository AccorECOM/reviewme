"""`reviewme-poll`: legacy name of `reviewme watch`, kept so existing setups keep working.

Same loop (see `watch.py`), configured the old way, from the environment only:
`REVIEW_ALL_PRS=1` reviews every open PR, otherwise only those labelled `REVIEW_LABEL`
(default `review-me`); `POLL_INTERVAL` in seconds; the dashboard on 127.0.0.1:8420 starts
with it. New setups: `reviewme watch --help`.
"""
from __future__ import annotations

import sys

from .config import load_config
from .logging_ import setup_logging
from .watch import WatchOptions, default_interval, watch


def main() -> None:
    # `--help` must never start the loop: otherwise an exploratory keystroke launches a
    # daemon that burns budget.
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print(__doc__.strip())
        return
    config = load_config()
    options = WatchOptions(
        interval_s=default_interval(),
        label="" if config.review_all_prs else config.review_label,
        dashboard=True,
    )
    raise SystemExit(watch(config, options, setup_logging()))


if __name__ == "__main__":
    main()
