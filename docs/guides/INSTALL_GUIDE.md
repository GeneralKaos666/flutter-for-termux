# Install Guide — Flutter 3.47.7 on Termux

This guide walks you through installing `flutter_3.47.7-6_aarch64.deb` on an
ARM64 Termux device, verifying it, and building your first app. After
installing you will have:

- `flutter doctor -v`
- `flutter create`
- `flutter build apk --release`
- `flutter build linux --release`
- `flutter run` + hot reload (requires an ADB device connection)

## Verified Versions

| Item | Value |
|------|-------|
| Flutter | 3.47.7 |
| Flutter Tools Dart | 3.13.5 |
| Dart VM (`dartvm`) | post-install `dartvm` resolves to Dart 3.13.5 (`android_arm64`) |

| Test device | Samsung SM-X716B / Android 16 / ARM64 |
| deb size | 635,042,964 bytes (about 606 MiB) |
| SHA256 | `3f62be6aad385092b3c4dd760080279c4b78ab292822c3fec8ba764eb88208a6` |

## System Requirements

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

## Method 1: apt install (Recommended)

Add the signed repository once, then install Flutter with APT. The
[README quick start](../README.md#quick-start) carries the same snippet plus the
signing-key fingerprint to compare against.

```bash
pkg install -y gnupg
mkdir -p "$PREFIX/etc/apt/keyrings"
curl -fsSL https://GeneralKaos666.github.io/flutter-for-termux/key.gpg \
  | gpg --dearmor > "$PREFIX/etc/apt/keyrings/flutter.gpg"
echo "deb [signed-by=$PREFIX/etc/apt/keyrings/flutter.gpg] https://GeneralKaos666.github.io/flutter-for-termux termux main" \
  > "$PREFIX/etc/apt/sources.list.d/flutter.list"
pkg update && pkg install flutter
flutter doctor
```

`pkg install flutter` pulls the stub package, which downloads the release archive,
verifies its checksum and size, unpacks it into `$PREFIX`, and runs
`post_install.sh` for you. `apt policy flutter` shows the candidate version and
its source. Later releases arrive with `apt upgrade flutter` — see the
[Upgrade guide](UPGRADE_GUIDE.md).

The payload ships without the Android NDK (~550 MB), so pure-Dart projects
work out of the box while native-plugin projects print a clear hint. To
provision the pinned Termux-arm64 NDK (opt-in, idempotent, SHA256-verified):

```bash
bash $PREFIX/share/flutter/post_install.sh --provision-ndk
```

---

## Method 2: Legacy curl installer (deprecated)

1. Download and run the installer:

   ```bash
   curl -sL https://raw.githubusercontent.com/GeneralKaos666/flutter-for-termux/main/install_flutter_complete.sh -o ~/install.sh
   bash ~/install.sh
   ```

   This script installs Flutter, the Android SDK/NDK, the required Termux
   packages, and runs post-install for you. It is deprecated and receives no new
   features — prefer [Method 1](#method-1-apt-install-recommended).

2. Wait for it to finish. The first run downloads a large amount of data, so
   keep the screen on and the network stable.

3. Continue with [Verify the Installation](#verify-the-installation).

---

## Method 3: Manual deb install

Run each step in order inside Termux.

1. Install the prerequisites:

   ```bash
   pkg update -y
   pkg install -y x11-repo git wget curl unzip openjdk-21 aapt2 android-tools cmake ninja clang
   ```

2. Download the deb and verify its checksum:

   ```bash
   cd ~
   wget https://github.com/GeneralKaos666/flutter-for-termux/releases/download/3.47.7/flutter_3.47.7-6_aarch64.deb
   sha256sum flutter_3.47.7-6_aarch64.deb
   ```

   Expected output — the hash must match exactly:

   ```text
   3f62be6aad385092b3c4dd760080279c4b78ab292822c3fec8ba764eb88208a6  flutter_3.47.7-6_aarch64.deb
   ```
   # Confirm the output matches: 3f62be6aad385092b3c4dd760080279c4b78ab292822c3fec8ba764eb88208a6

3. Install the package and repair any missing dependencies:

   ```bash
   dpkg -i flutter_3.47.7-6_aarch64.deb
   apt --fix-broken install -y
   ```

4. Run post-install. **This step is required** — `dpkg` only copies files;
   post-install patches the Termux runtime so Flutter actually works:

   ```bash
   bash $PREFIX/share/flutter/post_install.sh
   ```

5. Load the Flutter environment into your shell and check the install:

   ```bash
   source $PREFIX/etc/profile.d/flutter.sh
   flutter doctor -v
   ```

6. Continue with [Verify the Installation](#verify-the-installation).

### Expected `flutter doctor` warnings (not errors)

These two warnings are normal and can be ignored:

- **unknown channel / unknown upstream source** — the Flutter SDK in the deb
  is not an official git remote checkout.
- **no connected device** — no ADB device is connected in Termux yet; this
  does not affect `flutter build apk`.

---

## What post_install.sh Does

| Category | Details |
|----------|---------|
| Dart | Runs the Flutter CLI with Termux JIT Dart, keeping the engine `dartvm` / `dartaotruntime` for snapshots |
| Flutter Tools | Patches the Android/Termux host lookup and clears stale `flutter_tools` snapshots/cache |
| Gradle plugin | ARM64-only ABI; adds the `PLATFORM_ABI_LIST` that Flutter 3.44 needs |
| Android SDK | Installs/patches API 34/36, cmdline-tools, build-tools, licenses |
| NDK | Configures the clang driver and host-tag aliases, patches the CMake toolchain, replaces objcopy/strip |
| Linux desktop | Enables `flutter build linux` to run on the Termux host |

---

## Verify the Installation

Run all four checks:

```bash
flutter --version
dart --version
dartvm --version
flutter doctor -v
```

Expected highlights:

- `flutter --version` shows Flutter 3.47.7.
- `dart --version` shows `android_arm64` (Termux JIT Dart).
- `dartvm --version` shows `linux_arm64` (engine VM).
- `flutter doctor -v` may show the two expected warnings listed above.

---

## Build Your First Android APK

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

## Build Your First Linux Desktop App

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

## flutter run / Hot Reload

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

## Troubleshooting

Each entry below shows the exact error (**Symptom**), why it happens
(**Cause**), and the fix.

### `PLATFORM_ABI_LIST` unresolved

- **Symptom:** a Kotlin unresolved-reference error for `PLATFORM_ABI_LIST`
  while building an APK.
- **Cause:** the post-install Flutter Gradle plugin template or the Gradle
  cache is stale — common after updating to the 3.47.7 deb.
- **Fix:** re-run post-install and clear the Gradle caches, then rebuild:

  ```bash
  bash $PREFIX/share/flutter/post_install.sh
  ./android/gradlew --stop || true
  rm -rf ~/.gradle/caches .gradle android/.gradle build android/app/build
  flutter build apk --release --target-platform android-arm64 --no-tree-shake-icons
  ```

### AAPT2 / compileSdk errors

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

### NDK or CMake cannot find the compiler

- **Symptom:** native plugin compilation fails with a missing compiler error.
- **Cause:** Gradle may have downloaded a new NDK or build-tools that
  post-install has not configured yet.
- **Fix:** re-run post-install:

  ```bash
  bash $PREFIX/share/flutter/post_install.sh
  ```

### `did not have a source.properties file` (NDK metadata error)

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

### `Could not find toolchain file` (incomplete NDK)

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

### First build fails: `gen_snapshot` not found (host symlink dropped)

- **Symptom:** the first `flutter build apk --release` after a (re)install
  fails during AOT with:

  ```text
  Target android_aot_release_android-arm64 failed: ProcessException:
  Failed to find ".../engine/android-arm64-release/linux-arm64/gen_snapshot" in the search path.
  ```

- **Cause:** the first build materializes Flutter's engine artifacts and
  recreates the `android-arm64-*` directories, dropping the
  `linux-arm64` → `../linux-arm64` host symlink that post-install creates
  (step 15). The `linux-x64` copy upstream ships is x86-64 and cannot run on
  this device, so AOT cannot find a runnable `gen_snapshot`.
- **Fix (3.47.7-6 and newer):** nothing to do — post-install now patches
  flutter_tools to resolve `gen_snapshot` directly to the ARM64 binary
  shipped in the package (marker `Termux: prefer package-shipped aarch64
  gen_snapshot`), in a directory Flutter's first-run materialization never
  touches. Verified end-to-end: with the symlinks removed and the
  materialization triggered, `flutter build apk --release` produces the APK.
- **Fix (3.47.7-5 and older):** upgrade via apt (`pkg install flutter`), or
  re-run post-install (recreates the symlink), then build again — this
  exact sequence is verified to produce `app-release.apk`:

  ```bash
  bash $PREFIX/share/flutter/post_install.sh
  flutter build apk --release
  ```

### Stale Gradle or Kotlin errors after upgrading

- **Symptom:** old Gradle or Kotlin errors persist after installing a new deb.
- **Cause:** cached Gradle state from the previous version.
- **Fix:** re-run post-install and clear the Gradle caches:

  ```bash
  bash $PREFIX/share/flutter/post_install.sh
  rm -rf ~/.gradle/caches ~/.gradle/daemon
  ```

### Out of storage space

- **Symptom:** the build fails with no space left on device.
- **Cause:** Gradle caches and downloaded packages grow large.
- **Fix:**

  ```bash
  rm -rf ~/.gradle/caches ~/.gradle/wrapper ~/.pub-cache/hosted
  pkg clean
  ```

### Need an arm / x64 APK

- **Symptom:** you need a 32-bit ARM or x86_64 APK.
- **Cause:** not supported at the moment — this project only provides the
  ARM64 Android target.
- **Fix:** build explicitly for ARM64:

  ```bash
  flutter build apk --release --target-platform android-arm64
  ```
