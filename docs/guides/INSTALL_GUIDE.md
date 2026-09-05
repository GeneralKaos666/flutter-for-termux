**English** | [中文](INSTALL_GUIDE_ZH.md)

# Termux Flutter 3.47.2 Installation Guide

This guide covers `flutter_3.47.2_aarch64.deb`, targeting the following on ARM64 Termux:

- `flutter doctor -v`
- `flutter create`
- `flutter build apk --release`
- `flutter build linux --release`
- `flutter run` + hot reload (requires an ADB device connection)

## Verified Versions

| Item | Value |
|------|-------|
| Flutter | 3.47.2 |
| Flutter Tools Dart | 3.13.2 |
| Dart VM (`dartvm`) | post-install `dartvm` resolves to Dart 3.13.2 (`android_arm64`) |

| Test device | Samsung SM-X716B / Android 16 / ARM64 |
| deb size | 177,161,976 bytes (about 169 MiB) |
| SHA256 | `4443a27c2f528cb093fedcdb994e9f3342147669699c5d695fd2da6d553b98e0` |

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
wget https://github.com/GeneralKaos666/flutter-for-termux/releases/download/3.47.2/flutter_3.47.2_aarch64.deb
sha256sum flutter_3.47.2_aarch64.deb
# Confirm the output matches: 4443a27c2f528cb093fedcdb994e9f3342147669699c5d695fd2da6d553b98e0

dpkg -i flutter_3.47.2_aarch64.deb
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
| NDK | Creates clang wrappers, patches the CMake host tag, replaces objcopy/strip |
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

- `flutter --version` shows Flutter 3.47.2.
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
    compileSdk = 34

    defaultConfig {
        targetSdk = 34
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

This means the post-install Flutter Gradle plugin template or the Gradle cache is stale. After updating to the 3.47.2 deb, run:

```bash
bash $PREFIX/share/flutter/post_install.sh
./android/gradlew --stop || true
rm -rf ~/.gradle/caches .gradle android/.gradle build android/app/build
flutter build apk --release --target-platform android-arm64 --no-tree-shake-icons
```

### AAPT2 / compileSdk Errors

Always use API 34 and point to the Termux ARM64 aapt2:

```properties
android.aapt2FromMavenOverride=/data/data/com.termux/files/usr/bin/aapt2
```

```kotlin
compileSdk = 34
defaultConfig { targetSdk = 34 }
```

### NDK or CMake Cannot Find the Compiler

Gradle may have downloaded a new NDK or build-tools. Re-run post-install:

```bash
bash $PREFIX/share/flutter/post_install.sh
```

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