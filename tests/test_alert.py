"""Failure alert (`REVIEWME_ALERT_WEBHOOK`): a review that fails must not look like a
clean one.

An `API Error` relayed by the CLI is never posted on the PR. Before this, the run then
ended as a success: exit 0, commit marked as reviewed, nothing anywhere but the log.
"""
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import reviewme.alert as alert
import reviewme.run as R
from reviewme.config import Config, _load_instance_env
from reviewme.models import Finding, ReviewResult, Severity
from reviewme.projects import ProjectConfig, ReviewerSpec

LOGGER = logging.getLogger("test")
LOGGER.addHandler(logging.NullHandler())
PR = {"number": 7, "head": {"sha": "abcdef1234567890"}, "title": "Secret roadmap title",
      "html_url": "https://github.com/o/r/pull/7", "user": {"login": "dev"}}


def _config(**kw):
    base = dict(github_token="t", github_repo="o/r", confidence_threshold=80)
    base.update(kw)
    return Config(**base)


@pytest.fixture(autouse=True)
def _fresh_memory():
    alert._sent.clear()
    yield
    alert._sent.clear()


@pytest.fixture
def hook():
    """A real local endpoint: what the engine sends is read off the wire."""
    received: list[dict] = []
    answer = {"status": 200}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("content-length") or 0))
            received.append({"type": self.headers.get("content-type"),
                             "json": json.loads(body)})
            self.send_response(answer["status"])
            self.send_header("content-length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/services/T000/B000/secretpath"
    yield url, received, answer
    server.shutdown()
    server.server_close()


# --------------------------------------------------------------- message

def test_nothing_to_report_on_a_review_that_ran():
    for ok in ({"posted": 2}, {"skipped": True}, {"skipped": True, "reason": "empty_diff"}):
        assert alert.failure_text(PR, _config(), ok, env={}) is None


def test_full_failure_names_the_pr_the_reviewer_and_the_error():
    result = {"error": "all reviewers failed: tech",
              "failures": [{"reviewer": "tech", "error": "API Error: 529 Overloaded"}]}
    text = alert.failure_text(PR, _config(), result,
                              env={"BITRISE_BUILD_URL": "https://app.bitrise.io/build/abc"})
    lines = text.splitlines()
    assert lines[0] == ("*ReviewMe: review failed* on "
                        "<https://github.com/o/r/pull/7|o/r #7> (`abcdef12`)")
    assert lines[1] == "• *tech*: `API Error: 529 Overloaded`"
    assert lines[2] == ("Nothing was posted on the PR. "
                        "<https://app.bitrise.io/build/abc|Open the build>")
    assert len(lines) == 3                         # short enough to stay unfolded in Slack
    assert "Secret roadmap title" not in text      # no PR title leaves the engine


def test_partial_failure_says_the_others_ran():
    result = {"posted": 3, "failures": [{"reviewer": "us", "error": "API Error: 500"}]}
    text = alert.failure_text(PR, _config(), result, env={})
    assert text.startswith("*ReviewMe: 1 reviewer(s) failed* on <")
    assert "• *us*: `API Error: 500`" in text
    assert "The other reviewers ran to the end" in text and "Open the build" not in text


def test_usage_limit_is_reported_as_such():
    result = {"error": "usage limit reached, resets 3pm", "usage_limit": True}
    text = alert.failure_text(PR, _config(), result, env={})
    assert "usage limit reached" in text.splitlines()[0] and "not reviewed" in text


def test_error_without_a_reviewer_is_still_reported():
    text = alert.failure_text(PR, _config(), {"error": "project config: no reviewer"}, env={})
    assert "• `project config: no reviewer`" in text


def test_auth_error_text_is_withheld():
    """A gateway prints the suffix and the hash of the key it received."""
    result = {"error": "x", "failures": [{
        "reviewer": "tech",
        "error": "Failed to authenticate. API Error: 401 Key ...ab12, Key Hash = deadbeef"}]}
    text = alert.failure_text(PR, _config(), result, env={})
    assert "ab12" not in text and "deadbeef" not in text and "text withheld" in text


def test_secrets_and_slack_markup_never_reach_the_channel():
    result = {"error": "base branch '<!channel>' rejected, token=ghp_" + "a" * 36
                       + " & more"}
    text = alert.failure_text(PR, _config(), result, env={})
    assert "ghp_" not in text and "[REDACTED-SECRET]" in text
    assert "<!channel>" not in text and "&lt;!channel&gt;" in text and "&amp; more" in text


def test_long_error_is_cut():
    text = alert.failure_text(PR, _config(), {"error": "x" * 5000}, env={})
    assert len(text) < 500


def test_reviewers_stopped_by_the_same_error_share_one_line():
    """Seven reviewers hit by one outage: three lines, not nine behind "See more"."""
    ids = ["architect", "tech", "us", "i18n", "a11y", "perf", "advisor"]
    result = {"error": "all reviewers failed",
              "failures": [{"reviewer": r, "error": "API Error: 529 Overloaded"} for r in ids]}
    lines = alert.failure_text(PR, _config(), result, env={}).splitlines()
    assert len(lines) == 3
    assert lines[1] == f"• *{', '.join(ids)}*: `API Error: 529 Overloaded`"


def test_many_distinct_errors_are_capped():
    result = {"error": "x", "failures": [{"reviewer": f"r{i}", "error": f"failure {i}"}
                                         for i in range(7)]}
    lines = alert.failure_text(PR, _config(), result, env={}).splitlines()
    assert len(lines) == 2 + alert._MAX_LINES
    assert lines[1] == "• *r0*: `failure 0`"
    assert lines[-2] == "• and 4 other errors, see the build log"


def test_error_text_cannot_break_the_formatting():
    result = {"error": "x", "failures": [{"reviewer": "te*ch", "error": "bad `code` here"}]}
    text = alert.failure_text(PR, _config(), result, env={})
    assert "• *tech*: `bad 'code' here`" in text


def test_a_build_url_that_is_not_a_plain_link_is_not_linked():
    for bad in ("https://ci.example/x|<!channel>", "javascript:alert(1)", "https://a b"):
        text = alert.failure_text(PR, _config(), {"error": "boom"},
                                  env={"REVIEWME_BUILD_URL": bad})
        assert "<!channel>" not in text and "javascript" not in text
        assert text.endswith("Open the build")


def test_build_url_from_github_actions():
    env = {"GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": "o/r",
           "GITHUB_RUN_ID": "42"}
    assert alert.build_url(env) == "https://github.com/o/r/actions/runs/42"
    assert alert.build_url({}) == ""


# --------------------------------------------------------------- sending

def test_webhook_receives_a_slack_payload(hook):
    url, received, _ = hook
    sent = alert.notify(PR, _config(alert_webhook=url),
                        {"error": "all reviewers failed: tech",
                         "failures": [{"reviewer": "tech", "error": "API Error: 529"}]}, LOGGER)
    assert sent and len(received) == 1
    assert received[0]["type"] == "application/json"
    payload = received[0]["json"]
    assert set(payload) == {"text", "unfurl_links", "unfurl_media"}
    assert payload["unfurl_links"] is False and payload["unfurl_media"] is False
    assert "API Error: 529" in payload["text"]


def test_no_webhook_no_call_and_no_error(hook):
    _, received, _ = hook
    assert alert.notify(PR, _config(), {"error": "boom"}, LOGGER) is False
    assert not received


def test_success_sends_nothing(hook):
    url, received, _ = hook
    assert alert.notify(PR, _config(alert_webhook=url), {"posted": 1}, LOGGER) is False
    assert not received


def test_refused_alert_is_logged_without_the_url(hook, caplog):
    url, _, answer = hook
    answer["status"] = 404
    with caplog.at_level(logging.INFO, logger="test-alert"):
        sent = alert.notify(PR, _config(alert_webhook=url), {"error": "boom"},
                            logging.getLogger("test-alert"))
    assert sent is False
    assert "NOT sent" in caplog.text and "404" in caplog.text
    assert "secretpath" not in caplog.text


def test_unreachable_webhook_never_raises(caplog):
    with caplog.at_level(logging.INFO, logger="test-alert"):
        sent = alert.notify(PR, _config(alert_webhook="http://127.0.0.1:9/hook/secretpath"),
                            {"error": "boom"}, logging.getLogger("test-alert"))
    assert sent is False and "secretpath" not in caplog.text


def test_not_a_url_is_refused():
    ok, detail = alert.send("file:///etc/passwd", "x")
    assert not ok and "not an http(s) URL" in detail


def test_a_loop_alerts_once_per_failure(hook):
    """`reviewme watch` meets an unmarked failure again on every round."""
    url, received, _ = hook
    cfg = _config(alert_webhook=url)
    assert alert.notify(PR, cfg, {"error": "project config: broken"}, LOGGER) is True
    assert alert.notify(PR, cfg, {"error": "project config: broken"}, LOGGER) is False
    assert alert.notify(PR, cfg, {"error": "another failure"}, LOGGER) is True
    assert len(received) == 2


def test_test_message(hook):
    url, received, _ = hook
    ok, detail = alert.send_test(url, "o/r")
    assert ok and "secretpath" not in detail
    assert "*ReviewMe: test message* for o/r" in received[0]["json"]["text"]


def test_instance_env_cannot_redirect_alerts(tmp_path, monkeypatch):
    """A config repo's `.env` must not choose where the engine POSTs."""
    monkeypatch.delenv("REVIEWME_ALERT_WEBHOOK", raising=False)
    env = tmp_path / ".env"
    env.write_text("REVIEWME_ALERT_WEBHOOK=https://evil.example/x\n", encoding="utf-8")
    _load_instance_env(env)
    import os
    assert "REVIEWME_ALERT_WEBHOOK" not in os.environ


def _doctor(monkeypatch, capsys, *flags):
    import reviewme.cli as cli
    monkeypatch.setattr("reviewme.config.load_config", lambda **kw: None)   # no local .env
    monkeypatch.setattr("sys.argv", ["reviewme", "doctor", "--no-probe", *flags])
    with pytest.raises(SystemExit) as exit_:
        cli.main()
    return exit_.value.code, capsys.readouterr().out


def test_doctor_sends_a_test_message_and_hides_the_url(hook, monkeypatch, capsys):
    url, received, answer = hook
    monkeypatch.setenv("REVIEWME_ALERT_WEBHOOK", url)
    code, out = _doctor(monkeypatch, capsys, "--test-alert")
    assert code == 0 and "alert test: sent" in out and len(received) == 1
    assert "set (host 127.0.0.1" in out and "secretpath" not in out
    answer["status"] = 403
    code, out = _doctor(monkeypatch, capsys, "--test-alert")
    assert code == 1 and "alert test: FAILED" in out and "403" in out


def test_doctor_says_when_no_alert_is_configured(hook, monkeypatch, capsys):
    _, received, _ = hook
    monkeypatch.delenv("REVIEWME_ALERT_WEBHOOK", raising=False)
    code, out = _doctor(monkeypatch, capsys)
    assert code == 0 and "not set" in out and not received       # no flag, no message
    code, out = _doctor(monkeypatch, capsys, "--test-alert")
    assert code == 1 and "alert test: FAILED" in out


# --------------------------------------------------------------- the run itself

class _GH:
    def __init__(self):
        self.posted, self.reviews = [], []

    def get_pr_diff(self, n):
        return ("diff --git a/a.swift b/a.swift\n--- a/a.swift\n+++ b/a.swift\n"
                "@@ -1,1 +1,2 @@\n line\n+let x = y!\n")

    def get_pr_files(self, n):
        return [{"filename": "a.swift", "patch": "@@ -1,1 +1,2 @@\n line\n+let x = y!"}]

    def list_review_comments(self, n, review_id=None):
        return []

    def list_issue_comments(self, n):
        return []

    def create_review(self, n, sha, body, comments, event):
        self.reviews.append(comments)
        return {}

    def post_issue_comment(self, n, body):
        self.posted.append(body)
        return {"id": 1}


def _api_error():
    return ReviewResult(status="COMMENT", summary="", findings=[], parsed_ok=False,
                        raw="API Error: 529 Overloaded",
                        metadata={"is_error": True, "cost_usd": 0})


def _review():
    f = Finding(path="a.swift", line=2, severity=Severity.BLOCKER, message="Force unwrap",
                snippet="let x = y!", confidence=95)
    return ReviewResult(status="COMMENT", summary="", findings=[f], parsed_ok=True,
                        metadata={"cost_usd": 0.1})


@pytest.fixture
def engine(tmp_path, monkeypatch):
    """`run_review` with its side effects pointed at a temp folder, and the model call
    replaced by a table of answers per reviewer."""
    import reviewme.state as state
    monkeypatch.setattr(state, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(state, "STATE_LOCK", tmp_path / "state.lock")
    monkeypatch.setattr(R, "save_review", lambda *a, **k: None)
    monkeypatch.setattr(R, "enregistrer_stats", lambda *a, **k: None)
    monkeypatch.setattr(R, "build_history_context", lambda *a, **k: "")
    monkeypatch.setattr("reviewme.ledger.record", lambda *a, **k: "run-1")
    monkeypatch.setenv("FETCH_BASE_TIMEOUT", "0")
    answers: dict = {}

    def fake_reviewer(pr_number, title, diff, config, spec=None, extra_context=""):
        answer = answers[spec.id]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(R, "run_reviewer", fake_reviewer)

    def run(**by_reviewer):
        answers.update(by_reviewer)
        specs = tuple(ReviewerSpec(id=rid, project="p", system_prompt="x", verify=False,
                                   context_auto=False) for rid in by_reviewer)
        monkeypatch.setattr(R, "resolve_project", lambda config: ProjectConfig(
            name="p", directory=None, reviewers=specs))
        sent: list[dict] = []
        monkeypatch.setattr(alert, "send", lambda url, text: (sent.append({"text": text})
                                                              or (True, "ok")))
        gh = _GH()
        cfg = _config(alert_webhook="https://hooks.example/x", repo_path=str(tmp_path),
                      verify=False, config_from_base=False)
        out = R.run_review(dict(PR, base={"ref": "main"}), cfg, gh, LOGGER)
        return out, gh, sent, state

    return run


def test_api_error_is_a_failed_review_not_a_clean_one(engine):
    out, gh, sent, state = engine(tech=_api_error())
    assert out["error"] == "all reviewers failed: tech"
    assert not gh.posted and not gh.reviews                  # the error text stays off the PR
    assert not state.is_reviewed(7, PR["head"]["sha"], "o/r")   # a rebuild runs it again
    assert state.failed_at(7, PR["head"]["sha"], "o/r")         # a loop does not
    assert len(sent) == 1 and "• *tech*: `API Error: 529 Overloaded`" in sent[0]["text"]


def test_every_reviewer_in_error_fails_the_run(engine):
    out, _, sent, _ = engine(tech=_api_error(), us=RuntimeError("claude returncode=1"))
    assert out["error"] == "all reviewers failed: us, tech"
    assert "• *us*: `claude returncode=1`" in sent[0]["text"] and "• *tech*:" in sent[0]["text"]


def test_one_reviewer_in_error_keeps_the_others_and_alerts(engine):
    out, gh, sent, state = engine(tech=_review(), us=_api_error())
    assert "error" not in out and out["posted"] == 1
    assert [f["reviewer"] for f in out["failures"]] == ["us"]
    assert len(gh.reviews) == 1                              # tech's remark is posted
    assert state.is_reviewed(7, PR["head"]["sha"], "o/r")
    assert len(sent) == 1 and "1 reviewer(s) failed" in sent[0]["text"]


def test_clean_run_sends_nothing(engine):
    out, _, sent, state = engine(tech=_review())
    assert "error" not in out and "failures" not in out and not sent
    assert state.is_reviewed(7, PR["head"]["sha"], "o/r")


def test_unreadable_pr_is_reported_too(monkeypatch):
    """An expired GitHub token fails before the review starts: it must not be the one
    failure nobody hears about."""
    sent: list[str] = []
    monkeypatch.setattr(alert, "send", lambda url, text: (sent.append(text) or (True, "ok")))

    class _Down:
        def get_pr(self, n):
            raise RuntimeError("GitHub API 503: unavailable")

    out = R.run_review_by_number(7, _config(alert_webhook="https://hooks.example/x"), LOGGER,
                                 gh=_Down())
    assert out["error"].startswith("cannot read PR #7 from GitHub")
    assert len(sent) == 1 and "503" in sent[0]
    assert sent[0].startswith("*ReviewMe: review failed* on <https://github.com/o/r/pull/7|o/r #7>")
