"""Failure alert: one message to a chat webhook when a review did not happen.

A failed review leaves nothing on the PR: an error message must never be posted as a
comment (see `reconciler.prepare`). Without an alert the only trace is the CI log, and a
silent PR reads as "reviewed, nothing found".

`REVIEWME_ALERT_WEBHOOK` is the URL of a Slack incoming webhook, or of any endpoint that
accepts the same call: a `POST` of `{"text": "..."}` as JSON (Mattermost, an n8n webhook
node...). It is a job setting, like every credential: whoever holds the URL can post in
the channel, so it is never read from an instance's `.env` and never logged (only its
host is).

What is sent: the repo, the PR number and link, the commit, the reviewers that failed and
a short scrubbed excerpt of each error. No PR title, no diff, no finding. An
authentication error is sent without its text: a gateway prints fragments of the key it
received.

Best effort: a failing alert is logged and never changes the outcome of the review.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
from collections.abc import Mapping
from urllib.parse import urlparse

from .llm_diag import looks_like_auth_error
from .scrub import scrub_text

ENV_VAR = "REVIEWME_ALERT_WEBHOOK"
_TIMEOUT_S = 10.0
_EXCERPT_CHARS = 300
_AUTH_WITHHELD = ("authentication error (text withheld: it can carry fragments of a "
                  "credential; the build log has the diagnostics)")

# Alerts already sent by THIS process. A loop (`reviewme watch`) meets the same failure
# again on every round as long as the commit is not marked as failed (invalid project
# config, usage limit): one message per failure, not one per round. A one-shot run (CI)
# starts empty, so a rebuild that fails again alerts again.
_sent: set[str] = set()
_sent_lock = threading.Lock()


def webhook_host(url: str) -> str:
    """The host alone: enough to tell where alerts go, without the secret path."""
    return urlparse(url).netloc.rsplit("@", 1)[-1] or "?"


def _escape(text: str) -> str:
    """Slack reads `<...>` as a link or a mention (`<!channel>`): an error text is not
    trusted to stay clear of them (a branch name ends up in a config error)."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _excerpt(text: str) -> str:
    if looks_like_auth_error(text):
        return _AUTH_WITHHELD
    flat = " ".join(scrub_text(text).split())
    if len(flat) > _EXCERPT_CHARS:
        flat = flat[:_EXCERPT_CHARS].rstrip() + "…"
    return _escape(flat) or "no error text"


def build_url(env: Mapping[str, str] | None = None) -> str:
    """Link to the CI build that ran the review, when the CI says where it is."""
    env = os.environ if env is None else env
    explicit = (env.get("REVIEWME_BUILD_URL") or env.get("BITRISE_BUILD_URL") or "").strip()
    if explicit:
        return explicit
    server, repo, run = (env.get(k, "").strip() for k in
                         ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    return f"{server}/{repo}/actions/runs/{run}" if server and repo and run else ""


def failure_text(pr: dict, config, result: dict,
                 env: Mapping[str, str] | None = None) -> str | None:
    """The message for this run's outcome, or None when there is nothing to report.

    `result` is what `run.run_review` returns: `error` when no review happened,
    `failures` (one `{reviewer, error}` per reviewer that delivered nothing) when part of
    it did, `usage_limit` when the quota stopped it.
    """
    error = result.get("error")
    failures = result.get("failures") or []
    if not error and not failures:
        return None

    repo = getattr(config, "github_repo", "") or "?"
    number = pr.get("number")
    sha = ((pr.get("head") or {}).get("sha") or "")[:8]
    where = f"{repo} PR #{number}" + (f" (commit {sha})" if sha else "")
    if getattr(config, "dry_run", False):
        where += " [dry-run]"
    names = _escape(", ".join(f["reviewer"] for f in failures))

    if result.get("usage_limit"):
        head = f"ReviewMe: usage limit reached, {where} was not reviewed"
        outcome = "Nothing was posted on the PR. Run the review again after the reset."
    elif error:
        head = f"ReviewMe: the review of {where} failed"
        outcome = "Nothing was posted on the PR."
    else:
        head = f"ReviewMe: {len(failures)} reviewer(s) failed on {where}: {names}"
        outcome = "The other reviewers ran to the end; these ones posted nothing."

    lines = [head, pr.get("html_url") or f"https://github.com/{repo}/pull/{number}"]
    if failures:
        lines += [f"- {_escape(f['reviewer'])}: {_excerpt(f.get('error') or '')}"
                  for f in failures]
    else:
        lines.append(f"- {_excerpt(str(error))}")
    lines.append(outcome)
    build = build_url(env)
    if build:
        lines.append(f"Build: {build}")
    return "\n".join(lines)


def send(url: str, text: str) -> tuple[bool, str]:
    """Posts `{"text": text}` to the webhook. Returns (sent, detail); never raises, and
    the detail never contains the URL."""
    import httpx
    if urlparse(url).scheme not in ("https", "http"):
        return False, f"{ENV_VAR} is not an http(s) URL"
    host = webhook_host(url)
    try:
        resp = httpx.post(url, json={"text": text}, timeout=_TIMEOUT_S)
    except Exception as e:   # httpx.HTTPError, InvalidURL...: its message may quote the URL
        return False, f"{host} unreachable ({type(e).__name__})"
    if resp.status_code >= 300:
        body = " ".join(scrub_text(resp.text).split())[:200]
        return False, f"{host} answered {resp.status_code} {body}".rstrip()
    return True, f"{host} answered {resp.status_code}"


def notify(pr: dict, config, result: dict, logger: logging.Logger) -> bool:
    """Sends the alert for this run if it failed. True when a message went out."""
    try:
        text = failure_text(pr, config, result)
        if not text:
            return False
        url = (getattr(config, "alert_webhook", "") or "").strip()
        if not url:
            logger.info("No alert sent: %s is not set", ENV_VAR)
            return False
        key = hashlib.sha256(text.encode("utf-8")).hexdigest()
        with _sent_lock:
            if key in _sent:
                return False
        ok, detail = send(url, text)
        if ok:
            with _sent_lock:
                _sent.add(key)
            logger.info("Failure alert sent: %s", detail)
        else:
            logger.warning("Failure alert NOT sent: %s", detail)
        return ok
    except Exception as e:
        logger.warning("Failure alert NOT sent (%s)", type(e).__name__)
        return False


def send_test(url: str, repo: str = "") -> tuple[bool, str]:
    """`reviewme doctor --test-alert`: proves the webhook works before a failure needs it."""
    about = f" for {_escape(repo)}" if repo else ""
    return send(url, f"ReviewMe: test message{about}. Failed reviews will be reported here.")
