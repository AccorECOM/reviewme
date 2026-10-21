"""Tests for the simulated mode (`REVIEWME_FAKE`).

What matters is not that the mode "returns something", but that it returns something
USABLE by the rest of the pipeline:
  - findings anchored on real diff lines, otherwise the anti-422 validation rejects
    them all and nothing gets tested anymore;
  - distinct fingerprints per reviewer, otherwise dedup hides the fan-out;
  - confidences on both sides of the threshold, to see the filtering at work.

Run: `pytest tests/`.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reviewme.fake import _lignes_ajoutees, review_simulee
from reviewme.models import Severity
from reviewme.reconciler import _SEV_LABEL, _SEV_RANK

DIFF = """diff --git a/src/App.swift b/src/App.swift
index 1111111..2222222 100644
--- a/src/App.swift
+++ b/src/App.swift
@@ -10,6 +10,8 @@ struct App {
 let a = 1
+let added1 = 2
+let added2 = 3
 let b = 4
-let removed = 5
+let added3 = 6
diff --git a/src/Other.swift b/src/Other.swift
--- a/src/Other.swift
+++ b/src/Other.swift
@@ -1,2 +1,3 @@
 start
+let added4 = 7
"""

def check(name, condition, detail=""):
    """Single assertion point: a failing check must fail the test under pytest."""
    assert condition, f"{name}: {detail}"


def test_right_side_line_numbers():
    """A deleted line must NOT consume a RIGHT-side line number."""
    added = list(_lignes_ajoutees(DIFF))
    expected = [
        ("src/App.swift", 11, "let added1 = 2"),
        ("src/App.swift", 12, "let added2 = 3"),
        ("src/App.swift", 14, "let added3 = 6"),   # 13 = `let b`, the deletion does not count
        ("src/Other.swift", 2, "let added4 = 7"),
    ]
    check("test_right_side_line_numbers", added == expected, f"got {added}")


def test_findings_anchored_on_diff():
    """Every finding must point to a line that was actually added."""
    real = {(c, n) for c, n, _ in _lignes_ajoutees(DIFF)}
    r = review_simulee("tech", DIFF)
    outside = [(f.path, f.line) for f in r.findings if (f.path, f.line) not in real]
    check("test_findings_anchored_on_diff", not outside, f"outside diff: {outside}")


def test_confidences_straddle_threshold():
    r = review_simulee("tech", DIFF)
    conf = sorted(f.confidence for f in r.findings)
    check("test_confidences_straddle_threshold",
            any(c < 70 for c in conf) and any(c >= 70 for c in conf), f"confidences {conf}")


def test_reviewers_anchor_in_different_places():
    """Otherwise all four produce the same fingerprint and dedup hides the fan-out."""
    anchors = {rid: {(f.path, f.line) for f in review_simulee(rid, DIFF).findings}
              for rid in ("tech", "architect", "us", "i18n")}
    distinct = len({frozenset(v) for v in anchors.values()})
    check("test_reviewers_anchor_in_different_places", distinct > 1,
            f"{distinct} distinct anchor set(s) for 4 reviewers")


def test_severities_are_enum_and_render_distinctly():
    """A free-form string would fall back to [MINOR] for all three, silently."""
    findings = review_simulee("tech", DIFF).findings
    sev = [f.severity for f in findings]
    labels = [_SEV_LABEL.get(f.severity, "[DEFAULT]") for f in findings]
    check("test_severities_are_enum_and_render_distinctly",
            all(isinstance(s, Severity) for s in sev)
            and all(s in _SEV_RANK for s in sev)
            and len(set(labels)) == len(labels),
            f"severities {sev}, rendered {labels}")


def test_no_cost_and_metadata_flagged():
    r = review_simulee("tech", DIFF)
    check("test_no_cost_and_metadata_flagged",
            r.metadata["cost_usd"] == 0.0 and r.metadata.get("fake") is True and r.parsed_ok)


def test_empty_diff_does_not_break():
    r = review_simulee("tech", "")
    check("test_empty_diff_does_not_break", r.findings == [] and r.parsed_ok)
