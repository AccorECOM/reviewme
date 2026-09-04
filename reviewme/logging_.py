"""Logs : JSON structuré (fichier) + lisible (console)."""
from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from .config import LOG_FILE, REVIEWS_DIR


def _ensure_dirs() -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    REVIEWS_DIR.mkdir(parents=True, exist_ok=True)


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "msg": record.getMessage(),
        }
        for attr in ("pr_number", "pr_title", "status"):
            if hasattr(record, attr):
                entry[attr] = getattr(record, attr)
        return json.dumps(entry, ensure_ascii=False)


def setup_logging(name: str = "reviewme") -> logging.Logger:
    _ensure_dirs()
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    if logger.handlers:  # idempotent (évite les doublons de handlers)
        return logger

    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(JSONFormatter())
    logger.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s - %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(ch)
    return logger


def save_review(pr_number: int, review_data: dict) -> Path:
    """Sauvegarde une review individuelle en JSON local (data/reviews/)."""
    _ensure_dirs()
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    filepath = REVIEWS_DIR / f"pr_{pr_number}_{ts}.json"
    filepath.write_text(json.dumps(review_data, indent=2, ensure_ascii=False), encoding="utf-8")
    return filepath
