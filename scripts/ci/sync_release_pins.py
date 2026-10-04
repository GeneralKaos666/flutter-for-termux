#!/usr/bin/env python3
"""Sync the published release pins (deb SHA256/size) into installers and docs.

``EXPECTED_SHA256`` defaults and doc size/hash tables cannot come from
``build.toml`` (the hash is only known after the release builds), so they
go stale on every bump. ``build.yml`` runs this right after publishing a
release; it is also safe to run by hand after a manual publish.

Only exact, known patterns are rewritten (installer hash lines, release
download URLs, doc size/hash phrases). Toolchain/sysroot hashes and
changelog history are never touched. Re-running with the same inputs is
a no-op.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore
    except ImportError:
        tomllib = None  # type: ignore

HASH_RE = r"[0-9a-f]{64}"
DEB_RE = r"flutter_\d+\.\d+\.\d+(?:-[^/\s`\"']+)?_aarch64\.deb"
SIZE_PHRASE_RE = re.compile(r"\d[\d,]* bytes \((?:about |~)\d+ MiB\)")

SCRIPT_FILES = (
    "scripts/install/versions_common.sh",
    "scripts/install/lib_common.sh",
    "scripts/test/gh_e2e_test.sh",
    "scripts/device/run_termux_smoke.ps1",
)
DOC_FILES = (
    "docs/guides/BUILD_GUIDE.md",
    "docs/guides/INSTALL_GUIDE.md",
    "docs/releases/RELEASE_NOTES.md",
    "docs/CI_CD.md",
)


def repo_config(root: Path) -> tuple[str, str]:
    """Return (tag, pkg_rel) from build.toml."""
    if tomllib is None:
        raise SystemExit("tomllib/tomli is required")
    with open(root / "build.toml", "rb") as f:
        data = tomllib.load(f)
    tag = str(data.get("flutter", {}).get("tag") or "")
    pkg_rel = str(data.get("package", {}).get("pkg_rel") or "").strip()
    if not tag:
        raise SystemExit("build.toml [flutter] tag is missing")
    return tag, pkg_rel


def asset_name(tag: str, pkg_rel: str) -> str:
    version = f"{tag}-{pkg_rel}" if pkg_rel else tag
    return f"flutter_{version}_aarch64.deb"


def size_phrase(size_bytes: int, qualifier: str) -> str:
    mib = round(size_bytes / (1024 * 1024))
    return f"{size_bytes:,} bytes ({qualifier}{mib} MiB)"


def _sub_count(pattern: str, repl: str, text: str, flags: int = 0) -> tuple[str, int]:
    return re.subn(pattern, repl, text, flags=flags)


def sync_script(path: Path, sha256: str) -> bool:
    """Rewrite installer hash pins in one script. Returns True when changed."""
    text = path.read_text(encoding="utf-8")
    updated = text
    if path.suffix == ".ps1":
        updated, _ = _sub_count(
            r'(\$ExpectedSha256 = ")[0-9a-f]{64}(")', rf"\g<1>{sha256}\g<2>", updated
        )
    elif "EXPECTED_SHA256" in updated:
        # Plain assignment (versions_common.sh) or :-fallback chain
        # (lib_common.sh, gh_e2e_test.sh); other *SHA256* lines are left alone.
        updated, _ = _sub_count(
            r'^(export EXPECTED_SHA256=")[0-9a-f]{64}(")',
            rf"\g<1>{sha256}\g<2>",
            updated,
            flags=re.M,
        )
        updated, _ = _sub_count(
            r"(FLUTTER_DEB_SHA256:-)[0-9a-f]{64}",
            rf"\g<1>{sha256}",
            updated,
        )
    if updated != text:
        path.write_text(updated, encoding="utf-8")
        return True
    return False


def sync_doc_url(path: Path, tag: str, deb: str) -> bool:
    """Point release download URLs at the current tag/asset."""
    text = path.read_text(encoding="utf-8")
    updated, n = _sub_count(
        r"releases/download/[^/\s\"']+/" + DEB_RE,
        f"releases/download/{tag}/{deb}",
        text,
    )
    if n and updated != text:
        path.write_text(updated, encoding="utf-8")
        return True
    return False


def sync_doc_hashes(path: Path, sha256: str) -> bool:
    """Refresh hash pins on SHA lines only (never blind global replace)."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    out = []
    for line in lines:
        if re.search(r"SHA256|expected_sha256|output matches", line):
            new_line, _ = re.subn(HASH_RE, sha256, line)
            line = new_line
        out.append(line)
    updated = "".join(out)
    if updated != path.read_text(encoding="utf-8"):
        path.write_text(updated, encoding="utf-8")
        return True
    return False


