#!/usr/bin/env python3
"""Record a pointer to upstream Flutter stable release notes in the CHANGELOG.

The upstream notes live per major.minor series on docs.flutter.dev, so the
pointer is keyed by series (``X.Y``), not by patch tag: bumping 3.47.6 to
3.47.7 must not duplicate the 3.47 entry. Importing a link (instead of the
~1000-line upstream PR list) keeps the changelog readable and avoids
tripping the stale-version-wording scan in ``check_repo.py``.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

DOCS_URL = "https://docs.flutter.dev/release/release-notes/release-notes-{series}.0"


def series_of(tag: str) -> str:
    """Return the major.minor series for a tag like 3.47.6."""
    match = re.match(r"(\d+\.\d+)(?:\.\d+)?", tag.strip())
    if not match:
        raise ValueError(f"cannot derive a major.minor series from tag '{tag}'")
    return match.group(1)


def import_pointer(
    tag: str,
    text: str,
    *,
    notes_available: bool,
    today: str,
) -> tuple[str, str]:
    """Insert the upstream pointer; returns (new_text, outcome).

    Outcome is ``added``, ``already-present``, or ``no-unreleased-section``.
    """
    series = series_of(tag)
    heading = f"### Upstream Flutter {series}"
    if heading in text:
        return text, "already-present"
    status = "source verified" if notes_available else "page not yet published, link only"
    entry = (
        f"{heading}\n"
        f"- Stable release notes: {DOCS_URL.format(series=series)} "
        f"(imported {today}; {status}).\n"
    )
    match = re.search(r"(?m)^## \[Unreleased\]\s*$", text)
    if not match:
        return text, "no-unreleased-section"
    insert_at = match.end()
    return text[:insert_at] + "\n\n" + entry + text[insert_at:], "added"


def main() -> int:
    parser = argparse.ArgumentParser(description="Import upstream notes pointer into CHANGELOG")
    parser.add_argument("--tag", required=True, help="New Flutter tag, e.g. 3.48.0")
    parser.add_argument(
        "--changelog",
        default="docs/releases/CHANGELOG.md",
        help="Path to the changelog file",
    )
    parser.add_argument(
        "--notes-available",
        choices=("yes", "no"),
        default="no",
        help="Whether the upstream notes page already exists",
    )
    parser.add_argument("--today", default="", help="Import date (defaults to today UTC)")
    args = parser.parse_args()
    path = Path(args.changelog)
    if not path.is_file():
        print(f"changelog not found: {path}", file=sys.stderr)
        return 1
    text = path.read_text(encoding="utf-8")
    new_text, outcome = import_pointer(
        args.tag,
        text,
        notes_available=args.notes_available == "yes",
        today=args.today or date.today().isoformat(),
    )
    if outcome == "added":
        path.write_text(new_text, encoding="utf-8")
    print(outcome)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
