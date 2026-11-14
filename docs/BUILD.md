# Build and Install Guide

Install the package on a device, build it from source, and understand how the
build works internally. Replaces `docs/guides/INSTALL_GUIDE.md`,
`docs/guides/BUILD_GUIDE.md` and `docs/guides/BUILD_PROCESS.md`.

## Install on device

This guide walks you through installing `flutter_3.50.0-4_aarch64.deb` on an
ARM64 Termux device, verifying it, and building your first app. After
installing you will have:

- `flutter doctor -v`
- `flutter create`
- `flutter build apk --release`
- `flutter build linux --release`
- `flutter run` + hot reload (requires an ADB device connection)

### Verified Versions

| Item | Value |
|------|-------|
| Flutter | 3.50.0 |
| Flutter Tools Dart | 3.14.0 |
| Dart VM (`dartvm`) | Dart 3.14.0 (`linux_arm64`) |
| Test device | Samsung SM-X716B / Android 16 / ARM64 |
| deb size | 635,877,268 bytes (about 606 MiB) |
| SHA256 | `8a5ee969c1c671360054bf26b992b93b9770372ae6673dfdc6155bd4191dc981` |

### System Requirements

| Item | Requirement |
|------|-------------|
| Android | Android 11 (API 30) or higher |
| CPU | ARM64 / aarch64 |
| Termux | F-Droid build or official GitHub release build recommended |
| Storage | At least 5GB; 8GB+ recommended for a full Android SDK/NDK + Gradle cache |
| Java | `openjdk-21` |

> **Before you start:** run `pkg update && pkg upgrade` first:
>
> ```bash
> pkg update && pkg upgrade
> ```
>
> The `x11-repo` package is required by the manual Methods 2 and 3. Method 1
> (apt) needs no extra step here: the published stub declares
> `Pre-Depends: x11-repo`, so APT fetches it automatically during
> `pkg install flutter`:
>
> ```bash
> pkg install x11-repo   # Methods 2 and 3 only
> ```

---

### Method 1: apt install (Recommended)