def sync_doc_sizes(path: Path, size_bytes: int) -> bool:
    """Refresh `N bytes (about|~ M MiB)` phrases, keeping each qualifier."""
    text = path.read_text(encoding="utf-8")

    def repl(match: re.Match[str]) -> str:
        qualifier = "about " if "about " in match.group(0) else "~"
        return size_phrase(size_bytes, qualifier)

    updated, n = SIZE_PHRASE_RE.subn(repl, text)
    if n and updated != text:
        path.write_text(updated, encoding="utf-8")
        return True
    return False


def sync_doc_deb_names(path: Path, deb: str) -> bool:
    """Refresh `flutter_X.Y.Z[-rel]_aarch64.deb` mentions to the asset name."""
    text = path.read_text(encoding="utf-8")
    updated, n = re.subn(DEB_RE, deb, text)
    if n and updated != text:
        path.write_text(updated, encoding="utf-8")
        return True
    return False


def sync_pins(
    root: Path,
    sha256: str,
    size_bytes: int,
    tag: str,
    pkg_rel: str,
) -> list[str]:
    """Sync all pins under root. Returns sorted list of changed rel paths."""
    if not re.fullmatch(HASH_RE, sha256):
        raise SystemExit(f"refusing to sync malformed sha256: {sha256!r}")
    if size_bytes <= 0:
        raise SystemExit(f"refusing to sync non-positive size: {size_bytes!r}")
    deb = asset_name(tag, pkg_rel)
    changed: list[str] = []
    for rel in SCRIPT_FILES:
        path = root / rel
        if path.is_file() and sync_script(path, sha256):
            changed.append(rel)
    smoke = root / "scripts/device/run_termux_smoke.ps1"
    if smoke.is_file() and sync_doc_url(smoke, tag, deb):
        if "scripts/device/run_termux_smoke.ps1" not in changed:
            changed.append("scripts/device/run_termux_smoke.ps1")
    for rel in DOC_FILES:
        path = root / rel
        if not path.is_file():
            continue
        file_changed = False
        file_changed |= sync_doc_url(path, tag, deb)
        file_changed |= sync_doc_hashes(path, sha256)
        file_changed |= sync_doc_sizes(path, size_bytes)
        file_changed |= sync_doc_deb_names(path, deb)
        if file_changed and rel not in changed:
            changed.append(rel)
    return sorted(changed)


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync published release pins")
    parser.add_argument("--sha256", required=True, help="Published deb SHA256")
    parser.add_argument("--size", required=True, type=int, help="Published deb size in bytes")
    parser.add_argument("--tag", default="", help="Flutter tag (defaults to build.toml)")
    parser.add_argument("--pkg-rel", default="", help="Packaging revision (defaults to build.toml)")
    parser.add_argument("--root", default=".", help="Repository root")
    args = parser.parse_args()
    root = Path(args.root)
    file_tag, file_rel = repo_config(root)
    tag = args.tag or file_tag
    pkg_rel = args.pkg_rel or file_rel
    changed = sync_pins(root, args.sha256, args.size, tag, pkg_rel)
    for rel in changed:
        print(f"synced {rel}")
    if not changed:
        print("pins already in sync.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
