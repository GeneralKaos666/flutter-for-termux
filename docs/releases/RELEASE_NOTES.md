# Flutter 3.47.5 for Termux ARM64

**Flutter 3.47.5 / Dart 3.13.4 for Android-bionic ARM64 hosts.**

This release updates the Termux Flutter SDK package to Flutter 3.47.5. It incorporates all post-v3.44.2 installer hardening, dynamic JAVA_HOME auto-detection, robust PREFIX quoting under `set -euo pipefail`, and refreshed Termux toolchain sysroot packages.

## Package

| Item | Value |
|------|-------|
| Package | `flutter_3.47.5-1_aarch64.deb` |
| Size | 617,009,288 bytes (~588 MiB) |
| SHA256 | `6994580359002c6e0f6eb074d17a8ab3f9578e480e2aad83aa443474da3c9800` |
| Flutter | 3.47.5 |
| Flutter Tools Dart | 3.13.4 |
| Dart VM | post-install `dartvm` resolves to Dart 3.13.4 (`android_arm64`) |
| Target host | Termux / Android bionic / ARM64 |

## Install

```bash
pkg update -y
pkg install -y x11-repo wget openjdk-21 7zip
wget https://github.com/GeneralKaos666/flutter-for-termux/releases/download/3.47.5/flutter_3.47.5-1_aarch64.deb
dpkg -i flutter_3.47.5-1_aarch64.deb
apt --fix-broken install -y
bash $PREFIX/share/flutter/post_install.sh
source $PREFIX/etc/profile.d/flutter.sh
flutter doctor -v
```

## Verified

Device smoke on Samsung SM-X716B / Android 16 / ARM64 Termux:

| Command | Result |
|---------|--------|
| `flutter --version` | ✅ Flutter 3.47.5 |
| `dart --version` | ✅ Dart 3.13.4 on `android_arm64` |
| `dartvm --version` | ✅ Dart 3.13.4 on `linux_arm64` |
| `flutter doctor -v` | ✅ completes; unknown channel / no connected device are expected warnings |
| `flutter create --platforms=android,linux` | ✅ |
| `flutter build apk --release --target-platform android-arm64 --no-tree-shake-icons` | ✅ ARM64 APK produced |
| `flutter build linux --release` | ✅ ARM64 Linux bundle produced |
| deb artifact validator | ✅ `dart`, `dartvm`, `dartaotruntime` executable |

## Highlights

### Flutter 3.47.5 update

- Updated package metadata, NDK configurations, and patches to target Flutter 3.47.5 (Dart 3.13.4).
- Keeps Flutter CLI on Termux JIT Dart while preserving engine VM tools for snapshots.

### Installer & Environment Hardening

- Fully guarded `$PREFIX` paths against whitespace and `set -u` unbound variable errors.
- Dynamic `JAVA_HOME` discovery across Termux OpenJDK installations.
- Automated dependency resolution including OpenJDK 21 and 7zip.

### Post-install Dart VM detection fix

- Fixed the `post_install.sh` system Dart VM replacement logic to directly inspect the target path (`/data/data/com.termux/files/usr/bin/dart`) rather than using `command -v`, preventing path shadowing issues.

### Technical Details

- Build output directories: `linux_debug_arm64/`, `linux_release_arm64/`, `linux_profile_arm64/`, `android_release_arm64/`, `android_profile_arm64/`
- Deb package size is ~588MB.

## Required per-project Android settings

To build APKs successfully on Termux, you must configure the following project properties:

```properties
# android/gradle.properties
android.aapt2FromMavenOverride=/data/data/com.termux/files/usr/bin/aapt2
android.enableResourceOptimizations=false
```

```kotlin
// android/app/build.gradle.kts
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

Build with:

```bash
flutter build apk --release --target-platform android-arm64 --no-tree-shake-icons
```

## Known limitations

- Android APK targets are ARM64-only (`android-arm64` / `arm64-v8a`).
- `flutter run` for Android requires ADB pairing/connection from inside Termux.
- Some Flutter doctor warnings about unknown channel/source are expected for this repackaged SDK.
- Termux aapt2 (16.0.0.4) is used via `android.aapt2FromMavenOverride`; projects default to `compileSdk`/`targetSdk` 36 with a fail-closed fallback to 35/34 when aapt2 cannot load the newest platform.

## Previous releases

### v3.41.5 (2026-04-13)

- Flutter SDK upgraded to 3.41.5 (Dart 3.11.3).
- Added `flutter build linux` support.
- Fixed post-install sed delimiter and flutter_tools snapshot invalidation.

### v3.35.0 (2026-01-07)

- First public release.
- APK build and hot reload support for ARM64 Termux.
