# Build Guide — Flutter for Termux from Source

This guide explains how to build the Flutter deb package (including Android
gen_snapshot) from scratch on a Linux x86-64 host. It is organized as a quick
start, then numbered step-by-step instructions, then troubleshooting.

Related guides:

- Installing the finished deb on a device: [Install guide](INSTALL_GUIDE.md)
- Moving to a new Flutter version: [Upgrade guide](UPGRADE_GUIDE.md)
- How the build works internally (patches, GN configs, sysroot):
  [Build process](BUILD_PROCESS.md)
- CI workflows and the device lab: [CI/CD](../CI_CD.md)

## Current Version Status (3.47.8 / 2026-08-15)

| Item | Value |
|------|----|
| Flutter tag | `3.47.8` |
| Engine revision | `5a2a6a42cce67f965cf540fcecf616faca624aa1` |
| Package | `flutter_3.47.8-6_aarch64.deb` |
| Package size | 678,847,056 bytes (about 647 MiB) |
| SHA256 | `9abd5b3048202475407804618605d49861aec73da414a1223db2f0cfc3f2fe90` |
| Device smoke | Samsung SM-X716B / Android 16 / Termux |

3.47.8 introduces three new points that require special attention:

1. **Dart VM/tool split**: The Flutter CLI uses the Termux JIT `dart`, but
   engine snapshots still need the accompanying `dartvm` / `dartaotruntime`,
   so the package validator must check all three. The dart SDK is produced by
   the standard build's `dart_sdk_archive` target.
2. **Flutter Tools Android host**: On Termux, Dart reports
   `Platform.operatingSystem == "android"`, so the host artifact lookup must be
   mapped to Linux ARM64.
3. **Flutter Gradle plugin**: Flutter 3.44's `FlutterPlugin.kt` imports
   `PLATFORM_ABI_LIST` directly, so the post-install ARM64-only
   `FlutterPluginConstants.kt` template must keep this symbol, and the Gradle
   included-build cache must be cleaned.

> **Note:** `debuild` repackages the entire SDK (about 6-8 minutes) but does
> **not** recompile the engine — don't confuse its duration with that of the
> `ninja` build (hours).

## CI/CD Summary

The full engine build runs on the free GitHub-hosted `ubuntu-latest` runner
(`build.yml`); PRs run lightweight checks first:

```bash
ruff check build.py package.py sysroot.py utils.py test_build.py test_apt_repo.py scripts/ci/ scripts/release/
python -m py_compile build.py package.py sysroot.py utils.py scripts/ci/check_repo.py scripts/ci/check_version_drift.py scripts/ci/verify_release_asset.py scripts/ci/extract_release_notes.py scripts/ci/import_upstream_notes.py scripts/ci/sync_release_pins.py scripts/release/apt_repo.py
pytest test_build.py test_apt_repo.py
bash -n scripts/install/post_install.sh scripts/test/gh_e2e_test.sh scripts/device/termux_smoke.sh
python scripts/ci/check_repo.py
python scripts/ci/check_version_drift.py
git diff --check
```

GitHub Actions is currently split into these tracks:

- `.github/workflows/ci.yml`: GitHub-hosted sanity checks for PRs/pushes.
- `.github/workflows/build.yml`: GitHub-hosted full `.deb` build,
  auto-triggered on CI success on `main` (or manual); a `gate` job skips the
  build unless the `build.toml` tag is unreleased (version bump), publishes a
  release.
- `.github/workflows/build-deb.yml`: self-hosted fallback full `.deb` build
  with evidence/artifact collection.
- `.github/workflows/device-smoke.yml`: manual self-hosted Windows + ADB
  tablet smoke test.
- `.github/workflows/release-check.yml`: Release asset metadata / SHA256
  checks.
