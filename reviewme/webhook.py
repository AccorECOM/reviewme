"""Entrée webhook : reçoit les événements `pull_request` de GitHub et déclenche une
review one-shot. Point d'entrée pour le déploiement VPS (au lieu du poll).

SÉCURITÉ (invariants) :
- Signature HMAC `X-Hub-Signature-256` OBLIGATOIRE, vérifiée en temps constant sur le
  corps BRUT. Sans secret configuré (WEBHOOK_SECRET), le serveur REFUSE de démarrer
  (fail-closed).
- L'HMAC authentifie GitHub comme EXPÉDITEUR — il ne rend PAS le contenu de la PR fiable.
  Le contenu reste NON FIABLE (cf. reviewer.py : diff délimité, allowlist verrouillée,
  scrub de sortie).
- ⚠️ DÉPLOIEMENT : exécuter derrière un reverse-proxy TLS et faire tourner le moteur dans
  un SANDBOX sans réseau sortant (hors GitHub) — mitigation n°1 de la prompt-injection.
  Non géré ici : à câbler au niveau infra (conteneur/systemd + firewall).

Prototype : serveur stdlib, mono-worker + thread par review. Pour de la charge, préférer
un vrai serveur ASGI + une file de jobs (v2).
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
    """Vérifie X-Hub-Signature-256 en temps constant sur le corps BRUT."""
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
                return self._reply(413, "payload invalide")
            raw = self.rfile.read(length)

            if not verify_signature(secret, raw, self.headers.get("X-Hub-Signature-256")):
                logger.warning("Webhook : signature invalide, rejet")
                return self._reply(401, "signature invalide")

            if self.headers.get("X-GitHub-Event") != "pull_request":
                return self._reply(204, "événement ignoré")

            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                return self._reply(400, "json invalide")

            action = payload.get("action")
            if action not in _ACTIONS:
                return self._reply(204, f"action ignorée: {action}")

            pr = payload.get("pull_request", {})
            pr_number = pr.get("number")
            repo_full = payload.get("repository", {}).get("full_name", "")
            if not pr_number or repo_full != config.github_repo:
                # on ne review que le repo configuré (le webhook peut recevoir d'autres repos)
                return self._reply(204, "repo/pr hors périmètre")
            if pr.get("draft"):
                return self._reply(204, "PR draft ignorée")

            # review en tâche de fond -> on répond immédiatement à GitHub
            threading.Thread(
                target=run_review_by_number, args=(pr_number, config, logger), daemon=True
            ).start()
            logger.info("Webhook : review PR #%s déclenchée (action=%s)", pr_number, action)
            return self._reply(202, "review déclenchée")

    return WebhookHandler


def main() -> None:
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print(__doc__.strip())
        print("\nUsage : reviewme-webhook   (réglages par .env : WEBHOOK_SECRET obligatoire, "
              "WEBHOOK_HOST, WEBHOOK_PORT)")
        return
    secret = os.environ.get("WEBHOOK_SECRET", "")
    if not secret:
        raise SystemExit("WEBHOOK_SECRET non défini : le webhook refuse de démarrer (fail-closed).")
    config = load_config()
    logger = setup_logging()
    port = int(os.environ.get("WEBHOOK_PORT", "8480"))
    host = os.environ.get("WEBHOOK_HOST", "127.0.0.1")  # défaut local ; exposer via reverse-proxy TLS
    handler = _make_handler(config, secret, logger)
    logger.info("ReviewMe webhook sur http://%s:%s (repo=%s)", host, port, config.github_repo)
    logger.info("⚠️ Déploiement : reverse-proxy TLS + sandbox sans réseau sortant requis.")
    HTTPServer((host, port), handler).serve_forever()


if __name__ == "__main__":
    main()
