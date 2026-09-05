#!/bin/bash
# Flutter Termux build environment setup script
# Run this script in WSL Ubuntu

set -e

echo "=========================================="
echo "Flutter Termux build environment setup"
echo "=========================================="

# 1. Install build dependencies
echo "[1/4] Installing build dependencies..."
sudo apt update
sudo apt install -y \
	git python3 python3-pip python3-venv \
	ninja-build cmake clang pkg-config \
	libgtk-3-dev libglib2.0-dev \
	curl wget unzip zip xz-utils

# 2. Install Python dependencies
echo "[2/4] Installing Python dependencies..."
pip3 install gitpython fire pyyaml loguru tomli requests

# 3. Download Android NDK r27
echo "[3/4] Downloading Android NDK r27d..."
NDK_VERSION="r27d"
NDK_DIR="/opt/android-ndk-${NDK_VERSION}"

if [ -d "$NDK_DIR" ]; then
	echo "NDK already exists: $NDK_DIR"
else
	cd /tmp
	wget -q --show-progress https://dl.google.com/android/repository/android-ndk-${NDK_VERSION}-linux.zip
	echo "Extracting NDK..."
	sudo unzip -q android-ndk-${NDK_VERSION}-linux.zip -d /opt/
	rm android-ndk-${NDK_VERSION}-linux.zip
	echo "NDK installed: $NDK_DIR"
fi

# 4. Set environment variables
echo "[4/4] Setting environment variables..."
BASHRC_LINE="export ANDROID_NDK=${NDK_DIR}"
if ! grep -q "ANDROID_NDK" ~/.bashrc; then
	echo "$BASHRC_LINE" >>~/.bashrc
	echo "Added ANDROID_NDK to ~/.bashrc"
fi

export ANDROID_NDK="$NDK_DIR"

# Check clang version
CLANG_VERSION=$(ls ${NDK_DIR}/toolchains/llvm/prebuilt/linux-x86_64/lib/clang/ | head -1)
echo ""
echo "=========================================="
echo "Environment setup complete!"
echo "=========================================="
echo "NDK path: $NDK_DIR"
echo "Clang version: $CLANG_VERSION"
echo ""
echo "Run: source ~/.bashrc"
echo "Then you can start compiling!"
