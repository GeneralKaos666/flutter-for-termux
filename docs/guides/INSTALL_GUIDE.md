# Termux Flutter 3.47.7 Installation Guide

This guide covers `flutter_3.47.7-5_aarch64.deb`, targeting the following on ARM64 Termux:

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
| deb size | 607,509,252 bytes (about 579 MiB) |
| SHA256 | `8f399d6c4a2a5e77008a0f68763a829f8b86578acad50837033774ce3fcf582e` |

## System Requirements

| Item | Requirement |
|------|-------------|
| Android | Android 11 (API 30) or higher |
| CPU | ARM64 / aarch64 |
| Termux | F-Droid build or official GitHub release build recommended |
| Storage | At least 5GB; 8GB+ recommended for a full Android SDK/NDK + Gradle cache |
| Java | `openjdk-21` |

## Method 1: One-Command Install (Recommended)

```bash
curl -sL https://raw.githubusercontent.com/GeneralKaos666/flutter-for-termux/main/install_flutter_complete.sh -o ~/install.sh
bash ~/install.sh
```

This script installs Flutter, the Android SDK/NDK, the required Termux packages, and runs post-install. The first run downloads a large amount of data, so keep the screen on and the network stable.

## Method 2: Manual deb Install

```bash
pkg update -y
pkg install -y x11-repo git wget curl unzip openjdk-21 aapt2 android-tools cmake ninja clang

cd ~
wget https://github.com/GeneralKaos666/flutter-for-termux/releases/download/3.47.7/flutter_3.47.7-5_aarch64.deb
sha256sum flutter_3.47.7-5_aarch64.deb
# Confirm the output matches: 8f399d6c4a2a5e77008a0f68763a829f8b86578acad50837033774ce3fcf582e

dpkg -i flutter_3.47.7-5_aarch64.deb
apt --fix-broken install -y

# Required: dpkg only installs files; this step patches the Termux runtime.
bash $PREFIX/share/flutter/post_install.sh

source $PREFIX/etc/profile.d/flutter.sh
flutter doctor -v
```

These warnings in `flutter doctor` are expected:

- unknown channel / unknown upstream source: the Flutter SDK in the deb is not an official git remote checkout.
- no connected device: no ADB device connected in Termux yet; this does not affect `flutter build apk`.

## What post_install.sh Does

| Category | Details |
|----------|---------|
| Dart | Runs the Flutter CLI with Termux JIT Dart, keeping the engine `dartvm` / `dartaotruntime` for snapshots |
| Flutter Tools | Patches the Android/Termux host lookup and clears stale `flutter_tools` snapshots/cache |
| Gradle plugin | ARM64-only ABI; adds the `PLATFORM_ABI_LIST` that Flutter 3.44 needs |
| Android SDK | Installs/patches API 34/36, cmdline-tools, build-tools, licenses |
| NDK | Configures the clang driver and host-tag aliases, patches the CMake toolchain, replaces objcopy/strip |
| Linux desktop | Enables `flutter build linux` to run on the Termux host |

If you still see old Gradle or Kotlin errors after upgrading, re-run:

```bash
bash $PREFIX/share/flutter/post_install.sh
rm -rf ~/.gradle/caches ~/.gradle/daemon
```

## Verify the Installation

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

## Create an Android APK Project (Mode A: Local Build as an Example)

Create the project and fix the shebang:

```bash
flutter create myapp
cd myapp
sed -i '1s|#!/usr/bin/env bash|#!/data/data/com.termux/files/usr/bin/bash|' android/gradlew
```

1. Set the project Gradle properties (in `android/gradle.properties`):

```properties
android.aapt2FromMavenOverride=/data/data/com.termux/files/usr/bin/aapt2
android.enableResourceOptimizations=false
shrink=false
org.gradle.jvmargs=-Xmx2048m -XX:MaxMetaspaceSize=512m -Dfile.encoding=UTF-8
```

2. Edit `android/app/build.gradle.kts` (set the SDK and disable minification/shrinking):

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

3. Build (note: you must bypass the icon tree shaking restricted by JIT Dart):

```bash
flutter build apk --release --target-platform android-arm64 --no-tree-shake-icons
```

Artifacts:

```text
build/app/outputs/flutter-apk/app-release.apk
```

