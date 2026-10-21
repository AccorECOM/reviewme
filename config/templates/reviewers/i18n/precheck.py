#!/usr/bin/env python3
"""i18n precheck: compares translation keys across languages.

What this script establishes is a FACT — a key is present or missing, there is nothing to
judge. Doing it here rather than in the LLM is free, instant and has no false positives; the
model keeps what really requires judgement (wording, variables, plurals, length).

Supported formats: `.strings` (iOS), `strings.xml` (Android), `.json` (web/JS).
Output: markdown on stdout, injected as is into the reviewer's prompt.

Usage: precheck.py --repo /path/to/clone   (the PR diff arrives on stdin)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

#: Folders never scanned: dependencies, build artefacts, local tooling. Without this, an
#: iOS repository picks up the localizations of its third-party packages (and of any worktree present).
_IGNORES = frozenset({
    ".git", ".claude", ".build", "build", "DerivedData", "checkouts",
    "Pods", "Carthage", "node_modules", "vendor", "dist", ".venv",
})

MAX_FILES = 400          # guardrail: beyond this, the repository is not structured as expected
MAX_REPORTED = 25        # beyond this, we summarize: a wall of keys helps nobody

_STRINGS_RE = re.compile(r'^\s*"((?:[^"\\]|\\.)*)"\s*=', re.MULTILINE)
_IOS_LANG_RE = re.compile(r"/([A-Za-z-]+)\.lproj/")
_ANDROID_LANG_RE = re.compile(r"/values(?:-([A-Za-z][\w-]*))?/")
_WEB_LANG_RE = re.compile(r"/(?:locales?|i18n|lang)/([A-Za-z]{2}(?:[-_][A-Za-z]{2})?)(?:/|\.)")


def _flatten(obj, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            full = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, (dict, list)):
                keys |= _flatten(v, full)
            else:
                keys.add(full)
    return keys


def read_keys(path: Path) -> set[str]:
    try:
        if path.suffix == ".strings":
            return set(_STRINGS_RE.findall(path.read_text(encoding="utf-8", errors="replace")))
        if path.name.startswith("strings") and path.suffix == ".xml":
            root = ET.fromstring(path.read_text(encoding="utf-8", errors="replace"))
            return {el.get("name") for el in root.iter() if el.tag in ("string", "plurals")
                    and el.get("name")}
        if path.suffix == ".json":
            return _flatten(json.loads(path.read_text(encoding="utf-8", errors="replace")))
    except (OSError, ValueError, ET.ParseError):
        return set()
    return set()


def detect(path: Path, root: Path) -> tuple[str, str] | None:
    """(language, bundle) of a localization file, or None if not recognized.

    The "bundle" is the file's PATH stripped of its language segment — not just its
    name. A repository can contain several `InfoPlist.strings` or `Localizable.strings` in
    different targets: grouping them by name would make all the keys of one bundle show up
    as "missing" in the languages of another.
    """
    rel = path.relative_to(root).as_posix()
    posix = "/" + rel

    if m := _IOS_LANG_RE.search(posix):
        lang = m.group(1)
        bundle = rel.replace(f"{lang}.lproj/", "", 1)
        return (("en" if lang.lower() in ("base", "en") else lang), bundle)

    if path.name.startswith("strings") and path.suffix == ".xml" and (m := _ANDROID_LANG_RE.search(posix)):
        segment = f"values-{m.group(1)}/" if m.group(1) else "values/"
        return (m.group(1) or "default", rel.replace(segment, "", 1))

    if path.suffix == ".json":
        if m := _WEB_LANG_RE.search(posix):
            lang = m.group(1)
            return (lang, rel.replace(f"/{lang}/", "/", 1).replace(f"/{lang}.json", "/*.json", 1))
        if re.fullmatch(r"[a-z]{2}([-_][A-Za-z]{2})?", path.stem):
            return (path.stem, rel.replace(path.name, "*.json", 1))
    return None


def collect(repo: Path) -> dict[str, dict[str, set[str]]]:
    """{bundle: {language: keys}} — a "bundle" = the same file, one variant per language."""
    bundles: dict[str, dict[str, set[str]]] = defaultdict(dict)
    seen = 0
    for pattern in ("**/*.strings", "**/strings*.xml", "**/*.json"):
        for path in repo.glob(pattern):
            if not path.is_file() or any(p in _IGNORES for p in path.parts):
                continue
            found = detect(path, repo)
            if not found:
                continue
            lang, bundle = found
            keys = read_keys(path)
            if keys:
                bundles[bundle][lang] = keys
                seen += 1
            if seen >= MAX_FILES:
                return bundles
    return bundles


def report(bundles: dict[str, dict[str, set[str]]]) -> str:
    lines: list[str] = []
    total_missing = 0

    for bundle, per_lang in sorted(bundles.items()):
        if len(per_lang) < 2:
            continue  # a single language: nothing to compare
        union: set[str] = set().union(*per_lang.values())
        gaps = {lang: sorted(union - keys) for lang, keys in per_lang.items()}
        gaps = {lang: missing for lang, missing in gaps.items() if missing}
        langs = ", ".join(f"{lang} ({len(keys)})" for lang, keys in sorted(per_lang.items()))

        if not gaps:
            lines.append(f"- **{bundle}** — {len(union)} keys, complete in all languages: {langs}")
            continue

        total_missing += sum(len(v) for v in gaps.values())
        lines.append(f"- **{bundle}** — {len(union)} keys in total; languages: {langs}")
        for lang, missing in sorted(gaps.items()):
            shown = ", ".join(f"`{k}`" for k in missing[:MAX_REPORTED])
            more = f" … and {len(missing) - MAX_REPORTED} more" if len(missing) > MAX_REPORTED else ""
            lines.append(f"  - ❌ **{lang}**: {len(missing)} missing key(s) — {shown}{more}")

    if not lines:
        return "No multilingual set of localization files detected in this repository."

    header = ("All keys are present in all languages."
              if total_missing == 0 else
              f"**{total_missing} missing key(s)** in total.")
    return f"## Translation completeness\n\n{header}\n\n" + "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="root of the repository to analyse")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        print(f"Repository not found: {repo}", file=sys.stderr)
        return 1

    print(report(collect(repo)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
