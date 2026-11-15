#!/usr/bin/env python3
"""Fold the ``[Unreleased]`` changelog body into a dated release section.

``autorelease`` runs this before bumping to a new upstream tag. The new line's
first release then describes what changed since the previous release instead of
an ever-growing history bucket, and the changelog keeps the old entries under
the version they actually shipped as.
"""

from __future__ import annotations

import argparse
import re
from datetime import date
from pathlib import Path

UNRELEASED_RE = re.compile(r"(?m)^## \[Unreleased\]\s*$")
NEXT_SECTION_RE = re.compile(r"(?m)^## \[")


def close_unreleased(changelog_text: str, previous_version: str, today: str) -> tuple[str, bool]:
    """Move the Unreleased body under ``[previous_version] - today``.

    Returns ``(text, changed)``; an empty (or missing) Unreleased body leaves the
    text untouched, so re-running this on a closed changelog is a no-op.
    """
    match = UNRELEASED_RE.search(changelog_text)
    if not match:
        return changelog_text, False
    rest = changelog_text[match.end() :]
    following = NEXT_SECTION_RE.search(rest)
    body_end = match.end() + (following.start() if following else len(rest))
    body = changelog_text[match.end() : body_end].strip()
    if not body:
        return changelog_text, False
    section = f"\n\n## [{previous_version}] - {today}\n\n{body}\n"
    return changelog_text[: match.end()] + section + changelog_text[body_end:], True


def main() -> int:
    parser = argparse.ArgumentParser(description="Close the Unreleased changelog section")
    parser.add_argument(
        "--previous-version",
        required=True,
        help="Release tag being closed, e.g. 3.50.0-6",
    )
    parser.add_argument(
        "--changelog",
        default="docs/releases/CHANGELOG.md",
        help="Path to the changelog file",
    )
    parser.add_argument("--today", default="", help="Section date (defaults to today UTC)")
    args = parser.parse_args()
    path = Path(args.changelog)
    if not path.is_file():
        print(f"changelog not found: {path}")
        return 1
    text = path.read_text(encoding="utf-8")
    new_text, changed = close_unreleased(
        text, args.previous_version, args.today or date.today().isoformat()
    )
    if changed:
        path.write_text(new_text, encoding="utf-8")
        print(f"closed [{args.previous_version}]")
    else:
        print("nothing to close")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
