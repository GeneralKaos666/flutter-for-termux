#!/usr/bin/env python3
"""Resolve the body of a release from the notes written for its revision.

Respin notes live in ``docs/releases/RELEASE_NOTES.md`` under
``## Fixed in <package version>`` and are authoritative for the revision being
published. The changelog supplies the history for the first revision of a line
(``<tag>-1``) and the upstream pointer. A generated bump summary is the last
resort, so a version bump never ships a bare commit message as its notes.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

FOOTER = (
    "Full history: https://github.com/GeneralKaos666/flutter-for-termux/blob/main/"
    "docs/releases/CHANGELOG.md"
)
SECTION_RE = re.compile(r"(?m)^## \[(?P<name>[^\]]+)\].*$")
NOTES_HEADING_RE = re.compile(r"(?m)^## Fixed in (?P<name>[^\s]+)\s*$")
UPSTREAM_RE = re.compile(r"Stable release notes: (\S+)")


def _sections(pattern: re.Pattern[str], text: str) -> dict[str, str]:
    """Return ``{heading name: body}`` for every heading the pattern matches."""
    matches = list(pattern.finditer(text))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[match.group("name").strip()] = text[match.end() : end].strip()
    return sections


def extract_notes(tag: str, text: str) -> str:
    """Return the changelog body for tag, or "" when no section applies."""
    sections = _sections(SECTION_RE, text)
    for name in (f"{tag}-termux", tag, "Unreleased"):
        body = sections.get(name, "")
        if body:
            return body
    return ""


def extract_respin_notes(package_version: str, text: str) -> str:
    """Return the ``## Fixed in <package_version>`` body, or "" when absent."""
    return _sections(NOTES_HEADING_RE, text).get(package_version, "")


def upstream_notes_url(changelog_text: str) -> str:
    """Return the first upstream notes link recorded in the changelog."""
    match = UPSTREAM_RE.search(changelog_text)
    return match.group(1) if match else ""


def build_bump_summary(
    tag: str,
    dart_version: str,
    framework_revision: str,
    framework_commit_date: str,
    upstream_url: str,
) -> str:
    """Describe the Flutter version a fresh line is built from."""
    headline = f"Flutter {tag}"
    if dart_version:
        headline += f" / Dart {dart_version}"
    lines = [f"**{headline}** for Android-bionic ARM64 hosts.", ""]
    if framework_revision:
        stamp = f" ({framework_commit_date})" if framework_commit_date else ""
        lines.append(f"Framework revision `{framework_revision}`{stamp}.")
    if upstream_url:
        lines += ["", f"Upstream release notes: {upstream_url}"]
    return "\n".join(lines).strip()


def release_body(
    *,
    tag: str,
    package_version: str,
    notes_text: str,
    changelog_text: str,
    flutter_cfg: dict[str, str],
) -> str:
    """Resolve the body for package_version, or "" when nothing applies."""
    body = extract_respin_notes(package_version, notes_text)
    if not body and package_version.endswith("-1"):
        # A hand-written section for the new line wins over the shared
        # Unreleased bucket, which also carries the upstream pointer.
        sections = _sections(SECTION_RE, changelog_text)
        for name in (
            f"{package_version}-termux",
            package_version,
            f"{tag}-termux",
            tag,
            "Unreleased",
        ):
            body = sections.get(name, "")
            if body:
                break
    if not body:
        body = build_bump_summary(
            tag=tag,
            dart_version=str(flutter_cfg.get("dart_version", "") or ""),
            framework_revision=str(flutter_cfg.get("framework_revision", "") or ""),
            framework_commit_date=str(flutter_cfg.get("framework_commit_date", "") or ""),
            upstream_url=upstream_notes_url(changelog_text),
        )
    if not body:
        return ""
    return f"{body}\n\n{FOOTER}"


def read_build_toml(path: Path) -> tuple[dict[str, str], str]:
    """Return ``([flutter] section, pkg_rel)``; empty values when unreadable."""
    if not path.is_file():
        return {}, ""
    with open(path, "rb") as handle:
        data = tomllib.load(handle)
    flutter = data.get("flutter", {})
    pkg_rel = str(data.get("package", {}).get("pkg_rel") or "").strip()
    return flutter, pkg_rel


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract release notes for a package version")
    parser.add_argument("--tag", default="", help="Flutter tag, e.g. 3.50.0 (defaults to build.toml)")
    parser.add_argument(
        "--package-version",
        default="",
        help="Release identity, e.g. 3.50.0-6 (defaults to tag plus pkg_rel)",
    )
    parser.add_argument(
        "--changelog",
        default="docs/releases/CHANGELOG.md",
        help="Path to the changelog file",
    )
    parser.add_argument(
        "--release-notes",
        default="docs/releases/RELEASE_NOTES.md",
        help="Path to the per-revision notes file",
    )
    parser.add_argument("--build-toml", default="build.toml", help="Path to build.toml")
    args = parser.parse_args()

    flutter_cfg, pkg_rel = read_build_toml(Path(args.build_toml))
    tag = args.tag or str(flutter_cfg.get("tag") or "")
    if not tag:
        parser.error("--tag is required when [flutter] tag is missing from build.toml")
    package_version = args.package_version or (f"{tag}-{pkg_rel}" if pkg_rel else tag)

    notes_path = Path(args.release_notes)
    notes_text = notes_path.read_text(encoding="utf-8") if notes_path.is_file() else ""
    changelog_path = Path(args.changelog)
    changelog_text = changelog_path.read_text(encoding="utf-8") if changelog_path.is_file() else ""

    body = release_body(
        tag=tag,
        package_version=package_version,
        notes_text=notes_text,
        changelog_text=changelog_text,
        flutter_cfg=flutter_cfg,
    )
    if body:
        sys.stdout.write(body + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
