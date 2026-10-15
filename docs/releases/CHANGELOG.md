# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]


### Upstream Flutter 3.47
- Stable release notes: https://docs.flutter.dev/release/release-notes/release-notes-3.47.0 (imported 2026-10-09; source verified).

### Infrastructure & CI
- Lifted `compileSdk`/`targetSdk` default from 34 to 36 (fail-closed ladder 36→35→34) using Termux aapt2 16.0.0.4 (Android Build-Tools), driven by `build.toml` `[android]` and applied per-project by `post_install.sh`/`flutter_project_config.sh`.
- Centralized installer version/package metadata in `scripts/install/versions_common.sh`, sourced by `lib_common.sh` and drift-verified against `build.toml`.
- Modernized packages: single JDK (`openjdk-21`), `7zip`, dynamic NDK clang detection, and an `apt-mark hold aapt2` hardening with a `TERMUX_NO_HOLD_AAPT2` opt-out.
- Bumped host deps (`requirements.txt`) and CI/CD actions to `actions/checkout@v7.0.1` / `setup-python@v7.0.0` / `upload-artifact@v7.0.1`, Python 3.12.
- `build.yml` runs a `gate` job first and skips the multi-hour engine build unless the `build.toml` tag has no published release yet (version bump); manual dispatches always build. The workflow was restored from the legacy path with an inline `depot_tools` bootstrap (no unmaintained setup action) with the engine/dart/skia patches applied; `build-deb.yml` remains as the self-hosted fallback.
- Release descriptions are taken from this changelog — the section matching the tag, else the current `Unreleased` history, else the last commit message (`scripts/ci/extract_release_notes.py`).
- Each `autorelease` bump records a pointer to the upstream Flutter stable notes for that series (`docs.flutter.dev`) under `Unreleased` (`scripts/ci/import_upstream_notes.py`), so this changelog follows upstream.
- After each published release, `build.yml` syncs the installer `EXPECTED_SHA256` defaults and doc size/hash tables to the published asset (`scripts/ci/sync_release_pins.py`), so pins stay current without manual refreshes.
- `test_build.py` version expectations are now fully derived from `build.toml` at test time (bump-agnostic).

### Changed
- NDK is configured from `build.toml [ndk] version` everywhere (docs, `build-deb.yml` fallback to `/opt/android-ndk-r{M}`); `pytest.ini` `testpaths` points at `test_build.py`.
- Retired the Mode-A "pin API 34" constraint documented in the AAPT2 analysis.
- Reoriented repository documentation to English-only: `README.md` is the single English README and the `docs/guides/*` documents are English canonicals (no `*_ZH.md` twins exist). Translated user-facing messages and comments in scripts to English. Removed stale `.orig` merge artifacts.
- The built deb now lands under `release/` instead of the repository root (`build.toml [package] path`, with `Build.output()` creating the directory on demand).