## Create a Linux Desktop Project

```bash
flutter create mylinux --platforms=linux
cd mylinux
sed -i '1i set(CMAKE_SYSTEM_NAME Linux)' linux/CMakeLists.txt
flutter build linux --release
```

Artifacts:

```text
build/linux/arm64/release/bundle/
```

## flutter run / Hot Reload

`flutter run` requires ADB inside Termux to see the Android device. If you are using the same tablet/phone, Android "wireless debugging" is recommended:

```bash
pkg install android-tools
adb pair 127.0.0.1:<pair_port>
adb connect 127.0.0.1:<connect_port>
flutter devices
flutter run -d <device_id>
```

If `flutter doctor` shows no connected device, it only means ADB is not connected yet, not that the SDK is broken.

## Common Problems

### `PLATFORM_ABI_LIST` unresolved

This means the post-install Flutter Gradle plugin template or the Gradle cache is stale. After updating to the 3.47.7 deb, run:

```bash
bash $PREFIX/share/flutter/post_install.sh
./android/gradlew --stop || true
rm -rf ~/.gradle/caches .gradle android/.gradle build android/app/build
flutter build apk --release --target-platform android-arm64 --no-tree-shake-icons
```

### AAPT2 / compileSdk Errors

Always use API 36 and point to the Termux ARM64 aapt2 (post-install falls back to 35/34 if aapt2 cannot load android-36):

```properties
android.aapt2FromMavenOverride=/data/data/com.termux/files/usr/bin/aapt2
```

```kotlin
compileSdk = 36
defaultConfig { targetSdk = 36 }
```

### NDK or CMake Cannot Find the Compiler

Gradle may have downloaded a new NDK or build-tools. Re-run post-install:

```bash
bash $PREFIX/share/flutter/post_install.sh
```

### `did not have a source.properties file` (NDK metadata error)

If `flutter build apk`/`flutter build appbundle` fails while configuring a native plugin with:

```text
[CXX1101] NDK at .../ndk/<version> did not have a source.properties file
```

the installed NDK directory is incomplete. AGP requires
`<sdk>/ndk/<version>/source.properties` to identify the NDK, and an
interrupted or partially extracted install can leave that file missing.
Re-run post-install, which validates every installed NDK and regenerates the
missing metadata when the rest of the directory is usable:

```bash
bash $PREFIX/share/flutter/post_install.sh
```

If the directory is incomplete (no `toolchains/llvm`, or no CMake toolchain
file), post-install removes it rather than stamping it healthy. Re-running
post-install cannot rebuild a removed directory — the full installer is what
downloads and extracts the NDK:

```bash
curl -sL https://raw.githubusercontent.com/GeneralKaos666/flutter-for-termux/main/install_flutter_complete.sh -o ~/install.sh && bash ~/install.sh
```

### `Could not find toolchain file` (incomplete NDK)

If `flutter build apk`/`flutter build appbundle` fails while configuring a
native plugin (`:jni`, or any plugin using `externalNativeBuild`) with:

```text
CMake Error at .../CMakeDetermineSystem.cmake:165 (message):
  Could not find toolchain file:
  ".../ndk/<version>/build/cmake/android.toolchain.cmake"
```

the NDK directory is only partially extracted: it still has `toolchains/llvm`,
but the CMake toolchain files are gone. This is a different failure from the
`source.properties` error above and it appears *after* that metadata exists, so
re-running post-install will not repair it — post-install detects the directory
as incomplete and removes it. Re-provision the NDK with the full installer:

```bash
curl -sL https://raw.githubusercontent.com/GeneralKaos666/flutter-for-termux/main/install_flutter_complete.sh -o ~/install.sh && bash ~/install.sh
```

This re-downloads the NDK (~550 MB). A complete NDK is verified to configure,
compile, and link an `arm64-v8a` CMake module (`jni` 1.0.0) on-device, so the
build proceeds once the toolchain file is back.

### Out of Storage Space

```bash
rm -rf ~/.gradle/caches ~/.gradle/wrapper ~/.pub-cache/hosted
pkg clean
```

### Need an arm / x64 APK

Not supported at the moment. This project only provides the ARM64 Android target, so use:

```bash
flutter build apk --release --target-platform android-arm64
```