#!/data/data/com.termux/files/usr/bin/bash
# scripts/install/versions_common.sh
# Single source of truth for the release/toolchain versions used by the on-device
# Termux installers. scripts/ci/generate_versions.py --check asserts these agree
# with build.toml, and the autorelease workflow bumps them in lockstep, so keep
# every value here in sync with build.toml when cutting a release.

# Flutter release (matches [flutter] tag in build.toml)
export FLUTTER_VERSION="3.47.9"
export FLUTTER_PKG_REL="${FLUTTER_PKG_REL:-1}"
export RELEASE_TAG="3.47.9"
export EXPECTED_SHA256="eac81132f6060f4691cba05b86d445de79abb247ef8dd9aecf7e303f3e872474"
export FLUTTER_DEB_NAME="flutter_${FLUTTER_VERSION}-${FLUTTER_PKG_REL}_aarch64.deb"

# Android NDK (HomuHomu833/android-ndk-custom; r30)
# The SDK has no archive pin: post_install.sh assembles it from Termux
# packages (aapt2, d8, aidl, apksigner). An older HomuHomu833 SDK tarball pin
# lived here but had no remaining consumer and was removed.
export NDK_VERSION="30.0.14206865"
export NDK_ARCHIVE_URL="https://github.com/HomuHomu833/android-ndk-custom/releases/download/r30/android-ndk-r30-aarch64-linux-android.tar.xz"
export NDK_EXPECTED_SHA256="aa443113433cf8a3a4a97524a94041972d1ccbb72897adc4aa48c13ae627509d"

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
