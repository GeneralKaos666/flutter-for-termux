# AGENTS.md

This file provides guidance to AI coding agents (Codex CLI, etc.) when working with code in this repository.

## What This Is

Cross-compile the Flutter SDK for Termux (Android/Bionic ARM64). Produces a `.deb` package installable on Termux that enables `flutter run`, `flutter build apk`, and `flutter build linux`.

## Build Commands

The build core is a Python Fire CLI (`build.py`). `python3 build.py <command>` dispatches to a `Build` method; `tag`, `config`, `clone`, `sync`, `patch`, `configure`, `build`, `sysroot`, `debuild`, and `output` are all member methods. Calling `python3 build.py` with no arguments runs the whole pipeline.

```bash
# Full build (usage on a self-hosted runner, ~2-4 hours on 24 threads)
NDK_PATH=/opt/android-ndk-rXX python3 build.py

# Individual steps
python3 build.py tag                                    # Print release tag from build.toml
python3 build.py clone                                  # Clone Flutter 3.47.2
python3 build.py sync                                   # gclient sync (~30GB)
python3 build.py patch --file=./patches/engine.patch --path=.
python3 build.py sysroot --arch=arm64                   # Assemble Termux sysroot from apt
python3 build.py configure --arch=arm64 --mode=debug    # GN configure
python3 build.py build --arch=arm64 --mode=debug        # ninja build
python3 build.py debuild --arch=arm64                   # Package .deb
```

Configuration lives in `build.toml` (`[flutter] tag`, `[ndk]`, `[build] arch/runtime`, `[patch.*]`, `[sysroot.*]`, `[package]`).

## Code Architecture

| File | Role |
|------|------|
| `build.py` | CLI entry point (Python Fire). `Build` class holds all commands. |
| `build.toml` | Config: Flutter tag, NDK API, arch/runtime, patch paths, sysroot packages |
| `sysroot.py` | `Sysroot` class downloads Termux `.deb` packages (async) and extracts into sysroot |
| `package.py` | `Package` class reads `package.yaml`, resolves variable substitution, creates `.deb` |
| `package.yaml` | Declarative artifact mapping: build output paths → Termux install paths |
| `utils.py` | Helpers: arch mapping (`arm64→aarch64`), output path resolution, Termux detection |
| `patches/` | Git patches for Engine, Dart VM, Skia (flat, tag-agnostic) |
| `.github/workflows/ci.yml` | GitHub-hosted PR/push sanity checks |
| `.github/workflows/build.yml` | Self-hosted full `.deb` build referenced by autorelease |
| `.github/workflows/device-smoke.yml` | Manual Windows+ADB Termux tablet smoke workflow |
| `.github/workflows/release-check.yml` | Release asset metadata verifier |
| `scripts/ci/check_repo.py` | Lightweight repo contract checker used by CI |
| `scripts/device/` | Windows ADB driver and Termux-side smoke script |
| `scripts/test/gh_e2e_test.sh` | GitHub Release clean-install E2E smoke script |
| `docs/CI_CD.md` | CI/CD, runner, and device-lab guide |

## Lightweight Verification

```bash
python -m py_compile build.py package.py sysroot.py utils.py scripts/ci/check_repo.py
bash -n scripts/install/post_install.sh scripts/test/gh_e2e_test.sh scripts/device/termux_smoke.sh
pytest test_build.py
python scripts/ci/check_version_drift.py
python scripts/ci/check_repo.py
git diff --check
```

Self-hosted workflows are manual-only. Do not run expensive full build or tablet smoke automatically on PRs.

## Critical Implementation Details

1. **Release tag = build.toml `[flutter] tag`** (e.g. `3.47.2`), no `v` prefix. Release asset is `flutter_<tag>_aarch64.deb`.
2. **`python3 build.py` with no args** runs `Build.__call__`: config → clone → sync → for each arch: sysroot, configure+build per mode, debuild.
3. **Only ARM64 APK gen_snapshot works**. 32-bit ARM fails (BoringSSL), x64 fails (sysroot mismatch).
4. **`package.yaml` uses `safe_eval()`** for variable resolution — constrained evaluation with recursion limits, be careful with template strings.
5. **Android NDK discovery**: `build.py` reads `[ndk] path` from build.toml or falls back to the `ANDROID_NDK`/`NDK_PATH` env var (workflows set `NDK_PATH` on the runner).
6. **GN flag `is_termux=true`** activates custom BUILD.gn rules that add `-llog -lm` for Android logging symbols.
7. **`utils.py __MODE__` is `('release', 'debug', 'profile')`** — release first. `Output.any` picks the first existing directory; this drives which dart-sdk snapshots are found.

## Termux Runtime: post_install.sh Auto-Fixes

`post_install.sh` automatically handles these ARM64 compatibility issues:
- **compileSdkVersion 36→34**: Termux aapt2 (v2.19) cannot load android-35/36 `android.jar`
- **NDK clang wrappers**: Replaces x86_64 clang/clang++ with Termux ARM64 native wrappers (dynamic clang lib version)
- **NDK llvm-objcopy**: Replaces x86_64 `llvm-objcopy`/`llvm-strip` with Termux ARM64 native binaries
- **Shebang fix**: All generated wrapper scripts use `#!/data/data/com.termux/files/usr/bin/sh`

## Termux Runtime: Per-Project Configuration

Each Flutter project needs in `android/gradle.properties`:
```properties
android.aapt2FromMavenOverride=/data/data/com.termux/files/usr/bin/aapt2
```

And in `android/app/build.gradle.kts`:
```kotlin
android {
    compileSdk = 34  // Must use API 34 (Termux aapt2 limitation)
    defaultConfig {
        targetSdk = 34
        ndk { abiFilters += listOf("arm64-v8a") }
    }
}
```

## Build Output

```
flutter/engine/src/out/
├── linux_debug_arm64/          # debug dart-sdk, gen_snapshot, libflutter_linux_gtk.so
├── linux_release_arm64/        # release dart-sdk, gen_snapshot, libflutter_linux_gtk.so
├── linux_profile_arm64/        # profile dart-sdk, gen_snapshot, libflutter_linux_gtk.so
└── (release output package)    # deb assembled from release artifacts + sysroot
```

## Environment

- Build: Linux (WSL2 Ubuntu on Windows or GitHub self-hosted runner), NDK r29 at `/opt/android-ndk-r29` (set `NDK_PATH` or `[ndk] path`)
- Path: `<workspace-root>/`
- Target: aarch64, Flutter 3.47.2
- Test device: `[REDACTED]` (Samsung SM-X716B / Android 16)
- Use PowerShell (not Git Bash) for `adb push` to avoid path mangling
