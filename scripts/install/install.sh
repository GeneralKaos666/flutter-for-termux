#!/data/data/com.termux/files/usr/bin/bash
# Flutter for Termux ARM64 - One-click installer
# https://github.com/GeneralKaos666/flutter-for-termux
#
# Downloads the release deb (SHA256-verified), installs it, then provisions
# the Android SDK and the NDK through the deb-shipped post_install.sh.
# The aarch64 NDK repack comes from HomuHomu833
# (https://github.com/HomuHomu833/android-ndk-custom) - credit: HomuHomu833.
#
# Usage: curl -sL https://raw.githubusercontent.com/GeneralKaos666/flutter-for-termux/main/scripts/install/install.sh -o ~/install.sh && bash ~/install.sh

set -euo pipefail

source "$(dirname "$0")/lib_common.sh" || {
    echo "Fetching lib_common.sh..."
    curl -sLO https://raw.githubusercontent.com/GeneralKaos666/flutter-for-termux/main/scripts/install/lib_common.sh
    source ./lib_common.sh
}

parse_installer_args "$@"

trap print_summary EXIT
FLUTTER_DEB_NAME="${FLUTTER_DEB_NAME:-flutter_${FLUTTER_VERSION}-${FLUTTER_PKG_REL:-1}_aarch64.deb}"
DEB_URL="https://github.com/GeneralKaos666/flutter-for-termux/releases/download/${RELEASE_TAG}/${FLUTTER_DEB_NAME}"

echo "========================================"
echo "Flutter ${FLUTTER_VERSION} for Termux ARM64"
echo "========================================"
echo ""

# A full install needs the deb (~606 MiB), the extracted SDK, and the NDK
# (~550 MiB compressed, ~1.6 GB extracted): the guide's floor is 5 GB free.
preflight_check 5000000

# Prerequisites the deb cannot pull itself: x11-repo (source of its gtk3 and
# friends) and a JDK (deliberately not in the deb's Depends).
echo "[1/5] Installing prerequisites..."
pkg install -y x11-repo "${JAVA_PACKAGE:-openjdk-21}"

# Download deb
echo "[2/5] Downloading ${FLUTTER_DEB_NAME}..."

WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"; print_summary' EXIT
cd "$WORK_DIR"
curl -L -o flutter.deb "$DEB_URL" || { record_stage download failed; exit 20; }
record_stage download success

echo "Verifying SHA256 checksum..."
verify_sha256 flutter.deb "$EXPECTED_SHA256" || { record_stage integrity failed; exit 30; }
record_stage integrity success

# Install deb
echo "[3/5] Installing deb package..."
apt-get install -f -y ./flutter.deb || { record_stage package failed; exit 40; }
record_stage package success

# Run post-install script (apply mode: SDK staging + patches), then the
# opt-in NDK provisioning. post_install.sh --provision-ndk is a standalone
# mode that exits after installing the pinned HomuHomu833 aarch64 NDK repack,
# so both calls are required for a complete SDK + NDK setup.
echo "[4/5] Running post-install configuration (SDK + NDK)..."
if [ -f "$PREFIX/share/flutter/post_install.sh" ]; then
    bash "$PREFIX/share/flutter/post_install.sh" || { record_stage post-install failed; exit 50; }
    record_stage post-install success
    bash "$PREFIX/share/flutter/post_install.sh" --provision-ndk || { record_stage ndk-provision failed; exit 55; }
    record_stage ndk-provision success
else
    echo "Warning: post_install.sh not found"
fi

# Source profile
echo "[5/5] Setting up environment..."
[ -f "$PREFIX/etc/profile.d/flutter.sh" ] && source "$PREFIX/etc/profile.d/flutter.sh" 2>/dev/null || true


echo ""
echo "========================================"
echo "Installation complete!"
echo "========================================"
echo ""
echo "To start using Flutter:"
echo "  source \$PREFIX/etc/profile.d/flutter.sh"
echo "  flutter doctor"
echo ""
echo "To create a new project:"
echo "  flutter create myapp && cd myapp"
echo ""
echo "To build APK:"
echo "  flutter build apk --release"
echo ""
