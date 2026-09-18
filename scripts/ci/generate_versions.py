#!/usr/bin/env python3
"""Generate build.toml-derived version exports into versions_common.sh.

Single direction: build.toml -> scripts/install/versions_common.sh.
Only rewrites the lines derived from build.toml; all other lines
(checksums, URLs, SDK pins) are left untouched.

Usage:
    python scripts/ci/generate_versions.py [--check]

--check exits non-zero with a diff when the file is out of sync.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore
    except ImportError:
        tomllib = None  # type: ignore

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "scripts" / "install" / "versions_common.sh"


def load_config() -> dict:
    if tomllib is None:
        raise SystemExit("tomllib/tomli is required")
    with open(ROOT / "build.toml", "rb") as f:
        return tomllib.load(f)


def replacements(cfg: dict) -> list[tuple[str, str]]:
    flutter = cfg.get("flutter", {})
    ndk = cfg.get("ndk", {})
    android = cfg.get("android", {})
    installer = cfg.get("installer", {})
    tag = str(flutter.get("tag", ""))
    return [
        (r'^export FLUTTER_VERSION="[^"]*"', f'export FLUTTER_VERSION="{tag}"'),
        (r'^export RELEASE_TAG="[^"]*"', f'export RELEASE_TAG="{tag}"'),
        (r'^export NDK_VERSION="[^"]*"', f'export NDK_VERSION="{ndk.get("version", "")}"'),
        (
            r'^export COMPILE_SDK="\$\{TERMUX_COMPILE_SDK:-[^}]*\}"',
            'export COMPILE_SDK="${TERMUX_COMPILE_SDK:-%s}"' % android.get("compile_sdk", 36),
        ),
        (
            r'^export TARGET_SDK="\$\{TERMUX_TARGET_SDK:-[^}]*\}"',
            'export TARGET_SDK="${TERMUX_TARGET_SDK:-%s}"' % android.get("target_sdk", 36),
        ),
        (
            r'^export JAVA_PACKAGE="\$\{JAVA_PACKAGE:-[^}]*\}"',
            'export JAVA_PACKAGE="${JAVA_PACKAGE:-%s}"' % installer.get("java", "openjdk-21"),
        ),
        (
            r'^export ZIP_TOOL="\$\{ZIP_TOOL:-[^}]*\}"',
            'export ZIP_TOOL="${ZIP_TOOL:-%s}"' % installer.get("zip_tool", "7zip"),
        ),
    ]


def render(text: str, subs: list[tuple[str, str]]) -> str:
    for pattern, replacement in subs:
        text, n = re.subn(pattern, replacement, text, flags=re.M)
        if n == 0:
            raise SystemExit(f"generate_versions: pattern not found: {pattern}")
    return text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    cfg = load_config()
    original = TARGET.read_text(encoding="utf-8")
    updated = render(original, replacements(cfg))
    if args.check:
        if updated != original:
            print(f"{TARGET.relative_to(ROOT)} is out of sync with build.toml", file=sys.stderr)
            return 1
        print("versions_common.sh is in sync with build.toml.")
        return 0
    if updated != original:
        TARGET.write_text(updated, encoding="utf-8")
        print(f"updated {TARGET.relative_to(ROOT)} from build.toml")
    else:
        print("versions_common.sh already in sync.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
