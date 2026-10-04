#!/usr/bin/env python3
"""Extract release notes for a tag from the Keep-a-Changelog CHANGELOG.

Prefers an exact ``## [<tag>-termux]`` / ``## [<tag>]`` section, then falls
back to ``## [Unreleased]`` (whatever history is current), then to empty
(the caller falls back to the last commit message). The ``##`` header line
itself is stripped since the GitHub Release title already carries the
version.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SECTION_RE = re.compile(r"(?m)^## \[(?P<name>[^\]]+)\].*$")


def extract_notes(tag: str, text: str) -> str:
    """Return the notes body for tag, or "" when no section applies."""
    sections: dict[str, str] = {}
    matches = list(SECTION_RE.finditer(text))
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[match.group("name").strip()] = text[start:end].strip()
    for name in (f"{tag}-termux", tag, "Unreleased"):
        body = sections.get(name, "")
        if body:
            return body
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract CHANGELOG notes for a release tag")
    parser.add_argument("--tag", required=True, help="Flutter tag, e.g. 3.47.6")
    parser.add_argument(
        "--changelog",
        default="docs/releases/CHANGELOG.md",
        help="Path to the changelog file",
    )
    args = parser.parse_args()
    path = Path(args.changelog)
    if not path.is_file():
        return 0
    notes = extract_notes(args.tag, path.read_text(encoding="utf-8"))
    if notes:
        sys.stdout.write(notes + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
