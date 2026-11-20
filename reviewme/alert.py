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

The text uses Slack's `mrkdwn` (bold, `<url|label>` links, code spans) and is kept to a
few lines: Slack folds a long message behind "See more", and an alert nobody unfolds is
not read. Reviewers that failed the same way share one line, and link previews are off.

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
_EXCERPT_CHARS = 200
_MAX_LINES = 4        # distinct errors listed; the rest is in the build log
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
    """One error, as a code span: scrubbed, on one line, cut short."""
    if looks_like_auth_error(text):
        return _AUTH_WITHHELD
    flat = " ".join(scrub_text(text).split()).replace("`", "'")
    if len(flat) > _EXCERPT_CHARS:
        flat = flat[:_EXCERPT_CHARS].rstrip() + "…"
    return f"`{_escape(flat)}`" if flat else "no error text"


def _link(url: str, label: str) -> str:
    """`<url|label>`, or the label alone when the URL could break out of the link."""
    url = (url or "").strip()
    if not url.startswith(("https://", "http://")) or any(c in url for c in "<>| \n\t"):
        return _escape(label)
    return f"<{url}|{_escape(label)}>"


def _error_lines(failures: list[dict]) -> list[str]:
    """One line per distinct error, naming every reviewer that hit it: seven reviewers
    stopped by the same outage are one line, not seven."""
    groups: dict[str, list[str]] = {}
    for f in failures:
        groups.setdefault(_excerpt(f.get("error") or ""), []).append(
            _escape(str(f["reviewer"])).replace("*", ""))
    lines = [f"• *{', '.join(names)}*: {error}" for error, names in groups.items()]
    if len(lines) > _MAX_LINES:
        hidden = len(lines) - (_MAX_LINES - 1)
        lines = [*lines[:_MAX_LINES - 1], f"• and {hidden} other errors, see the build log"]
    return lines


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
    where = _link(pr.get("html_url") or f"https://github.com/{repo}/pull/{number}",
                  f"{repo} #{number}")
    if sha:
        where += f" (`{sha}`)"
    if getattr(config, "dry_run", False):
        where += " [dry-run]"

    if result.get("usage_limit"):
        head = f"*ReviewMe: usage limit reached*, {where} was not reviewed"
        outcome = "Nothing was posted on the PR. Run the review again after the reset."
    elif error:
        head = f"*ReviewMe: review failed* on {where}"
        outcome = "Nothing was posted on the PR."
    else:
        head = f"*ReviewMe: {len(failures)} reviewer(s) failed* on {where}"
        outcome = "The other reviewers ran to the end; these ones posted nothing."

    build = build_url(env)
    if build:
        outcome += " " + _link(build, "Open the build")
    details = _error_lines(failures) if failures else [f"• {_excerpt(str(error))}"]
    return "\n".join([head, *details, outcome])


def send(url: str, text: str) -> tuple[bool, str]:
    """Posts `{"text": text}` (plus Slack's two "no link preview" switches) to the
    webhook. Returns (sent, detail); never raises, and the detail never contains the URL."""
    import httpx
    if urlparse(url).scheme not in ("https", "http"):
        return False, f"{ENV_VAR} is not an http(s) URL"
    host = webhook_host(url)
    try:
        # No preview of the PR or build link under the alert: it would triple its height.
        resp = httpx.post(url, json={"text": text, "unfurl_links": False,
                                     "unfurl_media": False}, timeout=_TIMEOUT_S)
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
    return send(url, f"*ReviewMe: test message*{about}. Failed reviews will be reported here.")
