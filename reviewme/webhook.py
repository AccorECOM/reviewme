"""Webhook entry point: receives GitHub `pull_request` events and triggers a one-shot
review. Entry point for VPS deployment (instead of polling).

SECURITY (invariants):
- HMAC signature `X-Hub-Signature-256` is MANDATORY, verified in constant time on the
  RAW body. Without a configured secret (WEBHOOK_SECRET), the server REFUSES to start
  (fail-closed).
- The HMAC authenticates GitHub as the SENDER — it does NOT make the PR content
  trustworthy. Content remains UNTRUSTED (see reviewer.py: delimited diff, locked
  allowlist, output scrub).
- ⚠️ DEPLOYMENT: run behind a TLS reverse proxy and run the engine in a SANDBOX with no
  outbound network (except GitHub) — the #1 prompt-injection mitigation. Not handled
  here: to be wired at the infra level (container/systemd + firewall).

Prototype: stdlib server, single worker + one thread per review. For real load, prefer
a proper ASGI server + a job queue (v2).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from .config import load_config
from .logging_ import setup_logging
from .run import run_review_by_number

_MAX_BODY = 5 * 1024 * 1024  # 5 MB
_ACTIONS = {"opened", "reopened", "synchronize", "ready_for_review"}


def verify_signature(secret: str, raw_body: bytes, header: str | None) -> bool:
    """Verify X-Hub-Signature-256 in constant time on the RAW body."""
    if not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


def _make_handler(config, secret: str, logger: logging.Logger):
    class WebhookHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def _reply(self, code: int, msg: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(msg.encode("utf-8"))

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0 or length > _MAX_BODY:
                return self._reply(413, "invalid payload")
            raw = self.rfile.read(length)

            if not verify_signature(secret, raw, self.headers.get("X-Hub-Signature-256")):
                logger.warning("Webhook: invalid signature, rejected")
                return self._reply(401, "invalid signature")

            if self.headers.get("X-GitHub-Event") != "pull_request":
                return self._reply(204, "event ignored")

            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                return self._reply(400, "invalid json")

            action = payload.get("action")
            if action not in _ACTIONS:
                return self._reply(204, f"action ignored: {action}")

            pr = payload.get("pull_request", {})
            pr_number = pr.get("number")
            repo_full = payload.get("repository", {}).get("full_name", "")
            if not pr_number or repo_full != config.github_repo:
                # only review the configured repo (the webhook may receive other repos)
                return self._reply(204, "repo/pr out of scope")
            if pr.get("draft"):
                return self._reply(204, "draft PR ignored")

            # review in the background -> reply to GitHub immediately
            threading.Thread(
                target=run_review_by_number, args=(pr_number, config, logger), daemon=True
            ).start()
            logger.info("Webhook: review of PR #%s triggered (action=%s)", pr_number, action)
            return self._reply(202, "review triggered")

    return WebhookHandler


def main() -> None:
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print(__doc__.strip())
        print("\nUsage: reviewme-webhook   (settings via .env: WEBHOOK_SECRET required, "
              "WEBHOOK_HOST, WEBHOOK_PORT)")
        return
    secret = os.environ.get("WEBHOOK_SECRET", "")
    if not secret:
        raise SystemExit("WEBHOOK_SECRET not set: the webhook refuses to start (fail-closed).")
    config = load_config()
    logger = setup_logging()
    port = int(os.environ.get("WEBHOOK_PORT", "8480"))
    host = os.environ.get("WEBHOOK_HOST", "127.0.0.1")  # local default; expose via a TLS reverse proxy
    handler = _make_handler(config, secret, logger)
    logger.info("ReviewMe webhook on http://%s:%s (repo=%s)", host, port, config.github_repo)
    logger.info("⚠️ Deployment: TLS reverse proxy + sandbox without outbound network required.")
    HTTPServer((host, port), handler).serve_forever()


if __name__ == "__main__":
    main()