Add the signed repository once, then install Flutter with APT. The
[README quick start](../README.md#quick-start) carries the same snippet plus the
signing-key fingerprint to compare against.

```bash
pkg install -y gnupg
mkdir -p "$PREFIX/etc/apt/keyrings"
curl -fsSL https://generalkaos666.github.io/termux-apt-repo/key.gpg \
  -o "$PREFIX/etc/apt/keyrings/termux-apt-repo.gpg"
echo "deb [signed-by=$PREFIX/etc/apt/keyrings/termux-apt-repo.gpg] https://generalkaos666.github.io/termux-apt-repo stable main" \
  > "$PREFIX/etc/apt/sources.list.d/termux-apt-repo.list"
pkg update && pkg install flutter
flutter doctor
```

`pkg install flutter` pulls the stub package, which downloads the release archive,
verifies its checksum and size, unpacks it into `$PREFIX`, and runs
`post_install.sh` for you. `apt policy flutter` shows the candidate version and
its source. Later releases arrive with `apt upgrade flutter` — see the
[Upgrade guide](MAINTENANCE.md).

The payload ships without the Android NDK (~550 MB), so pure-Dart projects
work out of the box while native-plugin projects print a clear hint. To
provision the pinned Termux-arm64 NDK (opt-in, idempotent, SHA256-verified):

```bash
bash $PREFIX/share/flutter/post_install.sh --provision-ndk
```

---

### Method 2: Manual deb install

Run each step in order inside Termux.

1. Install the prerequisites:

   ```bash
   pkg update -y
   pkg install -y x11-repo git wget curl unzip openjdk-21 aapt2 android-tools cmake ninja clang
   ```

2. Download the deb and verify its checksum:

   ```bash
   cd ~
   wget https://github.com/GeneralKaos666/flutter-for-termux/releases/download/3.50.0/flutter_3.50.0-4_aarch64.deb
   # Confirm the output matches: 8a5ee969c1c671360054bf26b992b93b9770372ae6673dfdc6155bd4191dc981
   sha256sum flutter_3.50.0-4_aarch64.deb
   ```

   Expected output — the hash must match exactly:

   ```text
   eac81132f6060f4691cba05b86d445de79abb247ef8dd9aecf7e303f3e872474  flutter_3.50.0-4_aarch64.deb
   ```

3. Install the package and repair any missing dependencies:

   ```bash
   dpkg -i flutter_3.50.0-4_aarch64.deb
   apt --fix-broken install -y
   ```

4. Post-install runs automatically: the package's `postinst` invokes
   `bash $PREFIX/share/flutter/post_install.sh` during `dpkg`/`apt` install.
   It patches the Termux runtime so Flutter actually works, and skips its own
   `apt`/`pkg install` calls because dpkg holds those locks. To finish those
   skipped dependency installs, run it once more yourself (idempotent):

   ```bash
   bash $PREFIX/share/flutter/post_install.sh
   ```

   Its output from the automatic run is at
   `$PREFIX/share/flutter/post_install.log`.

5. Load the Flutter environment into your shell and check the install:

   ```bash
   source $PREFIX/etc/profile.d/flutter.sh
   flutter doctor -v
   ```

6. Continue with [Verify the Installation](#verify-the-installation).

#### Expected `flutter doctor` warnings (not errors)

These two warnings are normal and can be ignored:

- **unknown channel / unknown upstream source** — the Flutter SDK in the deb
  is not an official git remote checkout.
- **no connected device** — no ADB device is connected in Termux yet; this
  does not affect `flutter build apk`.

---

### What post_install.sh Does

| Category | Details |
|----------|---------|
| Dart | Keeps the engine-built `dart`, `dartvm`, and `dartaotruntime` in the SDK cache so the Flutter CLI and its snapshots stay in step |
| Flutter Tools | Patches the Android/Termux host lookup and clears stale `flutter_tools` snapshots/cache |
| Gradle plugin | ARM64-only ABI; adds the `PLATFORM_ABI_LIST` that Flutter 3.44 needs |
| Android SDK | Installs/patches API 36 (falling back 36 → 35 → 34), cmdline-tools, build-tools, licenses |
| NDK | Configures the clang driver and host-tag aliases, patches the CMake toolchain, replaces objcopy/strip |
| AOT `gen_snapshot` | Points `flutter build apk` at the Android-target `gen_snapshot` shipped in the package, and keeps the per-build symlinks as a fallback |
| Linux desktop | Enables `flutter build linux` to run on the Termux host |

---

### Verify the Installation

Run all four checks:

```bash
flutter --version
dart --version
$PREFIX/opt/flutter/bin/cache/dart-sdk/bin/dartvm --version
flutter doctor -v
```

Expected highlights:

- `flutter --version` shows Flutter 3.50.0.
- `dart --version` shows Dart 3.14.0 on `linux_arm64` (the SDK's own Dart).
- The SDK's `dartvm` shows Dart 3.14.0 on `linux_arm64` (the engine VM). It is
  not on `PATH`; use the full path above.
- `flutter doctor -v` may show the two expected warnings listed above.

---

### Build Your First Android APK

1. Create the project and fix the Gradle wrapper shebang:

   ```bash
   flutter create myapp
   cd myapp
   sed -i '1s|#!/usr/bin/env bash|#!/data/data/com.termux/files/usr/bin/bash|' android/gradlew
   ```

2. Set the project Gradle properties (in `android/gradle.properties`):

   ```properties
   android.aapt2FromMavenOverride=/data/data/com.termux/files/usr/bin/aapt2
   android.enableResourceOptimizations=false
   shrink=false
   org.gradle.jvmargs=-Xmx2048m -XX:MaxMetaspaceSize=512m -Dfile.encoding=UTF-8
   ```

3. Edit `android/app/build.gradle.kts` — set the SDK versions and disable
   minification/shrinking:

   ```kotlin
   android {
       compileSdk = 36

       defaultConfig {
           targetSdk = 36
           ndk { abiFilters += listOf("arm64-v8a") }
       }

       buildTypes {
           release {
               isMinifyEnabled = false
               isShrinkResources = false
           }
       }
   }
   ```

4. Build. You must bypass icon tree shaking, which is restricted by JIT Dart:

   ```bash
   flutter build apk --release --target-platform android-arm64 --no-tree-shake-icons
   ```

   Expected artifact:

   ```text
   build/app/outputs/flutter-apk/app-release.apk
   ```

> **Tip:** `bash $PREFIX/share/flutter/flutter_project_config.sh` applies the
> `gradle.properties` and `build.gradle.kts` settings above automatically —
> run it once per new project instead of editing by hand.

---

### Build Your First Linux Desktop App

1. Create the project, mark it as a Linux build, and build:

   ```bash
   flutter create mylinux --platforms=linux
   cd mylinux
   sed -i '1i set(CMAKE_SYSTEM_NAME Linux)' linux/CMakeLists.txt
   flutter build linux --release
   ```

   Expected artifact directory:

   ```text
   build/linux/arm64/release/bundle/
   ```

2. To see the app on screen, use [Termux:X11](https://github.com/termux/termux-x11/releases):

   ```bash
   export DISPLAY=:0
   termux-x11 :0 >/dev/null 2>&1 &
   flutter run -d linux
   ```

---

### flutter run / Hot Reload

`flutter run` requires ADB inside Termux to see the Android device. If you
are using the same tablet/phone, Android "wireless debugging" is recommended:

1. Install ADB (already included by both install methods, listed here for
   completeness):

   ```bash
   pkg install android-tools
   ```

2. Pair and connect to the device:

   ```bash
   adb pair 127.0.0.1:<pair_port>
   adb connect 127.0.0.1:<connect_port>
   ```

3. List devices and run:

   ```bash
   flutter devices
   flutter run -d <device_id>
   ```

If `flutter doctor` shows no connected device, it only means ADB is not
connected yet, not that the SDK is broken.

---

### Troubleshooting

Each entry below shows the exact error (**Symptom**), why it happens
(**Cause**), and the fix.

#### `PLATFORM_ABI_LIST` unresolved

- **Symptom:** a Kotlin unresolved-reference error for `PLATFORM_ABI_LIST`
  while building an APK.
- **Cause:** the post-install Flutter Gradle plugin template or the Gradle
  cache is stale — common after updating to the 3.50.0 deb.
- **Fix:** re-run post-install and clear the Gradle caches, then rebuild:

  ```bash
  bash $PREFIX/share/flutter/post_install.sh
  ./android/gradlew --stop || true
  rm -rf ~/.gradle/caches .gradle android/.gradle build android/app/build
  flutter build apk --release --target-platform android-arm64 --no-tree-shake-icons
  ```

#### AAPT2 / compileSdk errors

- **Symptom:** `aapt2` or `compileSdk` related errors while building an APK.
- **Cause:** the project is not pinned to API 36 and the Termux ARM64 aapt2.
- **Fix:** always use API 36 and point to the Termux ARM64 aapt2 (post-install
  falls back to 35/34 if aapt2 cannot load android-36):

  ```properties
  android.aapt2FromMavenOverride=/data/data/com.termux/files/usr/bin/aapt2
  ```

  ```kotlin
  compileSdk = 36
  defaultConfig { targetSdk = 36 }
  ```

#### NDK or CMake cannot find the compiler

- **Symptom:** native plugin compilation fails with a missing compiler error.
- **Cause:** Gradle may have downloaded a new NDK or build-tools that
  post-install has not configured yet.
- **Fix:** re-run post-install:

  ```bash
  bash $PREFIX/share/flutter/post_install.sh
  ```

#### `did not have a source.properties file` (NDK metadata error)

- **Symptom:** `flutter build apk` / `flutter build appbundle` fails while
  configuring a native plugin with:

  ```text
  [CXX1101] NDK at .../ndk/<version> did not have a source.properties file
  ```

- **Cause:** the installed NDK directory is incomplete. AGP requires
  `<sdk>/ndk/<version>/source.properties` to identify the NDK, and an
  interrupted or partially extracted install can leave that file missing.
- **Fix:** re-run post-install, which validates every installed NDK and
  regenerates the missing metadata when the rest of the directory is usable:

  ```bash
  bash $PREFIX/share/flutter/post_install.sh
  ```

  If the directory is incomplete (no `toolchains/llvm`, or no CMake toolchain
  file), post-install removes it rather than stamping it healthy. Re-running
  post-install cannot rebuild a removed directory. The NDK is not part of the
  apt payload (the stub restores the Flutter tree only), so (re)provision it
  with the opt-in flag — it downloads the aarch64 NDK (~550 MB), verifies its
  SHA256, and extracts it into `$PREFIX/opt/android-sdk/ndk/`:

  ```bash
  bash $PREFIX/share/flutter/post_install.sh --provision-ndk
  ```

  Pure-Dart projects are unaffected either way: `flutter build apk --release`
  builds and links without any NDK installed; only native plugins
  (CMake / `externalNativeBuild`) need it.

#### `Could not find toolchain file` (incomplete NDK)

- **Symptom:** `flutter build apk` / `flutter build appbundle` fails while
  configuring a native plugin (`:jni`, or any plugin using
  `externalNativeBuild`) with:

  ```text
  CMake Error at .../CMakeDetermineSystem.cmake:165 (message):
    Could not find toolchain file:
    ".../ndk/<version>/build/cmake/android.toolchain.cmake"
  ```

- **Cause:** the NDK directory is only partially extracted: it still has
  `toolchains/llvm`, but the CMake toolchain files are gone. This is a
  different failure from the `source.properties` error above, and it appears
  *after* that metadata exists — so re-running post-install will not repair
  it. Post-install detects the directory as incomplete and removes it.
- **Fix:** re-provision the NDK with the opt-in flag (the apt package does not
  carry the NDK; it downloads the aarch64 repack, ~550 MB, verifies its
  SHA256, and installs it into `$PREFIX/opt/android-sdk/ndk/`):

  ```bash
  bash $PREFIX/share/flutter/post_install.sh --provision-ndk
  ```

  A complete NDK is verified to configure, compile, and link an `arm64-v8a`
  CMake module (`jni` 1.0.0) on-device, so the build proceeds once the
  toolchain file is back.

#### Release APK builds but shows a blank screen

- **Symptom:** `flutter build apk --release` completes and the APK installs,
  but the app opens to a white/blank screen; logcat may report an unexpected
  kernel or unhandled snapshot format.
- **Cause:** Android AOT snapshots must be produced by a `gen_snapshot` built
  for Android (`target_os=android`, compressed pointers). The Linux desktop
  `gen_snapshot` shipped in packages up to `3.50.0-6` produces snapshots the
  Android VM rejects ([issue #41](https://github.com/GeneralKaos666/flutter-for-termux/issues/41)).
- **Fix (`3.50.0-7` and newer):** upgrade, re-run post-install, and rebuild:

  ```bash
  apt upgrade flutter
  bash $PREFIX/share/flutter/post_install.sh
  cd myapp && flutter clean
  flutter build apk --release --target-platform android-arm64 --no-tree-shake-icons
  ```

  `3.50.0-7` ships the Android-target binary at
  `bin/cache/artifacts/engine/linux-arm64-android-{release,profile}/gen_snapshot`,
  and post-install patches `flutter_tools` to resolve it directly (marker
  `Termux: prefer package-shipped aarch64 gen_snapshot`). Confirm it is
  present with:

  ```bash
  ls -l $PREFIX/opt/flutter/bin/cache/artifacts/engine/linux-arm64-android-release/gen_snapshot
  ```

#### First build fails: `gen_snapshot` not found

- **Symptom:** the first `flutter build apk --release` after a (re)install
  fails during AOT with:

  ```text
  Target android_aot_release_android-arm64 failed: ProcessException:
  Failed to find ".../engine/android-arm64-release/linux-arm64/gen_snapshot" in the search path.
  ```

- **Cause:** Flutter's first-run artifact materialization recreates the
  `android-arm64-*` directories and drops the `linux-arm64` symlink that
  post-install creates (step 15). Upstream only puts an x86-64 binary there,
  which cannot run on ARM64 Termux.
- **Fix (`3.50.0-6` and newer):** nothing to do for this failure. Post-install
  resolves `gen_snapshot` to the package-shipped ARM64 binary, in a directory
  Flutter never writes, so the first build completes. The step-15 symlinks
  remain only as a fallback.
- **Heads-up (`3.50.0-6` only):** that package shipped the *Linux* ARM64
  `gen_snapshot` for this path, so the build succeeded but the release APK
  white-screened. `3.50.0-7` ships the Android-target binary — see
  [Release APK builds but shows a blank screen](#release-apk-builds-but-shows-a-blank-screen).
- **Fix (`3.50.0-5` and older):** upgrade to the current package, or re-run
  post-install to recreate the symlink, then build again:

  ```bash
  bash $PREFIX/share/flutter/post_install.sh
  flutter build apk --release
  ```

#### Stale Gradle or Kotlin errors after upgrading

- **Symptom:** old Gradle or Kotlin errors persist after installing a new deb.
- **Cause:** cached Gradle state from the previous version.
- **Fix:** re-run post-install and clear the Gradle caches:

  ```bash
  bash $PREFIX/share/flutter/post_install.sh
  rm -rf ~/.gradle/caches ~/.gradle/daemon
  ```

#### Out of storage space

- **Symptom:** the build fails with no space left on device.
- **Cause:** Gradle caches and downloaded packages grow large.
- **Fix:**

  ```bash
  rm -rf ~/.gradle/caches ~/.gradle/wrapper ~/.pub-cache/hosted
  pkg clean
  ```

#### Need an arm / x64 APK

- **Symptom:** you need a 32-bit ARM or x86_64 APK.
- **Cause:** not supported at the moment — this project only provides the
  ARM64 Android target.
- **Fix:** build explicitly for ARM64:

  ```bash
  flutter build apk --release --target-platform android-arm64
  ```

## Build from source

This guide explains how to build the Flutter deb package (including Android
gen_snapshot) from scratch on a Linux x86-64 host. It is organized as a quick
start, then numbered step-by-step instructions, then troubleshooting.

Related guides:

- Installing the finished deb on a device: [Install guide](#install-on-device)
- Moving to a new Flutter version: [Upgrade guide](MAINTENANCE.md)
- How the build works internally (patches, GN configs, sysroot):
  [Build process](#build-internals)
- CI workflows and the device lab: [CI/CD](MAINTENANCE.md)

### Current Version Status (3.50.0)

| Item | Value |
|------|----|
| Flutter tag | `3.50.0` |
| Engine revision | `cf636b1dfb818dd6a3ff16e390e93b1a036969c8` |
| Package | `flutter_3.50.0-4_aarch64.deb` |
| Package size | 635,877,268 bytes (about 606 MiB) |
| SHA256 | `8a5ee969c1c671360054bf26b992b93b9770372ae6673dfdc6155bd4191dc981` |
| Device smoke | Samsung SM-X716B / Android 16 / Termux |

3.50.0 introduces three new points that require special attention:

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

### CI/CD Summary

The full engine build runs on the free GitHub-hosted `ubuntu-latest` runner
(`build.yml`); PRs run lightweight checks first:

```bash
ruff check build.py package.py sysroot.py utils.py test_build.py test_apt_repo.py scripts/ci/ scripts/release/
python -m py_compile build.py package.py sysroot.py utils.py scripts/ci/check_repo.py scripts/ci/check_version_drift.py scripts/ci/verify_release_asset.py scripts/ci/extract_release_notes.py scripts/ci/import_upstream_notes.py scripts/ci/sync_release_pins.py scripts/release/apt_repo.py
pytest test_build.py test_apt_repo.py
bash -n scripts/install/post_install.sh scripts/install/deb_preinst.sh scripts/install/deb_postinst.sh scripts/test/gh_e2e_test.sh scripts/device/termux_smoke.sh
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

See [`docs/MAINTENANCE.md`](MAINTENANCE.md) for details.

---

### 1. Build Environment Requirements

- Windows 11 + WSL2 (Ubuntu 22.04+), or any Linux x86-64 host
- At least 100GB of free disk space
- At least 16GB RAM
- Android NDK r30, unpacked at `/opt/android-ndk-r30` — or set `ANDROID_NDK`
  (or `[ndk] path` in `build.toml`) to its location. CI runners provide one;
  the version in `build.toml [ndk] version` is `30.0.14206865`.
- Stable network connection

---

### 2. Quick Start (One-Command Build)

If the environment is already set up (steps 1-4 below), the entire pipeline
is:

```bash
python3 build.py
```

This command automatically completes:

1. Clone and sync Flutter, applying the patches through the `.gclient` hooks
2. Assemble the Termux sysroot
3. Configure and compile the Linux release engine, including the Dart SDK
4. Configure and compile the Android-target `gen_snapshot` for release and profile
5. Package the deb

A full run takes roughly 2-4 hours on 24 threads. Patches are applied
automatically during `sync` via the `.gclient` `custom_hooks`, so no explicit
`patch_*` step is needed — in fact, running one right after `sync` fails with
"already exists" (see [Troubleshooting](#patch-already-exists-error)).

---

### 3. Step-by-Step Build

Run each step in order.

#### Step 1: Install Host Dependencies

```bash
# Run in Ubuntu WSL
sudo apt update
sudo apt install -y git curl python3 python3-pip ninja-build pkg-config
pip3 install fire loguru toml pyyaml
```

You also need `gclient` (installed in the next step), `vpython3`, `dpkg`
(`sysroot.py` runs `dpkg -x`), and `ar` (`package.py` runs `ar rc`).

#### Step 2: Set Up depot_tools

```bash
cd ~
git clone https://chromium.googlesource.com/chromium/tools/depot_tools.git
export PATH="$HOME/depot_tools:$PATH"
echo 'export PATH="$HOME/depot_tools:$PATH"' >> ~/.bashrc
```

#### Step 3: Clone the Project

```bash
mkdir -p ~/projects
cd ~/projects
git clone https://github.com/GeneralKaos666/flutter-for-termux.git termux-flutter
cd termux-flutter
```

#### Step 4: Sync Flutter Engine Source

```bash
python3 build.py clone   # Clone Flutter
python3 build.py sync    # Sync dependencies; the .gclient custom_hooks apply
                         # patches/engine.patch, dart.patch, and skia.patch
                         # automatically. Takes several hours (~30GB).
```

> **About patches:** patches are applied during `sync` — no explicit
> `patch_*` step is needed. The `patch` helper is only for re-applying a
> patch to a fresh checkout, e.g. when rebasing on a new Flutter version.

#### Step 5: Assemble the Sysroot

```bash
python3 build.py sysroot --arch=arm64
```

This downloads the required Termux runtime libraries from the official
Termux apt repo (skip if `sysroot/` is already assembled for this version).

#### Step 6: Configure and Compile

Build the Linux desktop engine first. It produces the Dart SDK, the GTK
library, and the `gen_snapshot` used by `flutter build linux`:

```bash
python3 build.py configure --arch=arm64 --mode=release
python3 build.py build --arch=arm64 --mode=release
```

Then build the Android-target `gen_snapshot` that `flutter build apk` uses.
This is a separate GN configuration (`--android --android-cpu arm64`) and only
the `gen_snapshot` target:

```bash
python3 build.py configure_android --arch=arm64 --mode=release
python3 build.py build_android --arch=arm64 --mode=release

# Profile AOT, for `flutter build apk --profile`
python3 build.py configure_android --arch=arm64 --mode=profile
python3 build.py build_android --arch=arm64 --mode=profile
```

The Linux desktop `gen_snapshot` cannot stand in for the Android one: it emits
`target_os=linux` snapshots without compressed pointers, which the Android VM
rejects (a release APK built that way white-screens). `build.toml`
`[build] android_runtime` controls which Android modes are built.

> **Note:** the `build()` ninja invocation includes
> `flutter/build/archives:dart_sdk_archive` (plus `flutter_patched_sdk` and
> `flutter_gtk`), so the dart binary is produced as part of the standard
> build — `ninja flutter` alone would **not** compile it.

#### Step 7: Package the deb

```bash
python3 build.py debuild --arch=arm64
```

#### Step 8: Output Files

After the build completes, the deb package is located at:

```text
release/flutter_3.50.0-4_aarch64.deb
```

---

### 4. Verify the Build

1. Check that the required host-side artifacts exist:

   ```bash
   # Linux desktop engine (dart SDK + gen_snapshot)
   ls -la flutter/engine/src/out/linux_release_arm64/dart-sdk/bin/dart
   ls -la flutter/engine/src/out/linux_release_arm64/gen_snapshot
   ls -la flutter/engine/src/out/linux_release_arm64/libflutter_linux_gtk.so

   # Android-target gen_snapshot (release and profile)
   ls -la flutter/engine/src/out/android_release_arm64/gen_snapshot
   ls -la flutter/engine/src/out/android_profile_arm64/gen_snapshot
   ```

2. Deploy to Termux and run the on-device checklist — see
   [Test Verification Checklist](#9-test-verification-checklist) below.

---

### 5. What Ships in the deb

| Component | Path | Purpose |
|------|------|------|
| Flutter SDK | /data/data/com.termux/files/usr/opt/flutter | Main program |
| dart binary | .../dart-sdk/bin/dart | Core of the flutter command |
| Linux gen_snapshot | .../engine/linux-arm64/gen_snapshot | `flutter build linux` |
| Android gen_snapshot (release) | .../engine/linux-arm64-android-release/gen_snapshot | `flutter build apk --release` |
| Android gen_snapshot (profile) | .../engine/linux-arm64-android-profile/gen_snapshot | `flutter build apk --profile` |

Both `gen_snapshot` builds are deb payloads. They go under
`linux-arm64-android-{release,profile}/` because Flutter's first-run artifact
materialization rewrites `android-arm64-{release,profile}/` and would replace
anything placed there. `post_install.sh` patches `flutter_tools` to resolve the
AOT compiler to these paths, and keeps `android-arm64-{release,profile}/linux-arm64`
symlinks as a fallback for lookups that read the per-build directories directly.

Debug APKs are JIT builds and need no `gen_snapshot`.

---

### 6. On-Device Setup After Building

Installing your freshly built deb works exactly like the release install —
follow [Install guide](#install-on-device) (the manual deb method), or in
short:

```bash
dpkg -i flutter_3.50.0-4_aarch64.deb
apt --fix-broken install -y
source $PREFIX/etc/profile.d/flutter.sh
flutter doctor -v
# optional: finish what the deb's postinst skipped (it cannot call apt)
bash $PREFIX/share/flutter/post_install.sh
```

The deb's `postinst` runs `post_install.sh` automatically during installation
with `TERMUX_POSTINST=true`, which skips its `apt`/`pkg install` calls because
dpkg owns those locks. Its `preinst` also removes payload that older releases
shipped and current ones exclude (the ~490 MiB framework `.git` history and the
build-only trees). Output goes to `$PREFIX/share/flutter/post_install.log`.

`post_install.sh` handles most of the things that had to be done manually in
older build versions: Android API 34/36, cmdline-tools, build-tools symlinks,
NDK clang driver configuration, CMake toolchain patches, Dart snapshots,
Flutter Tools Android-host patches, Gradle plugin ARM64-only ABI, ELF
cleaner, shebang fixes, and cache cleanup.

Per-project setup (the APK `gradle.properties` / `build.gradle.kts` pins and
the Linux desktop `CMakeLists.txt` tweak) is covered step-by-step in the
[Install guide](#build-your-first-android-apk) — your own
build needs exactly the same per-project settings.

---

### 7. Troubleshooting

Each entry shows the exact error (**Symptom**), why it happens (**Cause**),
and the fix (**Fix**).

#### ninja build error

- **Symptom:** the `ninja` build fails in a shell/platform file.
- **Cause:** the patches were not applied correctly.
- **Fix:** verify the patch is present:

  ```bash
  cd flutter/engine/src/flutter
  git diff shell/platform/embedder/BUILD.gn
  ```

#### Patch "already exists" error

- **Symptom:** running `python3 build.py patch_*` right after `sync` fails
  with "already exists".
- **Cause:** the `.gclient` `custom_hooks` already applied the patches during
  `sync`.
- **Fix:** this is expected — do not re-apply. Use `gclient sync -D` or reset
  the checkout first if you really need to re-test a patch manually.

#### Missing dependencies at compile time

- **Symptom:** headers or libraries are missing while compiling.
- **Cause:** the sysroot was not assembled (or is stale).
- **Fix:**

  ```bash
  python3 build.py sysroot --arch=arm64
  ```

#### vpython3 not found (depot_tools issue)

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

#### openjdk-17 does not exist (Termux package issue)

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

#### libflutter_linux_gtk.so missing (Linux desktop support)

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

#### dartaotruntime missing

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

#### CRLF line-ending issue (Windows/WSL)

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

#### ADB remote installation fails

- **Symptom:** commands sent to Termux using `am broadcast` do not execute.
- **Cause:** external app execution permission is not enabled in Termux.
- **Fix:**

  ```bash
  # Run in Termux
  echo "allow-external-apps=true" >> ~/.termux/termux.properties
  termux-reload-settings
  ```

  Or just run the installation commands manually in Termux.

#### X11-related dependencies (`flutter run -d linux` has no display)

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

#### gen_snapshot version mismatch

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

#### ninja: error: 'xxx' does not exist

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

#### TLS segment underaligned (Bionic linker issue)

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

#### Insufficient disk space

- **Symptom:** the build fails with no space left on device/host.
- **Cause:** the Flutter Engine source is about 30GB and the build output
  about 20GB.
- **Fix:** keep at least 60GB free (the environment requirements above
  recommend 100GB to be safe).

---

### 8. Known Limitations

#### Android APK only supports ARM64

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

#### Runtime modes

`build.toml [build] runtime = ['release', 'debug', 'profile']`, so the deb
carries all three GTK engine variants:

| Slot | Built by | Used by |
| --- | --- | --- |
| `artifacts/engine/linux-arm64/` | debug | `flutter run -d linux` (JIT, hot reload), `flutter build linux --debug` |
| `artifacts/engine/linux-arm64-release/` | release | `flutter build linux --release`, `flutter run -d linux --release` |
| `artifacts/engine/linux-arm64-profile/` | profile | `flutter run -d linux --profile` |
| `artifacts/engine/linux-arm64-android-{release,profile}/` | `--android`, both modes | `flutter build apk --release` / `--profile` AOT |

Shared developer tools (`dart`, `dartaotruntime`,
`frontend_server_aot.dart.snapshot`, `flutter_tester`, `gen_snapshot` for
desktop AOT) come from the release configuration and live in the
`linux-arm64/` slot alongside the debug GTK library.

**Impact on users:** everything works out of the box — `flutter doctor`,
`flutter run -d linux`, `flutter build linux` in any mode, and
`flutter build apk --release|--profile`.

Dropping a mode from `runtime` does not fail the build: `package.py`'s
`explore()` logs `source not found` and skips that slot, so the deb silently
loses it.

#### Release-mode build problems (developer reference)

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

### 9. Test Verification Checklist

After the build completes, verify with the following checklist.

1. Check that the release artifacts exist on the build host:

   ```bash
   ls -la flutter/engine/src/out/linux_release_arm64/dart-sdk/bin/dart
   ls -la flutter/engine/src/out/linux_release_arm64/gen_snapshot
   ls -la flutter/engine/src/out/linux_release_arm64/libflutter_linux_gtk.so
   ls -la flutter/engine/src/out/android_release_arm64/gen_snapshot
   ls -la flutter/engine/src/out/android_profile_arm64/gen_snapshot
   ```

   If you also built `--mode=debug` or `--mode=profile`, the matching
   `linux_debug_arm64/` and `linux_profile_arm64/` directories hold those
   variants.

2. After deploying to Termux and running `post_install.sh`:

   ```bash
   flutter doctor -v
   flutter create test_app
   cd test_app
   bash $PREFIX/share/flutter/flutter_project_config.sh
   flutter build apk --release --target-platform android-arm64 --no-tree-shake-icons
   flutter build linux --release
   ```

   The release APK should open to your app, not a white screen. That last
   check is the one that catches a wrong `gen_snapshot`. `flutter build apk
   --debug` uses JIT and works too; `flutter run` needs an ADB connection.

---

### 10. Feature Test Results (3.50.0)

| Feature | Status | Description |
|------|------|------|
| `flutter --version` | ✅ OK | Flutter 3.50.0 / Tools Dart 3.14.0 |
| `dart --version` | ✅ OK | Dart 3.14.0 (`linux_arm64`) |
| `$PREFIX/opt/flutter/bin/cache/dart-sdk/bin/dartvm --version` | ✅ OK | Dart 3.14.0 (`linux_arm64`) |
| `flutter doctor -v` | ✅ OK | unknown channel / no device are expected warnings |
| `flutter create` | ✅ OK | Can create Android + Linux projects |
| `flutter build apk --release --target-platform android-arm64 --no-tree-shake-icons` | ✅ OK | Requires running post_install.sh; only supports android-arm64 |
| `flutter build linux --release` | ✅ OK | Produces an ARM64 ELF bundle |
| `flutter run` (Android) | ✅ Supported | A device is only shown after ADB connects within Termux |

---

### 11. Upgrading the Flutter Version

The upgrade runbook (bump `build.toml`, rebase patches, rebuild, test,
publish) lives in the [Upgrade guide](MAINTENANCE.md). The one-command
pipeline also handles upgrades when the patches apply cleanly:

```bash
python3 build.py
```

#### Patch maintenance tips

See [Patch Maintenance Tips](MAINTENANCE.md#patch-maintenance-tips) for the
checklist.

---

## Build internals

This document explains **why** the Flutter-for-Termux build is put together
the way it is: the pipeline stages, the patches, the GN toolchain
configuration, the sysroot, and the known issues behind them. It is a
reference for future version upgrades.

For copy-pasteable build commands, use the [Build guide](#build-from-source);
for bumping to a new Flutter release, use the
[Upgrade guide](MAINTENANCE.md).

### Why `ninja flutter` alone was not enough

#### Original problem

- `flutter run -d linux` ✓ works
- `flutter build apk --release` ✗ fails (dart version issue)

#### Root cause

**`ninja flutter` alone does not compile the dart binary!**

The `bin/cache/dart-sdk/bin/dart` binary must ship in the deb package,
otherwise:

- The flutter command fails to execute properly
- gen_snapshot version mismatch errors

#### Solution

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

#### deb package contents confirmation (after fix)

```text
✓ bin/cache/dart-sdk/bin/dart (102MB)
✓ bin/cache/artifacts/engine/linux-arm64/gen_snapshot (6.9MB)
✓ bin/cache/artifacts/engine/linux-arm64-android-release/gen_snapshot (5.3MB; Android AOT)
✓ bin/cache/artifacts/engine/linux-arm64-android-profile/gen_snapshot (5.3MB; Android AOT)
```

### Build Environment

The concrete setup steps live in the
[Build guide §1-3](#1-build-environment-requirements). The
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

### Pipeline Overview

Each stage maps to a step in the [Build guide](#3-step-by-step-build):

| Stage | Command | Notes |
|-------|---------|-------|
| 1. Sync source | `build.py clone` then `build.py sync` | ~30GB, several hours. The `.gclient` `custom_hooks` apply the patches automatically — no separate patch step |
| 2. Patches | (automatic during `sync`) | Manual `build.py patch --file=...` only to re-test/rebase against a fresh checkout |
| 3. Sysroot | `build.py sysroot --arch=arm64` | Downloads Termux runtime libraries from the official Termux apt repo |
| 4. Build Linux components | `build.py configure --arch=arm64 --mode=<m>` + `build.py build --arch=arm64 --mode=<m>` for each mode in `[build] runtime` (`release`, `debug`, `profile`) | Debug GTK powers `flutter run -d linux`; release/profile feed `build linux` and `run -d linux --release\|--profile`. The `build()` ninja invocation includes `flutter/build/archives:dart_sdk_archive`, so the dart binary is produced as part of the standard build (no separate build step) |
| 5. Android gen_snapshot | `configure_android()` / `build_android()` (`--android --android-cpu arm64`, target `flutter/third_party/dart/runtime/bin:gen_snapshot`) | Shipped for release/profile as `linux-arm64-android-{release,profile}/gen_snapshot`; post-install resolves/provisions it for `flutter build apk`. |
| 6. Package the deb | `build.py debuild --arch=arm64` | Output: `release/flutter_3.50.0-4_aarch64.deb` |

---

### Architecture

#### Build-to-device flow

```
┌─────────────────────────────────────────────────────────────────┐
│                     Linux x86-64 build host                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │ Dart SDK    │  │ Flutter     │  │ gen_snapshot            │  │
│  │ (ARM64)     │  │ engine      │  │ Linux: desktop AOT      │  │
│  │             │  │ (ARM64)     │  │ Android: APK AOT        │  │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘  │
│         │               │                    │                   │
│         └───────────────┴────────────────────┘                   │
│                         │                                        │
│                    [package .deb]                                │
│                         │                                        │
└─────────────────────────┼────────────────────────────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                 Termux runtime on Android / ARM64               │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │ our .deb    │  │ Android SDK │  │ ARM64 NDK               │  │
│  │ (installed) │  │ (Termux     │  │ (opt-in download)       │  │
│  │             │  │  packages)  │  │                         │  │
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

#### Components We Compile (WSL cross-compilation)

| Component | File | Size | Purpose |
|------|------|------|------|
| **Dart CLI** | `dart-sdk/bin/dart` | ~50MB | Flutter CLI |
| **Dart VM** | `dart-sdk/bin/dartvm`, `dartaotruntime` | ~50MB | Runs engine snapshots / AOT runtime |
| **Flutter Engine** | `libflutter_linux_gtk.so` | ~106MB | Linux desktop runtime |
| **gen_snapshot (Linux)** | `linux-arm64/gen_snapshot` | ~7MB | `flutter build linux` |
| **gen_snapshot (Android)** | `android_{release,profile}_arm64/gen_snapshot` → `linux-arm64-android-{release,profile}/` | ~5MB | `flutter build apk --release/--profile` |
| **impellerc** | `impellerc` | ~20MB | Shader compiler |
| **const_finder** | `const_finder.dart.snapshot` | ~1MB | Icon tree shaking |

#### Downloaded Precompiled Components (third-party)

| Source | Component | Purpose |
|------|------|------|
| [HomuHomu833/android-ndk-custom](https://github.com/HomuHomu833/android-ndk-custom) | aarch64 NDK repack (r30) | Native compilation; opt-in via `post_install.sh --provision-ndk` |
| Termux packages | aapt2, d8, aidl, apksigner | APK packaging (`post_install.sh` assembles the SDK side) |
| Google Storage | Dart SDK snapshots (dds_aot, etc.) | Hot reload support |

#### Applied Patches

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

### Key Modification Notes

#### 1. Termux Toolchain Configuration

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

#### 2. Bionic Linker Issue

**Problem**: When the dart binary uses the glibc linker, the following appears:

```text
error: "dart": executable's TLS segment is underaligned
```

**Solution**: Add `--dynamic-linker=/system/bin/linker64` to
`executable_ldconfig` (see above). Full symptom/fix details, including the
rebuild commands, are in the
[Build guide troubleshooting](#tls-segment-underaligned-bionic-linker-issue).

#### 3. Dependencies Issue

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

### Known Issues and Solutions

Each entry below records the underlying cause and fix direction; matching
symptom/fix entries live in the
[Build guide troubleshooting](#7-troubleshooting).

#### Issue 1: vpython3 not found

**Symptoms**:

```text
/bin/sh: vpython3: not found
```

**Solution**: depot_tools' `vpython3` is a symlink pointing to a broken
`vpython`; replace it with a wrapper script — see
[vpython3 not found](#vpython3-not-found-depot_tools-issue).

#### Issue 2: Release Mode Build Failure (sysroot Conflict)

**Symptoms**:

```text
error: typedef redefinition with different types ('__mbstate_t' vs 'struct mbstate_t')
```

**Cause**: The sysroot contains both glibc and bionic headers

**Current Status**: All three runtime modes are built and packaged —
`build.toml [build] runtime = ['release', 'debug', 'profile']` — so the deb
ships the debug GTK engine (`linux-arm64/`, used by `flutter run -d linux`),
plus release and profile. The sysroot header conflict below only appears when
mixing modes against a dirty `sysroot/` tree; `python3 build.py sysroot
--arch=arm64` rebuilds it.

**Future Fix Direction**: Clean up the sysroot and keep only the bionic headers

#### Issue 3: flutter build apk Failure (dedup_instructions Error)

**Symptoms**:

```text
Flag dedup_instructions is false in snapshot, but dedup_instructions is always true in product mode
```

**Cause**:

- `dartaotruntime_product` expects a product-mode snapshot
- `frontend_server_aot.dart.snapshot` was built in debug mode

**Future Fix Direction**: Need to build a release-mode
`frontend_server_aot.dart.snapshot`

#### Issue 4: android-arm/android-x64 gen_snapshot Cannot Be Built

**Cause**:

- android-arm: BoringSSL has a 32-bit shift overflow error
- android-x64: The ARM64 sysroot is incompatible with x64 compilation

**Conclusion**: Only the android-arm64 target is supported

---

### Testing Process

#### Test Device Preparation

1. ARM64 Android device (Android 11+)
2. Install Termux (from F-Droid)
3. Ensure there is an SSH or ADB connection

#### Test Steps

```bash
# 1. Transfer the deb to the device
# Use PowerShell (Git Bash will corrupt the path)
adb push flutter_3.50.0-4_aarch64.deb /sdcard/Download/

# 2. Install it in Termux
pkg install x11-repo
dpkg -i /sdcard/Download/flutter_3.50.0-4_aarch64.deb
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

#### Test Result Record

| Test Item | Expected Result | Actual Result | Notes |
|----------|----------------|---------------|-------|
| flutter doctor | Shows version info | | |
| flutter create | Successfully creates a project | | |
| flutter build apk | Successfully builds an APK | | |
| flutter build linux | Successfully builds | | |

---

### Release Process

#### 1. Version Check

Confirm that the version numbers in the following files are consistent:
- `build.toml` - `tag` field
- `scripts/install/versions_common.sh` - installer version pins (`FLUTTER_VERSION`, checksums)
- `README.md` - version badge and text
- `package.yaml` - `Version: $package_version`

> CI enforces this: `scripts/ci/check_version_drift.py` and
> `scripts/ci/check_repo.py` fail the build on drift, and
> `scripts/ci/generate_versions.py --check` requires `versions_common.sh` to
> match `build.toml`.

#### 2. Build Artifact

```bash
# Final artifact location
release/flutter_3.50.0-4_aarch64.deb
```

#### 3. Upload to GitHub Releases

1. Create a new Release: `3.50.0`
2. Upload the deb file: `flutter_3.50.0-4_aarch64.deb`
3. Fill in the Release Notes

#### 4. Verify the Release

Install the published release in a fresh Termux environment following the
[Install guide](#install-on-device) (apt repository method), then build a debug
APK to confirm the toolchain end-to-end.

For upgrading to a new Flutter version, follow the
[Upgrade guide](MAINTENANCE.md).

#### 5. Publish the flutter Stub

The `pkg install flutter` path is served by the unified
[termux-apt-repo](https://github.com/GeneralKaos666/termux-apt-repo). After the
release carries the payload `.deb`, regenerate the stub with
`scripts/release/apt_repo.py stub` and drop the resulting
`flutter_<ver>_all.deb` into termux-apt-repo's `debs/`, then republish that
repository.

---

### File Structure Reference

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
│   ├── release/          # flutter stub builder: apt_repo.py + stub/ maintainer scripts
│   └── test/             # Release E2E smoke scripts (gh_e2e_test.sh)
├── docs/
│   ├── BUILD.md          # install / build / build internals (you are here)
│   ├── MAINTENANCE.md    # upgrade, CI/CD, device lab, AAPT2 analysis
│   └── releases/         # CHANGELOG.md, RELEASE_NOTES.md
├── README.md             # English (primary) documentation
├── sysroot/              # Termux runtime deps (generated at build)
├── flutter/              # Flutter source (cloned at build)
│   └── engine/src/out/   # build artifacts
└── release/              # final deb package
```

---
