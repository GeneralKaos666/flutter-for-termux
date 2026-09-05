#!/usr/bin/env bash
# Termux Flutter automated build script
# Purpose: cross-compile the Flutter Engine for Termux and package it as .deb
# Usage: ./build_termux_flutter.sh

set -e

# =====================================================
# Configuration - modify according to your environment
# =====================================================
NDK_PATH="${NDK_PATH:-${ANDROID_NDK:-${ANDROID_NDK_HOME:-${ANDROID_NDK_ROOT}}}}"
BUILD_DIR="$(cd "$(dirname "$0")" && pwd)"
ARCH="arm64"
MODE="debug"

# =====================================================
# Main flow
# =====================================================

cd "$BUILD_DIR"

echo "=== Step 1: Ensure dependencies are installed ==="
pip3 install --user --break-system-packages gitpython pyyaml fire loguru 2>/dev/null || true

echo "=== Step 2: Assemble Termux Sysroot ==="
python3 build.py sysroot --arch=$ARCH

echo "=== Step 3: Configure GN ==="
python3 build.py configure --arch=$ARCH --mode=$MODE

echo "=== Step 4: Compile ==="
python3 build.py build --arch=$ARCH --mode=$MODE

echo "=== Step 5: Package .deb ==="
python3 build.py debuild --arch=$ARCH

echo "=== Done ==="
ls -lh $BUILD_DIR/flutter_*.deb

echo ""
echo "To install on Termux:"
echo "  dpkg -i flutter_*.deb"
