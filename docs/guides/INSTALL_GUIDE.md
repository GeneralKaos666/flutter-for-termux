# Install Guide — Flutter 3.47.8 on Termux

This guide walks you through installing `flutter_3.47.8-7_aarch64.deb` on an
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
| Flutter | 3.47.8 |
| Flutter Tools Dart | 3.13.7 |
| Dart VM (`dartvm`) | Dart 3.13.7 (`linux_arm64`) |
| Test device | Samsung SM-X716B / Android 16 / ARM64 |
| deb size | 640,495,672 bytes (about 611 MiB) |
| SHA256 | `eac81132f6060f4691cba05b86d445de79abb247ef8dd9aecf7e303f3e872474` |

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
[Upgrade guide](UPGRADE_GUIDE.md).

The payload ships without the Android NDK (~550 MB), so pure-Dart projects
work out of the box while native-plugin projects print a clear hint. To
provision the pinned Termux-arm64 NDK (opt-in, idempotent, SHA256-verified):

```bash
bash $PREFIX/share/flutter/post_install.sh --provision-ndk
```

---

## Method 2: Manual deb install

Run each step in order inside Termux.

1. Install the prerequisites:

   ```bash
   pkg update -y
   pkg install -y x11-repo git wget curl unzip openjdk-21 aapt2 android-tools cmake ninja clang
   ```

2. Download the deb and verify its checksum:

   ```bash
   cd ~
   wget https://github.com/GeneralKaos666/flutter-for-termux/releases/download/3.47.8/flutter_3.47.8-7_aarch64.deb
   # Confirm the output matches: eac81132f6060f4691cba05b86d445de79abb247ef8dd9aecf7e303f3e872474
   sha256sum flutter_3.47.8-7_aarch64.deb
   ```

   Expected output — the hash must match exactly:

   ```text
   eac81132f6060f4691cba05b86d445de79abb247ef8dd9aecf7e303f3e872474  flutter_3.47.8-7_aarch64.deb
   ```

3. Install the package and repair any missing dependencies:

   ```bash
   dpkg -i flutter_3.47.8-7_aarch64.deb
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
| Dart | Keeps the engine-built `dart`, `dartvm`, and `dartaotruntime` in the SDK cache so the Flutter CLI and its snapshots stay in step |
| Flutter Tools | Patches the Android/Termux host lookup and clears stale `flutter_tools` snapshots/cache |
| Gradle plugin | ARM64-only ABI; adds the `PLATFORM_ABI_LIST` that Flutter 3.44 needs |
| Android SDK | Installs/patches API 36 (falling back 36 → 35 → 34), cmdline-tools, build-tools, licenses |
| NDK | Configures the clang driver and host-tag aliases, patches the CMake toolchain, replaces objcopy/strip |
| AOT `gen_snapshot` | Points `flutter build apk` at the Android-target `gen_snapshot` shipped in the package, and keeps the per-build symlinks as a fallback |
| Linux desktop | Enables `flutter build linux` to run on the Termux host |

---

## Verify the Installation

Run all four checks:

```bash
flutter --version
dart --version
$PREFIX/opt/flutter/bin/cache/dart-sdk/bin/dartvm --version
flutter doctor -v
```

Expected highlights:

- `flutter --version` shows Flutter 3.47.8.
- `dart --version` shows Dart 3.13.7 on `linux_arm64` (the SDK's own Dart).
- The SDK's `dartvm` shows Dart 3.13.7 on `linux_arm64` (the engine VM). It is
  not on `PATH`; use the full path above.
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
  cache is stale — common after updating to the 3.47.8 deb.
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

### Release APK builds but shows a blank screen

- **Symptom:** `flutter build apk --release` completes and the APK installs,
  but the app opens to a white/blank screen; logcat may report an unexpected
  kernel or unhandled snapshot format.
- **Cause:** Android AOT snapshots must be produced by a `gen_snapshot` built
  for Android (`target_os=android`, compressed pointers). The Linux desktop
  `gen_snapshot` shipped in packages up to `3.47.8-6` produces snapshots the
  Android VM rejects ([issue #41](https://github.com/GeneralKaos666/flutter-for-termux/issues/41)).
- **Fix (`3.47.8-7` and newer):** upgrade, re-run post-install, and rebuild:

  ```bash
  apt upgrade flutter
  bash $PREFIX/share/flutter/post_install.sh
  cd myapp && flutter clean
  flutter build apk --release --target-platform android-arm64 --no-tree-shake-icons
  ```

  `3.47.8-7` ships the Android-target binary at
  `bin/cache/artifacts/engine/linux-arm64-android-{release,profile}/gen_snapshot`,
  and post-install patches `flutter_tools` to resolve it directly (marker
  `Termux: prefer package-shipped aarch64 gen_snapshot`). Confirm it is
  present with:

  ```bash
  ls -l $PREFIX/opt/flutter/bin/cache/artifacts/engine/linux-arm64-android-release/gen_snapshot
  ```

### First build fails: `gen_snapshot` not found

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
- **Fix (`3.47.8-6` and newer):** nothing to do for this failure. Post-install
  resolves `gen_snapshot` to the package-shipped ARM64 binary, in a directory
  Flutter never writes, so the first build completes. The step-15 symlinks
  remain only as a fallback.
- **Heads-up (`3.47.8-6` only):** that package shipped the *Linux* ARM64
  `gen_snapshot` for this path, so the build succeeded but the release APK
  white-screened. `3.47.8-7` ships the Android-target binary — see
  [Release APK builds but shows a blank screen](#release-apk-builds-but-shows-a-blank-screen).
- **Fix (`3.47.8-5` and older):** upgrade to the current package, or re-run
  post-install to recreate the symlink, then build again:

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