### Fixed
- **APK smoke test failed under AGP 9 and rolled back installs (issue #27).** The installer's smoke project was rewritten with an outdated `build.gradle.kts` DSL (`kotlin-android` plugin, `kotlinOptions { jvmTarget }`, `externalNativeBuild`). AGP 9.1.0 / Kotlin 2.4.0 reject those accessors, so the test build died with "Script compilation errors" and the installer rolled the whole installation back. The smoke project now uses the current Flutter 3.47.7 DSL (no `kotlin-android`, `kotlin { compilerOptions { jvmTarget = JvmTarget.JVM_17 } }`, `compileOptions` at Java 17).
- **Smoke failures are now diagnosable.** On an APK smoke failure the installer prints the tail of the relevant Gradle build log (filtered to error lines) and detects/report a failed `flutter create`, instead of the previous bare `✗ APK build failed`.
- **NDK default pinned to the shipped NDK.** `post_install.sh` pins the Flutter Gradle plugin default `ndkVersion` (and the create-time Dart constant in `gradle_utils.dart`) to the NDK shipped in the package (`29.0.14206865`), so new projects no longer make AGP auto-download the x86-64-only `android-ndk-r28c-linux` (whose tools cannot run on ARM64 Termux).
- **Partial NDK installs no longer break native plugin builds (issue #29).** A `flutter build apk`/`appbundle` could fail with `[CXX1101] NDK at ... did not have a source.properties file` when the installed NDK directory was incomplete. `post_install.sh` now validates each NDK directory and regenerates the missing `source.properties` metadata (or removes an unusable stub, so the installer re-provisions a clean NDK), and `install_flutter_complete.sh` no longer treats an incomplete NDK directory as installed.
- **An NDK missing its CMake toolchain file is no longer accepted as healthy (issue #29).** After the metadata repair above, a partially extracted NDK still broke native plugin builds (`:jni`) with `CMake Error ... Could not find toolchain file: .../build/cmake/android.toolchain.cmake`, because `source.properties` alone satisfied the installer's validity check and masked the missing files. `ndk_is_valid()` and `repair_ndk_metadata()` now also require `build/cmake/android.toolchain.cmake`, and `post_install.sh` removes such a directory so the installer re-provisions it instead of stamping it healthy.
- **Rendered `manifest.json` carried a trailing newline in `engine_revision`.** `utils.engine_version()` returned the engine revision with its trailing `\n` intact, so the rendered `manifest.json` `engine_revision` value was not the bare revision and did not match `post_install.sh`'s revision grep. The revision is now stripped of surrounding whitespace before rendering.
- **`~/.flutter_settings` could be rewritten into invalid JSON.** When the existing file's last key was removed, the rewrite could leave a trailing comma before appending `"android-sdk"`, producing a doubled comma and a file that no longer parses. The rewrite now loads the JSON, sets the key, and re-serializes it (falling back to `cat` when `python3` is unavailable).
- **Snapshot installs reported false success in both installers.** `install_flutter_complete.sh` and the deb-shipped `post_install.sh` printed `✓ Dart SDK snapshots installed` even when the checksum or unzip failed. Both now print the banner only when the extracted snapshot exists and otherwise warn that the bundled snapshots are used.
- **Non-interactive installs blocked on the continuation prompt.** `install_flutter_complete.sh` now honours `--yes`/`--non-interactive` and a non-TTY stdin, skipping the prompt so piped and scripted installs run to completion.
- **`flutter_project_config.sh` was not shipped in the deb.** `package.yaml` now declares the `flutter_project_config` resource, installing the script at `$PREFIX/share/flutter/flutter_project_config.sh` instead of leaving the on-device per-project configuration missing.
- **Documented engine output directories were incorrect.** Earlier notes claimed the build produced `android_release_arm64/` and `android_profile_arm64/` engine output directories; the engine actually builds the three `linux_{debug,release,profile}_arm64/` directories under `flutter/engine/src/out/`. The build guides now match the build.

## [3.44.9-termux] - 2026-07-04

### Added
- Upgraded Flutter SDK cross-compilation target to `3.44.9`.
- Added support for disabling resource optimizations and shrinking during APK builds in smoke tests due to compatibility issues with Termux's native `aapt2`.

### Fixed
- Fixed Dart detection in `post_install.sh` to check for `$SYSTEM_DART` directly, avoiding path shadowing by the SDK's local CLI wrapper.
- Fixed a resource packaging failure (missing `AndroidManifest.xml`) during Termux release APK builds by updating the `termux_smoke.sh` configuration and documentation.


### Added
- GitHub-hosted `CI` workflow for lightweight PR/push checks: Python compile, shell syntax, PowerShell parse, workflow/package/docs sanity, and whitespace guard.
- Manual self-hosted workflows for full `.deb` builds, Windows+ADB tablet smoke tests, and release asset metadata verification.
- `docs/CI_CD.md` documenting workflow boundaries, runner requirements, security model, and local equivalents.
- `scripts/ci/check_repo.py` to keep release/docs/package contracts from silently regressing.
- `scripts/device/run_termux_smoke.ps1` and `scripts/device/termux_smoke.sh` for repeatable Termux tablet smoke testing.

### Changed
- Moved the GitHub Release E2E script from repository root to `scripts/test/gh_e2e_test.sh`.
- Removed the stale `.agent/workflows/build-termux-flutter.md` workflow in favor of maintained GitHub Actions and `docs/CI_CD.md`.
- Updated README/README_ZH directory maps and CI/CD guidance to match the organized `scripts/{ci,device,test}` layout.
- Ignored local OMX/OMC runtime state and normalized line-ending rules for workflows, Python, Markdown, shell, and PowerShell files.

## [3.44.0-termux] - 2026-06-01

### Added
- Flutter 3.44.0 / Dart 3.12 package refresh for Termux ARM64.
- Packaged `dartvm` alongside `dart` and `dartaotruntime` so Dart 3.10+ VM/tool split works on Termux.
- Release artifact metadata in docs: size `666,366,556` bytes and SHA256 `b8af08d26ee4ae4b3dcf1aab4ee6b05965529587ddf1bc9b936b48b5f01f9846`.

### Fixed
- Flutter Tools Android-host crashes by mapping Termux/Android host lookups to Linux ARM64 artifacts.
- Flutter 3.44 Gradle plugin failure by adding `PLATFORM_ABI_LIST` to the ARM64-only `FlutterPluginConstants.kt` post-install template.
- Stale Gradle included-build cache after post-install source rewrites.
- NDK clang runtime path handling for the 3.44 Android gen_snapshot build.

### Verified
- `python3 build.py debuild --arch=arm64` with artifact validator for `dart`, `dartvm`, and `dartaotruntime`.
- On-device smoke on Samsung SM-X716B / Android 16 / Termux: `flutter --version`, `dart --version`, `dartvm --version`, `flutter doctor -v`, `flutter create`, `flutter build apk --release --target-platform android-arm64 --no-tree-shake-icons`, and `flutter build linux --release`.

### Known Limitations
- APK target remains ARM64-only.
- Per-project Android settings still need API 34 + `android.aapt2FromMavenOverride`.
- `flutter run` requires ADB pairing/connection inside Termux.

---

## [3.41.5-termux-2] - 2026-04-13

### Fixed
- **`flutter build linux` on device**: `post_install.sh` sed command used `|` delimiter which collided with `||` in Dart source code, silently failing. Changed to `@` delimiter
- **`build.py` sync duplicating directories**: `cp -r` caused nested `scripts/scripts/` dirs. Fixed to use `cp -a {src}/. {dst}/`
- **flutter_tools snapshot not rebuilt**: Added `rm -f flutter_tools.stamp` and `rm -f flutter_tools.snapshot` after patching `build_linux.dart` to force Dart VM to pick up changes

### Added
- `docs/guides/UPGRADE_GUIDE.md` — complete step-by-step guide for upgrading to new Flutter versions
- `.gitignore` patterns for temp scripts (`fix_*.sh`, `test_*.sh`, etc.)
- E2E test script `gh_e2e_test.sh` for automated clean-install verification from GitHub Release

### Verified (E2E from GitHub Release)
- Download .deb → dpkg install → post_install.sh → flutter create → build apk → build linux: **ALL PASS**

---

## [3.41.5-termux] - 2026-03-25

### Added
- Flutter version upgrade: 3.35.0 → 3.41.5 (Dart 3.11.3)
- **Linux release/profile engine builds**: `build_all()` now compiles all three Linux modes (debug, release, profile)
- Full build matrix verified on device (6/6): Linux debug/release/profile + APK debug/release/profile

### Fixed
- **`flutter build linux` (release/profile) failure**: `build_all()` was only building debug mode engine for Linux, leaving `linux_release_arm64/` and `linux_profile_arm64/` empty in the deb package
- **`utils.py __MODE__` ordering**: the tuple is `('release', 'debug', 'profile')` (release first). `Output.any` picks the first existing engine output directory, so when a release directory exists its (product-mode) dart-sdk snapshots drive the Flutter CLI. Reordering modes arbitrarily breaks packaging. (This conflicts with an earlier changelog note claiming a debug-first order; current source of truth is `utils.py`, which is release-first.)

### Technical Details
- Build output now includes 5 directories: `linux_debug_arm64/`, `linux_release_arm64/`, `linux_profile_arm64/`, `android_release_arm64/`, `android_profile_arm64/`
- `build_all()` expanded from 8 to 12 steps to include Linux release/profile configure+build
- Deb package size increased from ~541MB to ~662MB due to additional engine artifacts

---

## [3.35.0-termux] - 2026-01-07

### Added
- Full Flutter 3.35.0 support for Termux ARM64
- `flutter build apk` support (ARM64 only, debug/profile/release modes)
- `flutter build linux` support
- `flutter run` with Hot Reload support (all modes: debug/profile/release)
- Linux desktop app support via Termux X11
- One-command installation script (`install_flutter_complete.sh`)
- Automatic NDK clang wrapper configuration
- Android SDK integration with API 34
- VM snapshots for Linux profile mode

### Fixed
- libc++_shared.so symlink issue (was pointing to linker script instead of ELF)
- NDK clang wrapper detection for multiple NDK versions
- Dependency conflicts during package upgrades (openjdk-17 vs openjdk-21)
- Missing Android build tools (d8, dx, aidl, apksigner)
- libatomic.a stubs for CMake compatibility
- **Linux profile mode crash** (`_NetworkProfiling` type not found in dart.io)
  - Root cause: `PropagateIfError()` crashes when no Dart stack frames
  - Fix: Made network profiling initialization optional in `dart_runtime_hooks.cc`
- **Gradle auto-downloading new NDK versions** causing build failures
  - Fix: Auto-set `ANDROID_NDK_HOME` in profile script to use pre-configured NDK

### Known Limitations
- APK builds only support ARM64 (android-arm64-v8a)
- ARMv7 (android-arm) not supported due to BoringSSL 32-bit issues
- x86/x64 Android targets not supported

### Technical Details
- Cross-compiled from x86_64 Linux (WSL) to ARM64 Bionic
- Uses NDK r27d with API level 35 (historical for this 3.35.0 release; current build baseline is NDK r29, API 26)
- Patches applied to Flutter Engine, Dart VM, and Skia
- TLS alignment fixed for Bionic linker compatibility

---

## Version Naming Convention

`{flutter_version}-termux` - e.g., `3.35.0-termux`

- `flutter_version`: Upstream Flutter SDK version
- `termux`: Indicates this is a Termux-compatible build

## Upgrading

See [UPGRADE_GUIDE.md](../guides/UPGRADE_GUIDE.md) for upgrade instructions.
