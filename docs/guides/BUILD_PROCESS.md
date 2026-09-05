**English** | [中文](BUILD_PROCESS_ZH.md)

# Flutter Termux Build Process Document

This document records in detail the complete process of building Flutter for Termux, to serve as a reference for future version upgrades.

## Table of Contents

1. [Build Environment](#build-environment)
2. [Complete Build Process](#complete-build-process)
3. [Key Modification Notes](#key-modification-notes)
4. [Known Issues and Solutions](#known-issues-and-solutions)
5. [Testing Process](#testing-process)
6. [Release Process](#release-process)

---

## Build Environment

### WSL Environment Requirements

```
OS: Windows 11 + WSL2 (Ubuntu 22.04+)
RAM: 16GB+ (32GB recommended)
Disk: 100GB+ of free space
CPU: Multi-core recommended (build uses -j24)
```

### Required Tools

```bash
# Ubuntu packages
sudo apt update
sudo apt install -y git curl python3 python3-pip ninja-build pkg-config

# Python packages
pip3 install -r requirements.txt

# depot_tools
cd ~
git clone https://chromium.googlesource.com/chromium/tools/depot_tools.git
export PATH="$HOME/depot_tools:$PATH"
echo 'export PATH="$HOME/depot_tools:$PATH"' >> ~/.bashrc
```

---

## Complete Build Process

### Stage 1: Sync Source Code

```bash
cd ~/projects/termux-flutter

# Clone Flutter (if this is a new environment)
python3 build.py clone

# Sync dependencies (~30GB, takes several hours)
python3 build.py sync
```

### Stage 2: Apply Patches

```bash
# Apply Termux adaptation patches
python3 build.py patch --file=./patches/engine.patch
python3 build.py patch --file=./patches/dart.patch --path=engine/src/flutter/third_party/dart
python3 build.py patch --file=./patches/skia.patch --path=engine/src/flutter/third_party/skia
```

The patch files are located in the `patches/` directory; the main changes:
- `patches/engine.patch` - Flutter Engine's Termux toolchain configuration
- `patches/dart.patch` - Dart SDK / VM Termux adaptation
- `patches/skia.patch` - Skia Android/bionic build adaptation

ARM64-only APK enforcement is done at install time by `post_install.sh`, not at build time.

### Stage 3: Build Sysroot

```bash
# Assemble Termux runtime dependencies
python3 build.py sysroot --arch=arm64
```

This downloads the required library files from the official Termux repo.

### Stage 4: Build Linux Components

```bash
# Configure the build
python3 build.py configure --arch=arm64 --mode=debug

# Build the Flutter Engine
python3 build.py build --arch=arm64 --mode=debug --jobs=24
```

**Important**: The `build()` ninja invocation includes `flutter/build/archives:dart_sdk_archive`, so the dart binary is produced as part of the standard build (no separate build step).

### Stage 5: Build Android gen_snapshot (Optional, for APK Builds)

> Standard builds ship gen_snapshot in the deb artifacts; no separate Android configure/build step is required.

### Stage 6: Package the deb

```bash
python3 build.py debuild --arch=arm64
```

Output: `release/flutter_3.47.2_aarch64.deb`

---

## Key Modification Notes

### 1. Termux Toolchain Configuration

Location: `patches/engine.patch` → `build/config/termux/BUILD.gn`

```gn
config("compiler") {
  # Use the Android Bionic target triple
  cflags += [ "--target=aarch64-linux-android${termux_api_level}" ]
  ldflags += [ "--target=aarch64-linux-android${termux_api_level}" ]
}

config("executable_ldconfig") {
  # Must use the Bionic linker, otherwise TLS alignment errors occur
  ldflags = [
    "-Bdynamic",
    "-Wl,-z,nocopyreloc",
    "-Wl,--dynamic-linker=/system/bin/linker64",  # critical!
  ]
}
```

### 2. Bionic Linker Issue

**Problem**: When the dart binary uses the glibc linker, the following appears:
```
error: "dart": executable's TLS segment is underaligned
```

**Solution**: Add `--dynamic-linker=/system/bin/linker64` to `executable_ldconfig`

### 3. Dependencies Issue

**Problem**: `-llog` and `-lm` cannot be found at link time

**Solution**: Add the following to the `runtime_library` configuration:
```gn
ldflags = [
  "-stdlib=libstdc++",
  "-Wl,--warn-shared-textrel",
  "-llog",   # Android logging library
  "-lm",     # math library
]
```

---

## Known Issues and Solutions

### Issue 1: vpython3 not found

**Symptoms**:
```
/bin/sh: vpython3: not found
```

**Solution**:
```bash
cd flutter/engine/src/flutter/third_party/depot_tools/.cipd_bin
rm -f vpython3
cat > vpython3 << 'EOF'
#!/bin/bash
exec python3 "$@"
EOF
chmod +x vpython3
```

### Issue 2: Release Mode Build Failure (sysroot Conflict)

**Symptoms**:
```
error: typedef redefinition with different types ('__mbstate_t' vs 'struct mbstate_t')
```

**Cause**: The sysroot contains both glibc and bionic headers

**Current Status**: Building in debug mode is used as a workaround

**Future Fix Direction**: Clean up the sysroot and keep only the bionic headers

### Issue 3: flutter build apk Failure (dedup_instructions Error)

**Symptoms**:
```
Flag dedup_instructions is false in snapshot, but dedup_instructions is always true in product mode
```

**Cause**:
- `dartaotruntime_product` expects a product-mode snapshot
- `frontend_server_aot.dart.snapshot` was built in debug mode

**Future Fix Direction**: Need to build a release-mode `frontend_server_aot.dart.snapshot`

### Issue 4: android-arm/android-x64 gen_snapshot Cannot Be Built

**Cause**:
- android-arm: BoringSSL has a 32-bit shift overflow error
- android-x64: The ARM64 sysroot is incompatible with x64 compilation

**Conclusion**: Only the android-arm64 target is supported

---

## Testing Process

### Test Device Preparation

1. ARM64 Android device (Android 11+)
2. Install Termux (from F-Droid)
3. Ensure there is an SSH or ADB connection

### Test Steps

```bash
# 1. Transfer the deb to the device
# Use PowerShell (Git Bash will corrupt the path)
adb push flutter_3.47.2_aarch64.deb /sdcard/Download/

# 2. Install it in Termux
pkg install x11-repo
dpkg -i /sdcard/Download/flutter_3.47.2_aarch64.deb
bash $PREFIX/share/flutter/post_install.sh
apt-get install -f

# 3. Load the environment
source /data/data/com.termux/files/usr/etc/profile.d/flutter.sh

# 4. Test flutter doctor
flutter doctor -v

# 5. Test creating a project
flutter create testapp
cd testapp

# 6. Test builds (may have issues)
flutter build apk --release --target-platform android-arm64 --no-tree-shake-icons
flutter build linux --release
```

### Test Result Record

| Test Item | Expected Result | Actual Result | Notes |
|----------|----------------|---------------|-------|
| flutter doctor | Shows version info | | |
| flutter create | Successfully creates a project | | |
| flutter build apk | Successfully builds an APK | | |
| flutter build linux | Successfully builds | | |

---

## Release Process

### 1. Version Check

Confirm that the version numbers in the following files are consistent:
- `build.toml` - `tag` field
- `install_flutter_complete.sh` - `FLUTTER_VERSION`
- `scripts/install/install_termux_flutter.sh` - `FLUTTER_VERSION`
- `README.md` - version badge and text (English primary README; `README_ZH.md` is the Chinese translation)
- `package.yaml` - `Version: $tag`

### 2. Build Artifact

```bash
# Final artifact location
release/flutter_3.47.2_aarch64.deb
```

### 3. Upload to GitHub Releases

1. Create a new Release: `3.47.2`
2. Upload the deb file: `flutter_3.47.2_aarch64.deb`
3. Fill in the Release Notes

### 4. Verify the One-Click Install Script

Test in a new Termux environment:
```bash
curl -sL https://raw.githubusercontent.com/GeneralKaos666/flutter-for-termux/main/install_flutter_complete.sh -o ~/install.sh && bash ~/install.sh
```

---

## Version Upgrade Guide

When Flutter releases a new version:

### 1. Update Version Configuration

Edit `build.toml`:
```toml
tag = "3.36.0"  # new version number
```

### 2. Sync Source Code

```bash
python3 build.py sync
```

### 3. Re-apply Patches

```bash
# You may need to update the patches to adapt to the new version
python3 build.py patch --file=./patches/engine.patch
```

If patch application fails, you need to manually update `patches/engine.patch`:
1. Review the conflicts
2. Resolve them manually
3. Regenerate the patch

### 4. Full Build and Test

Build and test by following the process described above.

---

## File Structure Reference

```
flutter-for-termux/
├── .github/workflows/    # CI / self-hosted build and device smoke workflows
├── docs/CI_CD.md         # CI/CD and device-lab guide
├── scripts/
│   ├── ci/               # lightweight contract checks
│   ├── device/           # ADB → Termux smoke automation
│   ├── install/          # install and post-install patches
│   └── test/             # Release E2E smoke scripts
├── patches/ # Flutter Engine / Dart / Skia patches
├── build.py              # main build script
├── build.toml            # build configuration (version, etc.)
├── package.yaml          # deb package definition
├── sysroot/              # Termux runtime deps (generated at build)
├── flutter/              # Flutter source (cloned at build)
│   └── engine/src/out/   # build artifacts
└── release/              # final deb package
```

---

## Update Log

### 2026-06-01
- Updated this document to the Flutter 3.47.2 / Dart 3.12 state
- Added `scripts/ci`, `scripts/device`, GitHub Actions and release metadata check
- Changed the testing process to ARM64 APK + Linux release smoke

### 2025-12-29
- Created this document
- Recorded debug/release mode issues
- Recorded the sysroot conflict issue

### 2025-12-28
- First successful build of Flutter 3.35.0
- Resolved the TLS alignment issue
- Resolved the -llog/-lm dependency issue
