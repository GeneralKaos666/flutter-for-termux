**English** | [中文](BUILD_GUIDE_ZH.md)

# Flutter Termux Complete Build Guide

This document explains how to build a Flutter deb package that includes Android gen_snapshot from scratch.

## Current Version Status (3.47.2 / 2026-08-15)

| Item | Value |
|------|----|
| Flutter tag | `3.47.2` |
| Engine revision | `5a2a6a42cce67f965cf540fcecf616faca624aa1` |
| Package | `flutter_3.47.2_aarch64.deb` |
| Package size | 666,366,556 bytes (about 636 MiB) |
| SHA256 | `4443a27c2f528cb093fedcdb994e9f3342147669699c5d695fd2da6d553b98e0` |
| Device smoke | Samsung SM-X716B / Android 16 / Termux |

3.47.2 introduces three new points that require special attention:

1. **Dart VM/tool split**: The Flutter CLI uses the Termux JIT `dart`, but engine snapshots still need the accompanying `dartvm` / `dartaotruntime`, so the package validator must check all three. The dart SDK is produced by the standard build's `dart_sdk_archive` target.
2. **Flutter Tools Android host**: On Termux, Dart reports `Platform.operatingSystem == "android"`, so the host artifact lookup must be mapped to Linux ARM64.
3. **Flutter Gradle plugin**: Flutter 3.44's `FlutterPlugin.kt` imports `PLATFORM_ABI_LIST` directly, so the post-install ARM64-only `FlutterPluginConstants.kt` template must keep this symbol, and the Gradle included-build cache must be cleaned.

`debuild` repackages the entire SDK (about 6-8 minutes) but does not recompile the engine; don't confuse its duration with that of the `ninja` build.

## CI/CD and Device Validation

The full engine build should still run on a WSL/self-hosted runner, but PRs can run lightweight checks first:

```bash
python -m py_compile build.py package.py sysroot.py utils.py scripts/ci/check_repo.py
bash -n scripts/install/post_install.sh scripts/test/gh_e2e_test.sh scripts/device/termux_smoke.sh
python scripts/ci/check_repo.py
git diff --check
```

GitHub Actions is currently split into four tracks:

- `.github/workflows/ci.yml`: GitHub-hosted sanity checks for PRs/pushes.
- `.github/workflows/build-deb.yml`: manual self-hosted Linux/WSL full `.deb` build, with optional release publish.
- `.github/workflows/device-smoke.yml`: manual self-hosted Windows + ADB tablet smoke test.
- `.github/workflows/release-check.yml`: Release asset metadata / SHA256 checks.

See [`docs/CI_CD.md`](../CI_CD.md) for details.

## Technical Architecture Overview

### Components We Compile (WSL cross-compilation)

| Component | File | Size | Purpose |
|------|------|------|------|
| **Dart CLI** | `dart-sdk/bin/dart` | ~50MB | Flutter CLI; post-install replaces it with Termux JIT Dart |
| **Dart VM** | `dart-sdk/bin/dartvm`, `dartaotruntime` | ~50MB | Runs engine snapshots / AOT runtime |
| **Flutter Engine** | `libflutter_linux_gtk.so` | ~106MB | Linux desktop runtime |
| **gen_snapshot (Linux)** | `linux-arm64/gen_snapshot` | ~30MB | `flutter build linux` |
| **gen_snapshot (Android)** | `android-arm64-release/.../gen_snapshot` | ~30MB | `flutter build apk` |
| **impellerc** | `impellerc` | ~20MB | Shader compiler |
| **const_finder** | `const_finder.dart.snapshot` | ~1MB | Icon tree shaking |

### Downloaded Precompiled Components (third-party)

