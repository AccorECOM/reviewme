"""Safeguard for the v0.2 -> v3 fingerprint migration.

The risk covered: the first v3 review on a PR already commented by v0.2 reposts
EVERYTHING as duplicates because the old markers (without a reviewer id) are no longer
recognized. These tests pin the two invariants that prevent it:

  1. the HASH is unchanged (reviewer_id is a prefix, it is not part of the sha1);
  2. a marker without a reviewer is attributed to `tech`.

Run: `pytest tests/` or `python tests/test_fingerprint_migration.py`.
"""
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reviewme.diff_utils import normalize_line
from reviewme.reconciler import (
    _MARKER_RE,
    LEGACY_REVIEWER_ID,
    fingerprint,
    fingerprint_hash,
    marker_key,
)

PATH = "src/dashboard/DashboardService.swift"
LINE = '        let authToken = "sk-live-XXXX"'


def _legacy_hash(path: str, line_content: str) -> str:
    """Original formula, copied verbatim. Do not "modernize" it: it is the control —
    if `fingerprint_hash` drifts from it, every comment already posted becomes
    orphaned."""
    return hashlib.sha1(f"{path}\n{normalize_line(line_content)}".encode()).hexdigest()[:10]


def test_hash_unchanged_since_v02():
    # If this test breaks, ALL comments already posted become orphaned -> duplicates.
    assert fingerprint_hash(PATH, LINE) == _legacy_hash(PATH, LINE)


def test_reviewer_id_not_in_hash():
    assert fingerprint_hash(PATH, LINE) == fingerprint_hash(PATH, LINE)
    assert "tech" not in fingerprint_hash(PATH, LINE)
    # two reviewers -> same hash, distinct markers
    assert fingerprint(PATH, LINE, "tech") != fingerprint(PATH, LINE, "us")


def test_legacy_marker_attributed_to_tech():
    legacy = f"<!-- reviewme:{_legacy_hash(PATH, LINE)} -->"
    assert marker_key(legacy) == (LEGACY_REVIEWER_ID, _legacy_hash(PATH, LINE))


def test_migration_zero_duplicates():
    """THE migration test: a v0.2 comment and a v3 finding from the `tech` reviewer
    produce the SAME dedup key -> the finding is deduplicated, not reposted."""
    old = f"<!-- reviewme:{_legacy_hash(PATH, LINE)} -->"          # posted by v0.2
    new = fingerprint(PATH, LINE, "tech")                          # computed by v3
    assert marker_key(old) == marker_key(new)


def test_reviewers_do_not_dedupe_each_other():
    """Two reviewers on the SAME line must coexist (otherwise the second is swallowed)."""
    assert marker_key(fingerprint(PATH, LINE, "tech")) != marker_key(fingerprint(PATH, LINE, "us"))
    assert marker_key(fingerprint(PATH, LINE, "us")) == ("us", fingerprint_hash(PATH, LINE))


def test_human_comment_not_recognized():
    assert marker_key("Good point, fixing it.") is None
    assert marker_key("") is None
    assert marker_key(None) is None


def test_both_forms_stripped_from_body():
    """`_degraded_body` strips the marker before display: both forms must go."""
    for body in (f"text <!-- reviewme:{_legacy_hash(PATH, LINE)} -->",
                 f"text {fingerprint(PATH, LINE, 'i18n')}"):
        assert "reviewme:" not in _MARKER_RE.sub("", body)


def test_id_with_dash_and_underscore():
    for rid in ("i18n", "us", "tech-ios", "sec_audit"):
        assert marker_key(fingerprint(PATH, LINE, rid)) == (rid, fingerprint_hash(PATH, LINE))


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok   {name}")
            except AssertionError as e:
                fails += 1
                print(f"  FAIL {name}: {e}")
    print("\n" + ("ALL TESTS PASS" if not fails else f"{fails} FAILURE(S)"))
    sys.exit(1 if fails else 0)
