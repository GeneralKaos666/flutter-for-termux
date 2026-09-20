#!/data/data/com.termux/files/usr/bin/bash
# scripts/install/versions_common.sh
# Single source of truth for the release/toolchain versions used by the on-device
# Termux installers. check_repo.py asserts these agree with build.toml, and the
# autorelease workflow bumps them in lockstep, so keep every value here in sync
# with build.toml when cutting a release.

# Flutter release (matches [flutter] tag in build.toml)
export FLUTTER_VERSION="3.47.5"
export FLUTTER_PKG_REL="${FLUTTER_PKG_REL:-1}"
export RELEASE_TAG="3.47.5"
export EXPECTED_SHA256="6994580359002c6e0f6eb074d17a8ab3f9578e480e2aad83aa443474da3c9800"
export FLUTTER_DEB_NAME="flutter_${FLUTTER_VERSION}-${FLUTTER_PKG_REL}_aarch64.deb"

# Android SDK (mumumusuc/termux-android-sdk apt package, release tag below)
export ANDROID_SDK_VERSION="${ANDROID_SDK_VERSION:-35.0.0}"
export ANDROID_SDK_DEB_URL="https://github.com/mumumusuc/termux-android-sdk/releases/download/${ANDROID_SDK_VERSION}/android-sdk_${ANDROID_SDK_VERSION}_aarch64.deb"
export ANDROID_SDK_EXPECTED_SHA256="fc727c848b8ca4e3011515850702adc1bf98ceae7205d7acc82d026bc94d2601"

# Android NDK (lzhiyong/termux-ndk; matches [ndk] version in build.toml)
export NDK_VERSION="29.0.14206865"
export NDK_ARCHIVE_URL="https://github.com/lzhiyong/termux-ndk/releases/download/android-ndk/android-ndk-r29-aarch64.tar.xz"
export NDK_EXPECTED_SHA256="02e10e4ddfe8deaeb0bd0cf29d04c981ed5bc8a5d6b560ebb9e7661f472d684b"

# compileSdk/targetSdk for `flutter build apk` (Stage-B winner, matches
# [android] in build.toml). TERMUX_COMPILE_SDK/TERMUX_TARGET_SDK override at run
# time so a device can fall back through the 36 → 35 → 34 ladder.
export COMPILE_SDK="${TERMUX_COMPILE_SDK:-36}"
export TARGET_SDK="${TERMUX_TARGET_SDK:-36}"

# cmdline-tools build id (dl.google.com/android/repository commandlinetools-linux-<id>_latest.zip)
export CMDLINE_TOOLS_ID="${CMDLINE_TOOLS_ID:-11076708}"

# Termux package names ([installer] in build.toml)
export JAVA_PACKAGE="${JAVA_PACKAGE:-openjdk-21}"
export ZIP_TOOL="${ZIP_TOOL:-7zip}"

# Dart SDK snapshots archive (hot reload / flutter run) checksum
export SNAPSHOT_EXPECTED_SHA256="527f074d86660fd3f7c900fc8c1ebd5a2ebc4581e174eb8cf9fe343a1664402d"