| Source | Component | Purpose |
|------|------|------|
| [mumumusuc/termux-android-sdk](https://github.com/mumumusuc/termux-android-sdk) | aapt2, d8, build-tools | Android build tools |
| [lzhiyong/termux-ndk](https://github.com/lzhiyong/termux-ndk) | ARM64 NDK (clang, linker) | Native compilation |
| Google Storage | Dart snapshots (dds_aot, etc.) | Hot reload support |

### Applied Patches

| Patch file | Problem solved |
|----------|-----------|
| `patches/engine.patch` | Bionic TLS alignment, `-llog -lm` linking, dynamic linker path, NDK clang runtime detection |
| `patches/dart.patch` | Dart VM / profiler shutdown Termux adaptation |
| `post_install.sh` → `FlutterPluginConstants.kt` | Compile ARM64 only by default, and keep Flutter 3.44 `PLATFORM_ABI_LIST` |
| `post_install.sh` | NDK wrapper, sysroot symlinks, official snapshots, Flutter Tools Android-host patches, cache cleanup |

### Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         WSL Build Environment                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │ Dart SDK    │  │ Flutter     │  │ gen_snapshot            │  │
│  │ (ARM64)     │  │ Engine      │  │ ├─ Linux ARM64          │  │
│  │             │  │ (ARM64)     │  │ └─ Android ARM64        │  │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘  │
│         │               │                    │                   │
│         └───────────────┴────────────────────┘                   │
│                         │                                        │
│                    [Package deb]                                │
│                         │                                        │
└─────────────────────────┼────────────────────────────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                       Termux Runtime Environment               │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │ Our deb     │  │ Android SDK │  │ ARM64 NDK               │  │
│  │ (built)     │  │ (downloaded)│  │ (downloaded)            │  │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘  │
│         │               │                    │                   │
│         └───────────────┴────────────────────┘                   │
│                         │                                        │
│              [post_install.sh integration]                      │
│                         │                                        │
│                         ▼                                        │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │ flutter doctor ✅  flutter build apk ✅  flutter run ✅  │    │
│  └─────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
```

---

## Build Environment Requirements

- Windows 11 + WSL2 (Ubuntu 22.04+)
- At least 100GB of free disk space
- At least 16GB RAM
- Stable network connection

## Complete Build Process

### 1. Install WSL Dependencies

```bash
# Run in Ubuntu WSL
sudo apt update
sudo apt install -y git curl python3 python3-pip ninja-build pkg-config
pip3 install fire loguru toml pyyaml
```

### 2. Set Up depot_tools

```bash
cd ~
git clone https://chromium.googlesource.com/chromium/tools/depot_tools.git
export PATH="$HOME/depot_tools:$PATH"
echo 'export PATH="$HOME/depot_tools:$PATH"' >> ~/.bashrc
```

### 3. Clone the Project

```bash
mkdir -p ~/projects
cd ~/projects
git clone https://github.com/GeneralKaos666/flutter-for-termux.git termux-flutter
cd termux-flutter
```

### 4. Sync Flutter Engine Source

```bash
python3 build.py clone   # Clone Flutter
python3 build.py sync    # Sync dependencies (about 30GB, takes several hours)
```

### 5. Apply Patches

```bash
python3 build.py patch --file=./patches/engine.patch
```

### 6. Build Sysroot

```bash
python3 build.py sysroot --arch=arm64
```

### 7. One-Command Build (recommended)

```bash
# New: build all components with a single command
python3 build.py
```

This command automatically completes:
1. Configure the Linux debug build
2. Compile the Flutter engine
3. Compile the dart binary (critical!)
4. Configure the Android gen_snapshot build
5. Compile the Android gen_snapshot
6. Package the deb

Or build step-by-step manually:

```bash
# Linux debug (for flutter run -d linux)
python3 build.py configure --arch=arm64 --mode=debug
python3 build.py build --arch=arm64 --mode=debug

# Android gen_snapshot (for flutter build apk)

# Package the deb
python3 build.py debuild --arch=arm64
```

### 8. Output Files

After the build completes, the deb package is located at:
```
release/flutter_3.47.2_aarch64.deb
```

## deb Package Contents

| Component | Path | Purpose |
|------|------|------|
| Flutter SDK | /data/data/com.termux/files/usr/opt/flutter | Main program |
| dart binary | .../dart-sdk/bin/dart | Core of the flutter command |
| Linux gen_snapshot | .../engine/linux-arm64/gen_snapshot | flutter run -d linux |
| Android gen_snapshot | .../engine/android-arm64-release/linux-arm64/gen_snapshot | flutter build apk |

## Verify the Build

```bash
# Check all required files
ls -la flutter/engine/src/out/linux_debug_arm64/dart-sdk/bin/dart  # Critical!
ls -la flutter/engine/src/out/linux_debug_arm64/gen_snapshot
ls -la flutter/engine/src/out/android_release_arm64/clang_arm64/gen_snapshot
```

## Problem Analysis and Solution (2025-12-28)

### Original Problem
- `flutter run -d linux` ✓ works
- `flutter build apk --release` ✗ fails (dart version issue)

### Root Cause
**`ninja flutter` alone does not compile the dart binary!**

The `bin/cache/dart-sdk/bin/dart` binary must ship in the deb package, otherwise:
- The flutter command fails to execute properly
- gen_snapshot version mismatch errors

### Solution
The FFT `build()` ninja invocation includes `flutter/build/archives:dart_sdk_archive` (plus `flutter_patched_sdk` and `flutter_gtk`), so the dart binary is produced as part of the standard build:

```bash
python3 build.py build --arch=arm64 --mode=release
```

Or use the one-command build:

```bash
python3 build.py
```

### deb Package Contents Confirmation (after fix)
```
✓ bin/cache/dart-sdk/bin/dart (102MB)
✓ bin/cache/artifacts/engine/linux-arm64/gen_snapshot (6.9MB)
✓ bin/cache/artifacts/engine/android-arm64-release/linux-arm64/gen_snapshot (6.4MB)
```

## Common Problems

### Build fails: ninja error
Make sure the patches were applied correctly:
```bash
cd flutter/engine/src/flutter
git diff shell/platform/embedder/BUILD.gn
```

### Missing dependencies
```bash
python3 build.py sysroot --arch=arm64
```

### vpython3 not found
Make sure depot_tools is in the PATH:
```bash
export PATH="$HOME/depot_tools:$PATH"
```

### Insufficient disk space
The Flutter Engine source is about 30GB and the build output about 20GB, so you need at least 60GB of space.

## Termux Setup Before Use (3.47.2)

After installing the deb, run the following in Termux:

```bash
dpkg -i flutter_3.47.2_aarch64.deb
apt --fix-broken install -y
bash $PREFIX/share/flutter/post_install.sh
source $PREFIX/etc/profile.d/flutter.sh
flutter doctor -v
```

`post_install.sh` handles most of the things that had to be done manually in older build versions: Android API 34/36, cmdline-tools, build-tools symlinks, NDK clang wrappers, CMake host tag, Dart snapshots, Flutter Tools Android-host patches, Gradle plugin ARM64-only ABI, ELF cleaner, shebang fixes, and cache cleanup.

### Per-APK-Project Manual Setup Still Required

```bash
flutter create myapp
cd myapp
sed -i '1s|#!/usr/bin/env bash|#!/data/data/com.termux/files/usr/bin/bash|' android/gradlew
cat >> android/gradle.properties <<'EOF'
android.aapt2FromMavenOverride=/data/data/com.termux/files/usr/bin/aapt2
EOF
```

`android/app/build.gradle.kts`:

```kotlin
android {
    compileSdk = 34
    defaultConfig {
        targetSdk = 34
        ndk { abiFilters += listOf("arm64-v8a") }
    }
}
```

Build:

```bash
flutter build apk --release --target-platform android-arm64 --no-tree-shake-icons
```

### Per-Linux-Desktop-Project Manual Setup Still Required

```bash
flutter create mylinux --platforms=linux
cd mylinux
sed -i '1i set(CMAKE_SYSTEM_NAME Linux)' linux/CMakeLists.txt
flutter build linux --release
```

## Build Common Problems and Solutions (Pitfalls) 🔥

This section records the various problems and their solutions encountered during the build, to avoid repeating the same mistakes.

### 1. vpython3 not found (depot_tools issue)

**Problem description:**
```
/bin/sh: vpython3: not found
```

ninja cannot find vpython3 during compilation. This is because depot_tools' vpython3 is a symlink pointing to vpython, and vpython is also broken.

**Solution:**
Manually create a vpython3 wrapper script:

```bash
cd flutter/engine/src/flutter/third_party/depot_tools/.cipd_bin

# Delete the broken symlink
rm -f vpython3

# Create the wrapper script
cat > vpython3 << 'EOF'
#!/bin/bash
exec python3 "$@"
EOF

chmod +x vpython3
```

**Note:** If you are on Windows/WSL, make sure the script uses LF line endings, not CRLF:
```bash
# Fix the CRLF issue
cat vpython3 | tr -d '\r' > vpython3.tmp && mv vpython3.tmp vpython3
chmod +x vpython3
```

### 2. openjdk-17 does not exist (Termux package issue)

**Problem description:**
```
android-sdk depends on openjdk-17; however:
  Package openjdk-17 is not installed.
```

The android-sdk package depends on openjdk-17, but Termux only has openjdk-21.

**Solution:**
```bash
# Install openjdk-21
pkg install openjdk-21

# Force-configure android-sdk (ignore dependencies)
dpkg --force-depends --configure android-sdk
```

**Permanent fix:** package.yaml has been updated to depend on openjdk-21:
```yaml
Depends: git, which, gtk3, xorgproto, ninja, cmake, clang, pkg-config, openjdk-21
```

### 3. libflutter_linux_gtk.so missing (Linux desktop support)

**Problem description:**
```
flutter build linux --debug
Error: Could not find libflutter_linux_gtk.so
```

`flutter build linux` requires `libflutter_linux_gtk.so`, but the default build does not compile this target.

**Solution:**
Enable the flutter_gtk target in the `build()` method of `build.py`:

```python
cmd = [
    'ninja', '-C', utils.target_output(root, arch, mode),
    'flutter',
    # Must enable this line to build Linux desktop support
    'flutter/shell/platform/linux:flutter_gtk',
]
```

Then rebuild:
```bash
python3 build.py build --arch=arm64 --mode=debug
```

### 4. dartaotruntime missing

**Problem description:**
```
Error: dartaotruntime not found
```

`flutter build apk --release` requires dartaotruntime.

**Solution:**
```bash
# Copy dartaotruntime_product to dart-sdk/bin
cp flutter/engine/src/out/linux_debug_arm64/dartaotruntime_product \
   flutter/engine/src/out/linux_debug_arm64/dart-sdk/bin/dartaotruntime
```

### 5. CRLF line-ending issue (Windows/WSL)

**Problem description:**
```
C:/Program: No such file or directory
```

Shell scripts created on Windows may have CRLF line endings, causing execution to fail.

**Solution:**
```bash
# Convert to LF
cat script.sh | tr -d '\r' > script.tmp && mv script.tmp script.sh
chmod +x script.sh

# Or use dos2unix
dos2unix script.sh
```

### 6. ADB remote installation fails

**Problem description:**
Commands sent to Termux using `am broadcast` do not execute.

**Solution:**
External app execution permission must be enabled in Termux:

```bash
# Run in Termux
echo "allow-external-apps=true" >> ~/.termux/termux.properties
termux-reload-settings
```

Or just run the installation commands manually in Termux.

### 7. X11-related dependencies

**Problem description:**
`flutter run -d linux` requires an X11 environment.

**Solution:**
Install the X11 repo and related packages in Termux:
```bash
pkg install x11-repo
pkg install termux-x11-nightly

# Start Termux:X11
termux-x11 &

# Set DISPLAY
export DISPLAY=:0

# Then run the Flutter app
flutter run -d linux
```

### 8. gen_snapshot version mismatch

**Problem description:**
```
version differs from vm's version
```

The dart and gen_snapshot versions are inconsistent.

**Solution:**
Make sure to run the full pipeline so dart and gen_snapshot come from the same build:
```bash
python3 build.py
```

### 9. ninja: error: 'xxx' does not exist

**Problem description:**
Building immediately after configuring reports a file-does-not-exist error.

**Solution:**
Make sure the configuration completes before building:
```bash
# Configure first
python3 build.py configure --arch=arm64 --mode=debug

# Wait for configuration to finish, then build
python3 build.py build --arch=arm64 --mode=debug
```

### 10. TLS segment underaligned (Bionic linker issue)

**Problem description:**
```
error: "dart": executable's TLS segment is underaligned: alignment is 8 (skew 0), needs to be at least 64 for ARM64 Bionic
```

This error appears when running `flutter doctor` or any dart command on Termux.

**Cause:**
The dart binary was compiled using glibc's dynamic linker (`/lib/ld-linux-aarch64.so.1`) instead of Android Bionic's (`/system/bin/linker64`). Android Bionic requires the TLS (Thread Local Storage) segment to be aligned to 64 bytes.

**Solution:**
Add the bionic linker to the `executable_ldconfig` config in `build/config/termux/BUILD.gn`:

```gn
config("executable_ldconfig") {
  if (current_toolchain == "//build/toolchain/termux:${current_cpu}") {
    ldflags = [
      "-Bdynamic",
      "-Wl,-z,nocopyreloc",
      "-Wl,--dynamic-linker=/system/bin/linker64",  # Must add!
    ]
  } else {
    configs = ["//build/config/gcc:executable_ldconfig"]
  }
}
```

Then reconfigure and rebuild dart:
```bash
python3 build.py configure --arch=arm64 --mode=debug
ninja -C flutter/engine/src/out/linux_debug_arm64 exe.unstripped/dart -j24
```

---

## Test Verification Checklist

After the build completes, verify with the following checklist:

```bash
# 1. Check required files exist
ls -la flutter/engine/src/out/linux_debug_arm64/dart-sdk/bin/dart
ls -la flutter/engine/src/out/linux_debug_arm64/gen_snapshot
ls -la flutter/engine/src/out/linux_debug_arm64/libflutter_linux_gtk.so
ls -la flutter/engine/src/out/android_release_arm64/clang_arm64/gen_snapshot

# 2. After deploying to Termux (after running post_install.sh)
flutter doctor -v               # ✅ verified
flutter create test_app         # ✅ verified
cd test_app
flutter build apk --release     # ✅ verified
flutter build apk --debug       # ✅ verified
flutter build linux --debug     # ✅ verified (requires Termux:X11)
flutter run                     # ✅ verified (Hot Reload supported)
```

## Target Version Status (3.47.2)

### Feature Test Results (updated 2026-08-15)

| Feature | Status | Description |
|------|------|------|
| `flutter --version` | ✅ OK | Flutter 3.47.2 / Tools Dart 3.13.2 |
| `dart --version` | ✅ OK | Termux JIT Dart 3.13.2 (`android_arm64`) |
| `dartvm --version` | ✅ OK | post-install `dartvm` resolves to Dart 3.13.2 (`android_arm64`) |
| `flutter doctor -v` | ✅ OK | unknown channel / no device are expected warnings |
| `flutter create` | ✅ OK | Can create Android + Linux projects |
| `flutter build apk --release --target-platform android-arm64 --no-tree-shake-icons` | ✅ OK | Requires running post_install.sh; only supports android-arm64 |
| `flutter build linux --release` | ✅ OK | Produces an ARM64 ELF bundle |
| `flutter run` (Android) | ✅ Supported | A device is only shown after ADB connects within Termux |

### Known Limitations

#### 1. Android APK only supports ARM64

**Problem:** `flutter build apk --release` only supports the `android-arm64` platform.

**Cause:** We can only compile the ARM64 version of gen_snapshot:
- `android-arm` (32-bit): BoringSSL has a 32-bit shift overflow compile error
- `android-x64`: The ARM64 sysroot cannot be used for x64 cross-compilation

**Impact:** The built APK can only run on ARM64 devices and does not support ARM32 or x86 emulators.

**Usage:** Explicitly specify ARM64 to avoid the old cache or project settings being misdetected:
```bash
flutter build apk --release --target-platform android-arm64 --no-tree-shake-icons
```

#### 2. Debug vs Release mode mismatch (technical background)

The current deb package uses binaries built in **debug mode**:
- `dart` - debug mode
- `dartaotruntime` - debug mode
- `frontend_server_aot.dart.snapshot` - debug mode
- `gen_snapshot` - debug mode

This is because the release mode build hits sysroot conflicts in the WSL environment (glibc vs bionic headers).

**Impact on users:**
- `flutter doctor` ✅ runs normally
- `flutter build apk --release` ✅ runs normally (uses the android gen_snapshot)
- `flutter run -d linux` ⚠️ can only use debug mode

### Release mode build problems (developer reference)

If you try to build release mode, you may encounter:

#### sysroot header conflict
```
error: typedef redefinition with different types ('__mbstate_t' vs 'struct mbstate_t')
```

**Cause:** The sysroot contains both glibc and bionic headers:
- `/sysroot/usr/include/` - glibc headers
- `/sysroot/data/data/com.termux/files/usr/include/` - Termux/bionic headers

**Resolution direction:** The sysroot needs to be cleaned to keep only the bionic headers.

#### BoringSSL getrandom syscall issue
```
error: This system call is not available on Android
```

**Cause:** BoringSSL detects that the getrandom() syscall is unavailable.

**Resolution direction:** A `__ANDROID__` define needs to be added or BoringSSL needs to be patched.

---

## Termux APK Build Complete Setup Guide (3.47.2)

> **📌 Important: the runtime layer is handled automatically by `post_install.sh`; this section only lists the settings that must be kept in each Flutter project.**

### 1. Pin Android API / ABI

```kotlin
android {
    compileSdk = 34

    defaultConfig {
        targetSdk = 34
        ndk { abiFilters += listOf("arm64-v8a") }
    }
}
```

### 2. Specify the Termux aapt2

```properties
android.aapt2FromMavenOverride=/data/data/com.termux/files/usr/bin/aapt2
```

### 3. Fix the Gradle wrapper shebang

```bash
sed -i '1s|#!/usr/bin/env bash|#!/data/data/com.termux/files/usr/bin/bash|' android/gradlew
```

### 4. Build and verify

```bash
flutter build apk --release --target-platform android-arm64 --no-tree-shake-icons
ls -lh build/app/outputs/flutter-apk/app-release.apk
```

If you hit a Kotlin error from the Flutter 3.44 Gradle plugin (e.g. `PLATFORM_ABI_LIST` unresolved), the post-install template or the Gradle cache is stale:

```bash
bash $PREFIX/share/flutter/post_install.sh
./android/gradlew --stop || true
rm -rf ~/.gradle/caches .gradle android/.gradle build android/app/build
flutter build apk --release --target-platform android-arm64 --no-tree-shake-icons
```

### 5. Linux desktop build

```bash
sed -i '1i set(CMAKE_SYSTEM_NAME Linux)' linux/CMakeLists.txt
flutter build linux --release
```

## Upgrading the Flutter Version

When Flutter releases a new version, upgrade with the following steps:

### 1. Rebase the patches

```bash
# Patches live flat in patches/ (engine.patch, dart.patch, skia.patch)
python3 build.py patch --file=./patches/engine.patch
python3 build.py patch --file=./patches/dart.patch --path=engine/src/flutter/third_party/dart
python3 build.py patch --file=./patches/skia.patch --path=engine/src/flutter/third_party/skia
```

### 2. Update build.toml

```toml
[flutter]
tag = '<NEW_TAG>'  # Update the version number
```

### 3. Sync the new version

```bash
python3 build.py clone
python3 build.py sync  # This downloads the new version's engine
```

### 4. Try applying the patches

```bash
python3 build.py patch --file=./patches/engine.patch
python3 build.py patch --file=./patches/dart.patch --path=engine/src/flutter/third_party/dart
python3 build.py patch --file=./patches/skia.patch --path=engine/src/flutter/third_party/skia
```

**If a patch fails:**

1. Look at the error message and find where the conflict is
2. Manually fix the patch files in `patches/`
3. Re-run the patch command

### 5. Build the new version

```bash
python3 build.py sysroot --arch=arm64
python3 build.py
python3 build.py debuild --arch=arm64
```

### 6. Test

Test all functionality in a clean Termux environment:

```bash
# Install
dpkg -i flutter_<NEW_TAG>_aarch64.deb
apt-get install -f
bash $PREFIX/share/flutter/post_install.sh

# Test
flutter doctor -v
flutter create test_app && cd test_app
flutter build apk --release
flutter build linux
```

### 7. Release

```bash
# Commit changes
git add -A
git commit -m "feat: Support Flutter <NEW_TAG>"

# Tag
git tag -a v<NEW_TAG>-termux -m "Flutter <NEW_TAG> for Termux ARM64"
git push origin main --tags

# Create a GitHub Release
gh release create v<NEW_TAG>-termux \
  --title "Flutter <NEW_TAG> for Termux" \
  --notes "See docs/releases/CHANGELOG.md" \
  flutter_<NEW_TAG>_aarch64.deb
```

### Patch maintenance tips

1. **Keep patches minimal**: only modify what is necessary
2. **Add comments**: explain in the patch why the modification is needed
3. **Version isolation**: each Flutter version has its own patch directory
4. **Record changes**: update docs/releases/CHANGELOG.md

---

## Update Log

### 2026-06-01 v3.44.0
- ✅ Flutter 3.44.0 / Dart 3.12 deb packaged and passed the artifact validator
- ✅ Termux smoke: doctor / create / build apk / build linux
- ✅ Added the Flutter Gradle plugin `PLATFORM_ABI_LIST` and cache cleanup
- ✅ Added GitHub-hosted PR CI, self-hosted full build / tablet smoke workflows, and release metadata checks
- 🧹 Organized the GitHub Release E2E and device smoke scripts into `scripts/test/`, `scripts/device/`
- 📝 Reorganized the 3.44.0 status of README, docs/guides/INSTALL_GUIDE, docs/releases/RELEASE_NOTES, and docs/releases/CHANGELOG

### 2025-12-29 v5
- ✅ Full `flutter run` + Hot Reload support
- ✅ `post_install.sh` automatically downloads official Dart SDK snapshots (required for hot reload)
- ✅ `post_install.sh` automatically runs termux-elf-cleaner (fixes linker warnings)
- 📝 Fixed the NDK version and paths in the documentation
- 📝 Updated the feature status table

### 2025-12-29 v4
- ✅ `flutter build apk --release` fully working
- ✅ `flutter build apk --debug` fully working
- ✅ `flutter build apk --profile` fully working
- ✅ APK installs and runs correctly
- 📝 Fully documented all the steps required for APK builds

### 2025-12-29 v3
- ✅ `flutter doctor` fully working
- ✅ `flutter build apk --release` working (android-arm64 only)
- ⚠️ Documented the current debug/release mode limitation
- ⚠️ Documented the sysroot conflict issue for future fixes

### 2025-12-28 v2
- ✅ Old record: previously changed `flutter build apk --release` to default to ARM64; from 3.44.0 on it is still recommended to explicitly add `--target-platform android-arm64`
- ✅ Added flutter_gtk build support for `flutter build linux`
- ✅ Added common build problems and their solutions
- ✅ Updated the dependency from openjdk-17 to openjdk-21
