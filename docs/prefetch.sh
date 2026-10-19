#!/data/data/com.termux/files/usr/bin/bash
# Prefetch the flutter .deb into apt's archive cache.
#
# The published apt repository (https://generalkaos666.github.io/flutter-for-termux)
# serves metadata only; the actual .deb lives on a GitHub release. apt appends
# the Packages "Filename" field to the repository URL, so it cannot reach the
# release asset itself. Running this script once per version places the .deb
# exactly where apt looks for it (size + sha256 verified), after which a plain
# `apt install flutter` works without fetching the .deb over the network.
#
# Usage:
#   bash prefetch.sh
#   curl -fsSL https://generalkaos666.github.io/flutter-for-termux/prefetch.sh | bash

set -euo pipefail

PKG=flutter
PAGES_URL=https://generalkaos666.github.io/flutter-for-termux
RELEASE_DL=https://github.com/GeneralKaos666/flutter-for-termux/releases/download

die() { echo "prefetch: ERROR: $*" >&2; exit 1; }

command -v curl >/dev/null 2>&1 || die "curl is required (pkg install curl)"
command -v apt-cache >/dev/null 2>&1 || die "apt not found — this script is for Termux"

# --- candidate version from the local apt lists ----------------------------
candidate=$(apt-cache policy "$PKG" 2>/dev/null | awk '/Candidate:/{print $2}')
[ -n "${candidate:-}" ] && [ "$candidate" != "(none)" ] || \
    die "no candidate version for $PKG — run 'apt update' first"

# --- metadata record published by this repository --------------------------
record=$(apt-cache show "$PKG=$candidate" 2>/dev/null) || die "no metadata for $PKG=$candidate"
block=$(awk -v RS='' '/^Filename: dists\//' <<<"$record")
[ -n "$block" ] || block=$(awk -v RS='' 'NR==1' <<<"$record")

filename=$(awk '/^Filename:/{print $2; exit}' <<<"$block")
size=$(awk '/^Size:/{print $2; exit}' <<<"$block")
sha256=$(awk '/^SHA256:/{print $2; exit}' <<<"$block")
arch=$(awk '/^Architecture:/{print $2; exit}' <<<"$block")
[ -n "$filename" ] && [ -n "$size" ] && [ -n "$sha256" ] || die "incomplete metadata for $PKG=$candidate"

# --- where apt will look for the file (apt's own naming convention) --------
store=$(apt-get download --print-uris "$PKG=$candidate" 2>/dev/null | awk '{print $2; exit}')
[ -n "${store:-}" ] || store="${PKG}_${candidate}_${arch}.deb"

# apt's Dir::Cache::archives (Termux resolves this to
# /data/data/com.termux/cache/apt/archives — NOT $PREFIX/var/cache/apt)
cache=$(apt-config dump | awk -F'"' '
    function join(base, rel) {
        if (substr(base, length(base), 1) != "/") base = base "/"
        return base rel
    }
    /^Dir "/        {root=$2}
    /^Dir::Cache "/ {c=$2}
    /^Dir::Cache::archives "/ {a=$2}
    END {
        cd = (c ~ /^\//) ? c : join(root, c)
        ad = (a ~ /^\//) ? a : join(cd, a)
        print ad
    }')
[ -n "$cache" ] || die "could not resolve apt archive cache directory"
mkdir -p "${cache}partial"

dest="${cache}${store}"

# --- reuse an already-prefetched file --------------------------------------
if [ -f "$dest" ]; then
    actual_size=$(stat -c %s "$dest")
    if [ "$actual_size" = "$size" ] && [ "$(sha256sum "$dest" | awk '{print $1}')" = "$sha256" ]; then
        echo "prefetch: OK — $store already in apt cache (verified)."
        echo "prefetch: next: apt install $PKG"
        exit 0
    fi
    echo "prefetch: existing $store is stale/corrupt — re-downloading"
    rm -f "$dest"
fi

# --- locate the release asset (tag = version minus deb-revision suffix) ----
tag=$(sed -E 's/-[0-9]+$//' <<<"$candidate")
asset=$(basename "$filename")
url="$RELEASE_DL/$tag/$asset"

echo "prefetch: $PKG $candidate"
echo "prefetch: downloading $url"
curl -fL --retry 3 --retry-delay 2 -o "${dest}.part" "$url" || {
    rm -f "${dest}.part"
    die "download failed: $url
  (if this 404s, the release for tag '$tag' may be missing asset '$asset')"
}
mv "${dest}.part" "$dest"

# --- verify ----------------------------------------------------------------
actual_size=$(stat -c %s "$dest")
[ "$actual_size" = "$size" ] || { rm -f "$dest"; die "size mismatch: got $actual_size, apt metadata says $size"; }
actual_sha=$(sha256sum "$dest" | awk '{print $1}')
[ "$actual_sha" = "$sha256" ] || { rm -f "$dest"; die "sha256 mismatch: got $actual_sha"; }

echo "prefetch: OK — $store verified (size + sha256) in $cache"
echo "prefetch: next: apt install $PKG"