- The `pkg install flutter` path is served by the unified
  [termux-apt-repo](https://github.com/GeneralKaos666/termux-apt-repo): this
  repo generates the stub with `scripts/release/apt_repo.py stub`, which is
  dropped into that repository and republished there.

See [`docs/CI_CD.md`](../CI_CD.md) for details.

---

## 1. Build Environment Requirements

- Windows 11 + WSL2 (Ubuntu 22.04+), or any Linux x86-64 host
- At least 100GB of free disk space
- At least 16GB RAM
- Stable network connection

---

## 2. Quick Start (One-Command Build)

If the environment is already set up (steps 1-4 below), the entire pipeline
is:

```bash
python3 build.py
```

This command automatically completes:

1. Configure the Linux release build
2. Compile the Flutter engine
3. Compile the dart binary (critical!)
4. Package the deb

A full run takes roughly 2-4 hours on 24 threads. Patches are applied
automatically during `sync` via the `.gclient` `custom_hooks`, so no explicit
`patch_*` step is needed — in fact, running one right after `sync` fails with
"already exists" (see [Troubleshooting](#patch-already-exists-error)).

---

## 3. Step-by-Step Build

Run each step in order.

### Step 1: Install Host Dependencies

```bash
# Run in Ubuntu WSL
sudo apt update
sudo apt install -y git curl python3 python3-pip ninja-build pkg-config
pip3 install fire loguru toml pyyaml
```

You also need `gclient` (installed in the next step), `vpython3`, `dpkg`
(`sysroot.py` runs `dpkg -x`), and `ar` (`package.py` runs `ar rc`).

### Step 2: Set Up depot_tools

```bash
cd ~
git clone https://chromium.googlesource.com/chromium/tools/depot_tools.git
export PATH="$HOME/depot_tools:$PATH"
echo 'export PATH="$HOME/depot_tools:$PATH"' >> ~/.bashrc
```

### Step 3: Clone the Project

```bash
mkdir -p ~/projects
cd ~/projects
git clone https://github.com/GeneralKaos666/flutter-for-termux.git termux-flutter
cd termux-flutter
```

### Step 4: Sync Flutter Engine Source

```bash
python3 build.py clone   # Clone Flutter
python3 build.py sync    # Sync dependencies; the .gclient custom_hooks apply
                         # patches/engine.patch, dart.patch, and skia.patch
                         # automatically. Takes several hours (~30GB).
```

> **About patches:** patches are applied during `sync` — no explicit
> `patch_*` step is needed. The `patch` helper is only for re-applying a
> patch to a fresh checkout, e.g. when rebasing on a new Flutter version.

### Step 5: Assemble the Sysroot

```bash
python3 build.py sysroot --arch=arm64
```

This downloads the required Termux runtime libraries from the official
Termux apt repo (skip if `sysroot/` is already assembled for this version).

### Step 6: Configure and Compile

```bash
# Linux release (also builds the gen_snapshot used by flutter build linux and,
# on-device, flutter build apk)
python3 build.py configure --arch=arm64 --mode=release
python3 build.py build --arch=arm64 --mode=release
```

An Android-target `gen_snapshot` is built for `flutter build apk --release/--profile` (see [What Ships in the deb](#5-what-ships-in-the-deb)). The Linux desktop `gen_snapshot` is not used for Android AOT on this target; `post_install.sh` resolves and provisions it. Debug uses JIT.

> **Note:** the `build()` ninja invocation includes
> `flutter/build/archives:dart_sdk_archive` (plus `flutter_patched_sdk` and
> `flutter_gtk`), so the dart binary is produced as part of the standard
> build — `ninja flutter` alone would **not** compile it.

### Step 7: Package the deb

```bash
python3 build.py debuild --arch=arm64
```

### Step 8: Output Files

After the build completes, the deb package is located at:

```text
release/flutter_3.47.8-6_aarch64.deb
```

---

## 4. Verify the Build

1. Check that the required host-side artifacts exist:

   ```bash
   # Check all required files
   ls -la flutter/engine/src/out/linux_debug_arm64/dart-sdk/bin/dart  # Critical!
   ls -la flutter/engine/src/out/linux_debug_arm64/gen_snapshot
   ls -la flutter/engine/src/out/linux_release_arm64/gen_snapshot
   ```

2. Deploy to Termux and run the on-device checklist — see
   [Test Verification Checklist](#9-test-verification-checklist) below.

---

## 5. What Ships in the deb

| Component | Path | Purpose |
|------|------|------|
| Flutter SDK | /data/data/com.termux/files/usr/opt/flutter | Main program |
| dart binary | .../dart-sdk/bin/dart | Core of the flutter command |
| Linux gen_snapshot | .../engine/linux-arm64/gen_snapshot | flutter run -d linux |
| Android gen_snapshot | .../engine/android-arm64-release/linux-arm64/gen_snapshot | flutter build apk (on-device path; created by post_install.sh after precache, not a separate deb payload) |

Only the Linux `gen_snapshot` ships in the deb. The Android row is the
on-device path that `post_install.sh` creates (a symlink to the Linux
artifact) once `flutter precache --android` has made the
`android-arm64-release` directory exist.

---

## 6. On-Device Setup After Building

Installing your freshly built deb works exactly like the release install —
follow [Install guide](INSTALL_GUIDE.md) (the manual deb method), or in
short:

```bash
dpkg -i flutter_3.47.8-6_aarch64.deb
apt --fix-broken install -y
bash $PREFIX/share/flutter/post_install.sh
source $PREFIX/etc/profile.d/flutter.sh
flutter doctor -v
```

`post_install.sh` handles most of the things that had to be done manually in
older build versions: Android API 34/36, cmdline-tools, build-tools symlinks,
NDK clang driver configuration, CMake toolchain patches, Dart snapshots,
Flutter Tools Android-host patches, Gradle plugin ARM64-only ABI, ELF
cleaner, shebang fixes, and cache cleanup.

Per-project setup (the APK `gradle.properties` / `build.gradle.kts` pins and
the Linux desktop `CMakeLists.txt` tweak) is covered step-by-step in the
[Install guide](INSTALL_GUIDE.md#build-your-first-android-apk) — your own
build needs exactly the same per-project settings.

---

## 7. Troubleshooting

Each entry shows the exact error (**Symptom**), why it happens (**Cause**),
and the fix (**Fix**).

### ninja build error

- **Symptom:** the `ninja` build fails in a shell/platform file.
- **Cause:** the patches were not applied correctly.
- **Fix:** verify the patch is present:

  ```bash
  cd flutter/engine/src/flutter
  git diff shell/platform/embedder/BUILD.gn
  ```

### Patch "already exists" error

- **Symptom:** running `python3 build.py patch_*` right after `sync` fails
  with "already exists".
- **Cause:** the `.gclient` `custom_hooks` already applied the patches during
  `sync`.
- **Fix:** this is expected — do not re-apply. Use `gclient sync -D` or reset
  the checkout first if you really need to re-test a patch manually.

### Missing dependencies at compile time

- **Symptom:** headers or libraries are missing while compiling.
- **Cause:** the sysroot was not assembled (or is stale).
- **Fix:**

  ```bash
  python3 build.py sysroot --arch=arm64
  ```

### vpython3 not found (depot_tools issue)

- **Symptom:**

  ```text
  /bin/sh: vpython3: not found
  ```

- **Cause:** depot_tools is not on the PATH, or depot_tools' `vpython3` is a
  symlink pointing to `vpython`, and `vpython` is also broken.
- **Fix:** first make sure depot_tools is on the PATH:

  ```bash
  export PATH="$HOME/depot_tools:$PATH"
  ```

  If ninja still cannot find vpython3 during compilation, manually create a
  vpython3 wrapper script:

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

  > **Note:** if you are on Windows/WSL, make sure the script uses LF line
  > endings, not CRLF:
  >
  > ```bash
  > # Fix the CRLF issue
  > cat vpython3 | tr -d '\r' > vpython3.tmp && mv vpython3.tmp vpython3
  > chmod +x vpython3
  > ```

### openjdk-17 does not exist (Termux package issue)

> **Deprecated:** modern installers pull a single JDK (`openjdk-21`) plus
> `7zip`; `openjdk-17` is no longer referenced anywhere in the project.

- **Symptom:**

  ```text
  android-sdk depends on openjdk-17; however:
    Package openjdk-17 is not installed.
  ```

- **Cause:** the android-sdk package depends on openjdk-17, but Termux only
  has openjdk-21.
- **Fix:**

  ```bash
  # Install openjdk-21
  pkg install openjdk-21

  # Force-configure android-sdk (ignore dependencies)
  dpkg --force-depends --configure android-sdk
  ```

  **Permanent fix:** on the primary apt path nothing is needed — the published
  stub's control appends the installer's unconditional packages (`openjdk-21`
  from `build.toml [installer] java`, plus `wget`, `curl`, `unzip`, `7zip`,
  `binutils`, `tar`, `xz-utils`) to the payload's `Depends`, so
  `pkg install flutter` pulls Java in automatically. Only a raw payload `.deb`
  built locally lacks them: `package.yaml` declares the slim list below, so a
  manual `apt install ./<deb>` must have a JDK present first:

  ```yaml
  Depends: git, which, gtk3, xorgproto, ninja, cmake, clang, pkg-config
  ```

### libflutter_linux_gtk.so missing (Linux desktop support)

- **Symptom:**

  ```text
  flutter build linux --debug
  Error: Could not find libflutter_linux_gtk.so
  ```

- **Cause:** `flutter build linux` requires `libflutter_linux_gtk.so`, but an
  older build configuration did not compile this target.
- **Fix:** enable the flutter_gtk target in the `build()` method of
  `build.py` (already included in the current pipeline):

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

### dartaotruntime missing

- **Symptom:**

  ```text
  Error: dartaotruntime not found
  ```

- **Cause:** `flutter build apk --release` requires dartaotruntime.
- **Fix:** copy it into the dart-sdk bin directory:

  ```bash
  # Copy dartaotruntime_product to dart-sdk/bin
  cp flutter/engine/src/out/linux_debug_arm64/dartaotruntime_product \
     flutter/engine/src/out/linux_debug_arm64/dart-sdk/bin/dartaotruntime
  ```

### CRLF line-ending issue (Windows/WSL)

- **Symptom:**

  ```text
  C:/Program: No such file or directory
  ```

- **Cause:** shell scripts created on Windows may have CRLF line endings,
  causing execution to fail.
- **Fix:**

  ```bash
  # Convert to LF
  cat script.sh | tr -d '\r' > script.tmp && mv script.tmp script.sh
  chmod +x script.sh

  # Or use dos2unix
  dos2unix script.sh
  ```

### ADB remote installation fails

- **Symptom:** commands sent to Termux using `am broadcast` do not execute.
- **Cause:** external app execution permission is not enabled in Termux.
- **Fix:**

  ```bash
  # Run in Termux
  echo "allow-external-apps=true" >> ~/.termux/termux.properties
  termux-reload-settings
  ```

  Or just run the installation commands manually in Termux.

### X11-related dependencies (`flutter run -d linux` has no display)

- **Symptom:** `flutter run -d linux` requires an X11 environment.
- **Cause:** no X server is available inside Termux.
- **Fix:** install the X11 repo and related packages in Termux:

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

### gen_snapshot version mismatch

- **Symptom:**

  ```text
  version differs from vm's version
  ```

- **Cause:** the dart and gen_snapshot binaries come from different builds.
- **Fix:** make sure to run the full pipeline so dart and gen_snapshot come
  from the same build:

  ```bash
  python3 build.py
  ```

### ninja: error: 'xxx' does not exist

- **Symptom:** building immediately after configuring reports a
  file-does-not-exist error.
- **Cause:** the build started before the configuration finished.
- **Fix:** make sure the configuration completes before building:

  ```bash
  # Configure first
  python3 build.py configure --arch=arm64 --mode=debug

  # Wait for configuration to finish, then build
  python3 build.py build --arch=arm64 --mode=debug
  ```

### TLS segment underaligned (Bionic linker issue)

- **Symptom:**

  ```text
  error: "dart": executable's TLS segment is underaligned: alignment is 8 (skew 0), needs to be at least 64 for ARM64 Bionic
  ```

  This error appears when running `flutter doctor` or any dart command on
  Termux.
- **Cause:** the dart binary was compiled using glibc's dynamic linker
  (`/lib/ld-linux-aarch64.so.1`) instead of Android Bionic's
  (`/system/bin/linker64`). Android Bionic requires the TLS (Thread Local
  Storage) segment to be aligned to 64 bytes.
- **Fix:** add the bionic linker to the `executable_ldconfig` config in
  `build/config/termux/BUILD.gn` (already in the current
  `patches/engine.patch`):

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

### Insufficient disk space

- **Symptom:** the build fails with no space left on device/host.
- **Cause:** the Flutter Engine source is about 30GB and the build output
  about 20GB.
- **Fix:** keep at least 60GB free (the environment requirements above
  recommend 100GB to be safe).

---

## 8. Known Limitations

### Android APK only supports ARM64

- **Problem:** `flutter build apk --release` only supports the
  `android-arm64` platform.
- **Cause:** we can only compile the ARM64 version of gen_snapshot:
  - `android-arm` (32-bit): BoringSSL has a 32-bit shift overflow compile error
  - `android-x64`: The ARM64 sysroot cannot be used for x64 cross-compilation
- **Impact:** the built APK can only run on ARM64 devices and does not support
  ARM32 or x86 emulators.
- **Usage:** explicitly specify ARM64 to avoid the old cache or project
  settings being misdetected:

  ```bash
  flutter build apk --release --target-platform android-arm64 --no-tree-shake-icons
  ```

### Debug vs Release mode mismatch (technical background)

The current deb package uses binaries built in **release mode**:

- `dart` - release mode
- `dartaotruntime` - release mode
- `frontend_server_aot.dart.snapshot` - release mode
- `gen_snapshot` - release mode

This is because the deb packages only the modes listed in
`build.toml [build] runtime`.

**Impact on users:**

- `flutter doctor` ✅ runs normally
- `flutter build apk --release` ✅ runs normally (uses the android gen_snapshot)
- `flutter run -d linux --release` / `flutter build linux --release` ✅ run normally
- `flutter run -d linux` (debug) ⚠️ needs a debug-mode engine, so pass
  `--release` or `--profile`, or rebuild with `--mode=debug`

### Release-mode build problems (developer reference)

If you try to build release mode, you may encounter:

**sysroot header conflict**

```text
error: typedef redefinition with different types ('__mbstate_t' vs 'struct mbstate_t')
```

- **Cause:** the sysroot contains both glibc and bionic headers:
  - `/sysroot/usr/include/` - glibc headers
  - `/sysroot/data/data/com.termux/files/usr/include/` - Termux/bionic headers
- **Resolution direction:** the sysroot needs to be cleaned to keep only the
  bionic headers.

**BoringSSL getrandom syscall issue**

```text
error: This system call is not available on Android
```

- **Cause:** BoringSSL detects that the getrandom() syscall is unavailable.
- **Resolution direction:** a `__ANDROID__` define needs to be added or
  BoringSSL needs to be patched.

**dedup_instructions snapshot flag error**

```text
Flag dedup_instructions is false in snapshot, but dedup_instructions is always true in product mode
```

- **Cause:** `dartaotruntime_product` expects a product-mode snapshot, but
  `frontend_server_aot.dart.snapshot` was built in debug mode.
- **Resolution direction:** build a release-mode
  `frontend_server_aot.dart.snapshot`.

---

## 9. Test Verification Checklist

After the build completes, verify with the following checklist.

1. Check required files exist on the build host:

   ```bash
   # 1. Check required files
   ls -la flutter/engine/src/out/linux_debug_arm64/dart-sdk/bin/dart
   ls -la flutter/engine/src/out/linux_debug_arm64/gen_snapshot
   ls -la flutter/engine/src/out/linux_debug_arm64/libflutter_linux_gtk.so
   ls -la flutter/engine/src/out/linux_release_arm64/gen_snapshot
   ```

2. After deploying to Termux (after running post_install.sh):

   ```bash
   # 2. After deploying to Termux (after running post_install.sh)
   flutter doctor -v               # ✅ verified
   flutter create test_app         # ✅ verified
   cd test_app
   flutter build apk --release     # ✅ verified
   flutter build apk --debug       # ✅ verified
   flutter build linux --debug     # ✅ verified (requires Termux:X11)
   flutter run                     # ✅ verified (Hot Reload supported)
   ```

---

## 10. Feature Test Results (3.47.8, updated 2026-08-15)

| Feature | Status | Description |
|------|------|------|
| `flutter --version` | ✅ OK | Flutter 3.47.8 / Tools Dart 3.13.7 |
| `dart --version` | ✅ OK | Termux JIT Dart 3.13.7 (`android_arm64`) |
| `dartvm --version` | ✅ OK | post-install `dartvm` resolves to Dart 3.13.7 (`android_arm64`) |
| `flutter doctor -v` | ✅ OK | unknown channel / no device are expected warnings |
| `flutter create` | ✅ OK | Can create Android + Linux projects |
| `flutter build apk --release --target-platform android-arm64 --no-tree-shake-icons` | ✅ OK | Requires running post_install.sh; only supports android-arm64 |
| `flutter build linux --release` | ✅ OK | Produces an ARM64 ELF bundle |
| `flutter run` (Android) | ✅ Supported | A device is only shown after ADB connects within Termux |

---

## 11. Upgrading the Flutter Version

The upgrade runbook (bump `build.toml`, rebase patches, rebuild, test,
publish) lives in the [Upgrade guide](UPGRADE_GUIDE.md). The one-command
pipeline also handles upgrades when the patches apply cleanly:

```bash
python3 build.py
```

### Patch maintenance tips

1. **Keep patches minimal**: only modify what is necessary
2. **Add comments**: explain in the patch why the modification is needed
3. **Version isolation**: each Flutter version has its own patch directory
4. **Record changes**: update docs/releases/CHANGELOG.md

---

## History

<details>
<summary><b>Problem analysis (2025-12-28): why `ninja flutter` alone was not enough</b></summary>

### Original problem

- `flutter run -d linux` ✓ works
- `flutter build apk --release` ✗ fails (dart version issue)

### Root cause

**`ninja flutter` alone does not compile the dart binary!**

The `bin/cache/dart-sdk/bin/dart` binary must ship in the deb package,
otherwise:

- The flutter command fails to execute properly
- gen_snapshot version mismatch errors

### Solution

The FFT `build()` ninja invocation includes
`flutter/build/archives:dart_sdk_archive` (plus `flutter_patched_sdk` and
`flutter_gtk`), so the dart binary is produced as part of the standard build:

```bash
python3 build.py build --arch=arm64 --mode=release
```

Or use the one-command build:

```bash
python3 build.py
```

### deb package contents confirmation (after fix)

```text
✓ bin/cache/dart-sdk/bin/dart (102MB)
✓ bin/cache/artifacts/engine/linux-arm64/gen_snapshot (6.9MB)
✓ bin/cache/artifacts/engine/android-arm64-release/linux-arm64/gen_snapshot (6.4MB; on-device symlink created by post_install.sh)
```

</details>

<details>
<summary><b>Historical manual release flow (pre-automation)</b></summary>

Before the `Build` workflow auto-published releases (see
[CI/CD](../CI_CD.md#release-policy)), upgrades were released by hand:

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

Test all functionality in a clean Termux environment before tagging:

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

</details>

<details>
<summary><b>Update log</b></summary>

Version history lives in [docs/releases/CHANGELOG.md](../releases/CHANGELOG.md).
Historical build-guide milestones:

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

</details>
