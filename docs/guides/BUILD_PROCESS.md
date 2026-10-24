# Build Process Internals — How the Termux Build Works

This document explains **why** the Flutter-for-Termux build is put together
the way it is: the pipeline stages, the patches, the GN toolchain
configuration, the sysroot, and the known issues behind them. It is a
reference for future version upgrades.

For copy-pasteable build commands, use the [Build guide](BUILD_GUIDE.md);
for bumping to a new Flutter release, use the
[Upgrade guide](UPGRADE_GUIDE.md).

## Table of Contents

1. [Build Environment](#build-environment)
2. [Pipeline Overview](#pipeline-overview)
3. [Architecture](#architecture)
4. [Key Modification Notes](#key-modification-notes)
5. [Known Issues and Solutions](#known-issues-and-solutions)
6. [Testing Process](#testing-process)
7. [Release Process](#release-process)
8. [File Structure Reference](#file-structure-reference)

---

## Build Environment

The concrete setup steps live in the
[Build guide §1-3](BUILD_GUIDE.md#1-build-environment-requirements). The
constraints they exist to satisfy:

```
OS: Windows 11 + WSL2 (Ubuntu 22.04+), or any Linux x86-64 host
RAM: 16GB+ (32GB recommended)
Disk: 100GB+ of free space
CPU: Multi-core recommended (build uses -j24; override with --jobs=N)
```

Required toolchain roles:

- **Ubuntu packages** (`git curl python3 python3-pip ninja-build pkg-config`) —
  host basics.
- **Python packages** (`pip3 install -r requirements.txt`, equivalent to the
  per-package list in the Build guide) — `build.py` and its tooling.
- **depot_tools** — provides `gclient` / `vpython3` for the engine source
  sync.
- **`dpkg` and `ar`** — `sysroot.py` runs `dpkg -x`; `package.py` runs
  `ar rc` when assembling the deb.

---

## Pipeline Overview

Each stage maps to a step in the [Build guide](BUILD_GUIDE.md#3-step-by-step-build):

| Stage | Command | Notes |
|-------|---------|-------|
| 1. Sync source | `build.py clone` then `build.py sync` | ~30GB, several hours. The `.gclient` `custom_hooks` apply the patches automatically — no separate patch step |
| 2. Patches | (automatic during `sync`) | Manual `build.py patch --file=...` only to re-test/rebase against a fresh checkout |
| 3. Sysroot | `build.py sysroot --arch=arm64` | Downloads Termux runtime libraries from the official Termux apt repo |
| 4. Build Linux components | `build.py configure --arch=arm64 --mode=release` + `build.py build --arch=arm64 --mode=release` | The `build()` ninja invocation includes `flutter/build/archives:dart_sdk_archive`, so the dart binary is produced as part of the standard build (no separate build step) |
| 5. Android gen_snapshot | (none) | Standard builds ship gen_snapshot in the deb artifacts; no separate Android configure/build step is required |
| 6. Package the deb | `build.py debuild --arch=arm64` | Output: `release/flutter_3.47.8-6_aarch64.deb` |

---

## Architecture

### Build-to-device flow

```
┌─────────────────────────────────────────────────────────────────┐
│                         WSL Build Environment                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │ Dart SDK    │  │ Flutter     │  │ gen_snapshot            │  │
│  │ (ARM64)     │  │ Engine      │  │ (Linux ARM64, reused    │  │
│  │             │  │ (ARM64)     │  │  on-device for Android) │  │
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

### Components We Compile (WSL cross-compilation)

| Component | File | Size | Purpose |
|------|------|------|------|
| **Dart CLI** | `dart-sdk/bin/dart` | ~50MB | Flutter CLI; post-install replaces it with Termux JIT Dart |
| **Dart VM** | `dart-sdk/bin/dartvm`, `dartaotruntime` | ~50MB | Runs engine snapshots / AOT runtime |
| **Flutter Engine** | `libflutter_linux_gtk.so` | ~106MB | Linux desktop runtime |
| **gen_snapshot** | `linux-arm64/gen_snapshot` | ~30MB | `flutter build linux` directly; `flutter build apk` via the on-device `post_install.sh` symlink |
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
| `patches/skia.patch` | Skia Android/bionic build adaptation |
| `post_install.sh` → `FlutterPluginConstants.kt` | Compile ARM64 only by default, and keep Flutter 3.44 `PLATFORM_ABI_LIST` |
| `post_install.sh` | NDK wrapper, sysroot symlinks, official snapshots, Flutter Tools Android-host patches, cache cleanup |

ARM64-only APK enforcement is done at install time by `post_install.sh`,
not at build time.

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

```text
error: "dart": executable's TLS segment is underaligned
```

**Solution**: Add `--dynamic-linker=/system/bin/linker64` to
`executable_ldconfig` (see above). Full symptom/fix details, including the
rebuild commands, are in the
[Build guide troubleshooting](BUILD_GUIDE.md#tls-segment-underaligned-bionic-linker-issue).

### 3. Dependencies Issue

**Problem**: `-llog` and `-lm` cannot be found at link time.

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

Each entry below records the underlying cause and fix direction; matching
symptom/fix entries live in the
[Build guide troubleshooting](BUILD_GUIDE.md#7-troubleshooting).

### Issue 1: vpython3 not found

**Symptoms**:

```text
/bin/sh: vpython3: not found
```

**Solution**: depot_tools' `vpython3` is a symlink pointing to a broken
`vpython`; replace it with a wrapper script — see
[vpython3 not found](BUILD_GUIDE.md#vpython3-not-found-depot_tools-issue).

### Issue 2: Release Mode Build Failure (sysroot Conflict)

**Symptoms**:

```text
error: typedef redefinition with different types ('__mbstate_t' vs 'struct mbstate_t')
```

**Cause**: The sysroot contains both glibc and bionic headers

**Current Status**: Release mode is the shipped default —
`build.toml [build] runtime = ['release']`, so the pipeline builds and
packages release artifacts; debug/profile are opt-in via `--mode`

**Future Fix Direction**: Clean up the sysroot and keep only the bionic headers

### Issue 3: flutter build apk Failure (dedup_instructions Error)

**Symptoms**:

```text
Flag dedup_instructions is false in snapshot, but dedup_instructions is always true in product mode
```

**Cause**:

- `dartaotruntime_product` expects a product-mode snapshot
- `frontend_server_aot.dart.snapshot` was built in debug mode

**Future Fix Direction**: Need to build a release-mode
`frontend_server_aot.dart.snapshot`

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
adb push flutter_3.47.8-6_aarch64.deb /sdcard/Download/

# 2. Install it in Termux
pkg install x11-repo
dpkg -i /sdcard/Download/flutter_3.47.8-6_aarch64.deb
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
- `scripts/install/versions_common.sh` - installer version pins (`FLUTTER_VERSION`, checksums)
- `README.md` - version badge and text
- `package.yaml` - `Version: $package_version`

> CI enforces this: `scripts/ci/check_version_drift.py` and
> `scripts/ci/check_repo.py` fail the build on drift, and
> `scripts/ci/generate_versions.py --check` requires `versions_common.sh` to
> match `build.toml`.

### 2. Build Artifact

```bash
# Final artifact location
release/flutter_3.47.8-6_aarch64.deb
```

### 3. Upload to GitHub Releases

1. Create a new Release: `3.47.8`
2. Upload the deb file: `flutter_3.47.8-6_aarch64.deb`
3. Fill in the Release Notes

### 4. Verify the Release

Install the published release in a fresh Termux environment following the
[Install guide](INSTALL_GUIDE.md) (apt repository method), then build a debug
APK to confirm the toolchain end-to-end.

For upgrading to a new Flutter version, follow the
[Upgrade guide](UPGRADE_GUIDE.md).

### 5. Publish the apt Repository

`.github/workflows/apt-repo.yml` republishes the signed stub package and apt
metadata on GitHub Pages whenever the `build` workflow completes on `main`
(plus manual dispatch: `gh workflow run apt-repo.yml`). A release guard skips
the job unless the `build.toml` tag release carries the payload `.deb`.
One-time setup: Pages → Source = "GitHub Actions" and the
`APT_REPO_SIGNING_KEY` / `APT_REPO_SIGNING_PASSPHRASE` secrets.

---

## File Structure Reference

```
flutter-for-termux/
├── .github/workflows/    # GitHub-hosted + self-hosted build / CI / device smoke workflows
├── build.py              # Main CLI (Python Fire)
├── build.toml            # All config: version, NDK, sync paths, sysroot package lists
├── package.py            # .deb packaging logic
├── package.yaml          # Declarative: build output → deb install paths
├── sysroot.py            # Downloads Termux apt packages to assemble the sysroot
├── utils.py              # arch mapping, output paths
├── .gclient              # gclient sync config (its custom_hooks apply the patches)
├── patches/              # Flat, tag-agnostic patches: engine.patch, dart.patch, skia.patch
├── scripts/
│   ├── ci/               # lightweight contract checks (e.g. check_repo.py)
│   ├── device/           # ADB → Termux smoke automation
│   ├── install/          # post_install.sh (on-device) + install.sh one-click
│   ├── release/          # apt repo builder: apt_repo.py + stub/ maintainer scripts
│   └── test/             # Release E2E smoke scripts (gh_e2e_test.sh)
├── docs/
│   ├── CI_CD.md          # CI/CD and device-lab guide
│   ├── guides/           # install / build / upgrade guides (you are here)
│   └── releases/         # CHANGELOG.md, RELEASE_NOTES.md
├── README.md             # English (primary) documentation
├── sysroot/              # Termux runtime deps (generated at build)
├── flutter/              # Flutter source (cloned at build)
│   └── engine/src/out/   # build artifacts
└── release/              # final deb package
```

---

## Update Log

<details>
<summary>Historical updates</summary>

### 2026-06-01
- Updated this document to the Flutter 3.47.8 / Dart 3.13.7 state
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

</details>
