# Flutter 3.47.8 for Termux ARM64

**Flutter 3.47.8 / Dart 3.13.7 for Android-bionic ARM64 hosts.**

This release updates the Termux Flutter SDK package to Flutter 3.47.8. It incorporates all post-v3.44.2 installer hardening, dynamic JAVA_HOME auto-detection, robust PREFIX quoting under `set -euo pipefail`, and refreshed Termux toolchain sysroot packages.

## Fixed in 3.47.8-4

`3.47.8-3` could fail its own post-install APK smoke test and roll the installation back ([issue #27](https://github.com/GeneralKaos666/flutter-for-termux/issues/27)). `3.47.8-4` fixes this:

- **Smoke test.** The installer's smoke project was generated with an outdated Gradle DSL (`kotlin-android` plugin, `kotlinOptions { jvmTarget }`) that AGP 9.1.0 / Kotlin 2.4.0 reject; it now uses the current Flutter 3.47.8 DSL.
- **Diagnostics.** If the smoke build ever fails, the installer now prints the relevant Gradle log lines and reports a failed `flutter create`, instead of a bare `✗ APK build failed`.
- **NDK default.** `post_install.sh` pins the Flutter Gradle plugin default `ndkVersion` (and the create-time Dart constant) to the NDK shipped in this package (`29.0.14206865`). Projects no longer trigger AGP to auto-download the x86-64-only `android-ndk-r28c-linux`, whose tools cannot run on ARM64 Termux.

Installing `3.47.8-4` over `3.47.8-3` is a drop-in replacement — follow the [Install](#install) steps. If a previous attempt rolled back and removed the SDK, reinstall from scratch with the same commands.

## Fixed in 3.47.8-5

`3.47.8-5` is an installer, manifest, and packaging hardening revision. `3.47.8-5` fixes:

- **Manifest.** The rendered `manifest.json` `engine_revision` no longer carries a trailing newline. The engine revision is now stripped when read, so the manifest is valid JSON and `post_install.sh`'s revision check matches instead of silently failing.
- **Settings.** `~/.flutter_settings` is now always valid JSON. The old rewrite could append `"android-sdk"` after a trailing comma and produce a doubled comma; it now loads, updates, and rewrites the file as JSON (falling back to `cat` when `python3` is unavailable).
- **Snapshot reporting.** Both `install_flutter_complete.sh` and the deb-shipped `post_install.sh` print the `✓ Dart SDK snapshots installed` banner only when the extracted snapshot actually exists. A checksum or unzip failure now reports a warning that the bundled snapshots are used, instead of a false success.
- **Non-interactive installs.** `install_flutter_complete.sh` honours `--yes`/`--non-interactive` (and a non-TTY stdin) and no longer blocks on its continuation prompt; piped and scripted installs run to completion.
- **Project config.** `flutter_project_config.sh` is now actually shipped in the deb (at `$PREFIX/share/flutter/flutter_project_config.sh`), so the per-project `compileSdk`/`targetSdk`/`abiFilters` configuration runs from the installed package instead of being missing.
- **Artifact location.** The deb is emitted under `release/` instead of the repository root.

Installing `3.47.8-5` over `3.47.8-4` is a drop-in replacement — follow the [Install](#install) steps.

## Package

| Item | Value |
|------|-------|
| Package | `flutter_3.47.8-6_aarch64.deb` |
| Size | 678,847,056 bytes (~647 MiB) |
| SHA256 | `9abd5b3048202475407804618605d49861aec73da414a1223db2f0cfc3f2fe90` |
| Flutter | 3.47.8 |
| Flutter Tools Dart | 3.13.7 |
| Dart VM | post-install `dartvm` resolves to Dart 3.13.7 (`android_arm64`) |
| Target host | Termux / Android bionic / ARM64 |

## Install

Recommended: the signed apt repository (verify the fingerprint against the
README before trusting the key):

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

The key and repository lines are one-time; upgrades are then plain
`apt upgrade flutter`.

Native-plugin projects (CMake / `externalNativeBuild`) additionally need the
NDK — it is opt-in, since pure-Dart builds do not use it:

```bash
bash $PREFIX/share/flutter/post_install.sh --provision-ndk
```

Fallback: download the payload from this release manually:

```bash
pkg update -y
pkg install -y x11-repo wget openjdk-21 7zip
wget https://github.com/GeneralKaos666/flutter-for-termux/releases/download/3.47.8/flutter_3.47.8-6_aarch64.deb
dpkg -i flutter_3.47.8-6_aarch64.deb
apt --fix-broken install -y
bash $PREFIX/share/flutter/post_install.sh
source $PREFIX/etc/profile.d/flutter.sh
flutter doctor -v
```

## Verified

Device smoke on Samsung SM-X716B / Android 16 / ARM64 Termux:

| Command | Result |
|---------|--------|
| `flutter --version` | ✅ Flutter 3.47.8 |
| `dart --version` | ✅ Dart 3.13.7 on `android_arm64` |
| `dartvm --version` | ✅ Dart 3.13.7 on `linux_arm64` |
| `flutter doctor -v` | ✅ completes; unknown channel / no connected device are expected warnings |
| `flutter create --platforms=android,linux` | ✅ |
| `flutter build apk --release --target-platform android-arm64 --no-tree-shake-icons` | ✅ ARM64 APK produced |
| `flutter build linux --release` | ✅ ARM64 Linux bundle produced |
| deb artifact validator | ✅ `dart`, `dartvm`, `dartaotruntime` executable |

## Highlights

### Flutter 3.47.8 update

- Updated package metadata, NDK configurations, and patches to target Flutter 3.47.8 (Dart 3.13.7).
- Keeps Flutter CLI on Termux JIT Dart while preserving engine VM tools for snapshots.

### Installer & Environment Hardening

- Fully guarded `$PREFIX` paths against whitespace and `set -u` unbound variable errors.
- Dynamic `JAVA_HOME` discovery across Termux OpenJDK installations.
- Automated dependency resolution including OpenJDK 21 and 7zip.

### Post-install Dart VM detection fix

- Fixed the `post_install.sh` system Dart VM replacement logic to directly inspect the target path (`/data/data/com.termux/files/usr/bin/dart`) rather than using `command -v`, preventing path shadowing issues.

### Technical Details

- Build output directories: `linux_debug_arm64/`, `linux_release_arm64/`, `linux_profile_arm64/` (the engine has no `android_*` output directory)
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
