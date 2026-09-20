# AGENTS.md

Guidance for AI coding agents working in this repository.

## What This Is

Cross-compiles the Flutter SDK for Termux (Android/Bionic ARM64). Produces a `.deb` installable on Termux that enables `flutter run`, `flutter build apk`, and `flutter build linux`. Build host is Linux x86-64 (WSL2 Ubuntu locally; GitHub-hosted `ubuntu-latest` in CI with a self-hosted fallback); target is aarch64 only.

## Build CLI

`build.py` is a Python Fire CLI: `python3 build.py <command>` dispatches to a `Build` method (`config`, `clone`, `sync`, `patch`, `configure`, `build`, `sysroot`, `debuild`, `output`).

```bash
# Full pipeline (~2-4 h on 24 threads, needs NDK)
ANDROID_NDK=/opt/android-ndk-r29 python3 build.py

# Individual steps
python3 build.py tag                                  # prints release tag (Fire exposes the self.tag attribute)
python3 build.py clone
python3 build.py sync                                 # copies repo-root .gclient into flutter/ (its custom_hooks apply the patches), then gclient sync -DR
python3 build.py sysroot --arch=arm64                 # assemble Termux sysroot from apt
python3 build.py configure --arch=arm64 --mode=debug  # GN configure (is_termux=true)
python3 build.py build --arch=arm64 --mode=debug      # ninja
python3 build.py debuild --arch=arm64                 # produce .deb
```

**Patches are applied by the default pipeline.** `Build.__call__` (invoked with no args) is config → clone → sync → for each arch: sysroot, configure+build per mode, debuild. `sync` copies the repo-root `.gclient` and its `custom_hooks` apply `patches/engine.patch`, `patches/dart.patch`, and `patches/skia.patch` during `gclient sync -DR` — so a bare `python3 build.py` gets them automatically. The `patch_*` helpers are only for manually re-applying / rebasing a patch to a fresh checkout, and must **not** be run right after `sync` (the hooks already applied them; re-application fails with "already exists"):

```bash
python3 build.py patch_engine   # engine.patch @ repo root
python3 build.py patch_dart     # dart.patch @ engine/src/flutter/third_party/dart
python3 build.py patch_skia     # skia.patch @ engine/src/flutter/third_party/skia
```

or `python3 build.py patch --file=./patches/<name>.patch --path=<repo path>`. Skip if `flutter/` already exists at the right tag (clone auto-skips).

Key details:

- Modes come from `build.toml [build] runtime` — currently `['release']` only. To also build debug/profile you must rebuild those steps with `--mode=debug|profile`.
- `tag` is the release version (no `v` prefix). Release asset is `flutter_<tag>_aarch64.deb`.
- Prefix `NO_RECORD=1` to bypass the `@utils.record` debug-logging wrapper (it logs and re-raises; bypass reduces log noise, used by CI for `python3 build.py tag`).
- NDK discovery: build.py reads `[ndk] path` from build.toml, else the `ANDROID_NDK` env var. Workflows translate `NDK_PATH`/`ANDROID_NDK_HOME` → `ANDROID_NDK`.
- Host must have `dpkg` (sysroot.py runs `dpkg -x`) and `ar` (package.py runs `ar rc`).
- `pip install -r requirements.txt` first (fire, loguru, GitPython, PyYAML, aiohttp, requests, pytest).

## Architecture

| File | Role |
|------|------|
| `build.py` | CLI entry + orchestration. Host hardcoded `linux-x86_64`. |
| `build.toml` | Config: `[flutter] tag`, `[ndk] api/path`, `[build] arch/runtime`, `[patch.*]`, `[sysroot.*]`, `[package]` |
| `sysroot.py` | Downloads real Termux `.deb`s (async aiohttp), extracts them, symlinks `usr/` → `data/data/com.termux/files/usr`, stubs `libpthread.a` |
| `package.py` | `Package` reads `package.yaml`; resolves template vars, writes control/data tars, runs `ar` |
| `package.yaml` | Declarative artifact mapping: build output paths → Termux install paths |
| `utils.py` | Arch map (`arm64→aarch64`), output path resolution, `__MODE__`, Termux detection |
| `patches/` | Flat, tag-agnostic git patches (engine/dart/skia, plus `dart.new.patch` which is a symlink to `dart.patch` per `test_build.py`) |
| `scripts/` | Build helpers, `install/post_install.sh`, device smoke, CI checks, e2e test |
| `docs/CI_CD.md` | CI/CD, runner, and device-lab guide |

## Lightweight verification

Mirrors `ci.yml`. Run all of these before pushing:

```bash
python -m py_compile build.py package.py sysroot.py utils.py scripts/ci/check_repo.py scripts/ci/check_version_drift.py scripts/ci/verify_release_asset.py scripts/ci/generate_versions.py
pytest test_build.py        # NB: there is no tests/ dir; pytest.ini (testpaths=test_build.py) names the file
bash -n scripts/install/post_install.sh scripts/test/gh_e2e_test.sh scripts/device/termux_smoke.sh
python scripts/ci/generate_versions.py --check
python scripts/ci/check_version_drift.py   # add --fix to auto-rewrite drifted version refs from build.toml
python scripts/ci/check_repo.py
git diff --check
```

## CI/CD

`ci.yml` runs on PRs, pushes to `main`, and manual dispatch (compile + pytest + shellcheck + actionlint + build.toml schema + version-drift + repo-contract + whitespace). `validate.yml` is path-filtered (patches/build.py/build.toml/test_build.py/utils.py/stubs/requirements) and checks the `pytest` command contract plus that `engine.patch` applies to the configured tag via a shallow clone. Actual builds:

- `build.yml` — GitHub-hosted full `.deb` build (uses the NDK that ships on hosted runners via `ANDROID_NDK` env); auto-triggers on `CI` success on `main` (or manual dispatch) and publishes a release with the deb. This is the sole build path.
- `build-deb.yml` — self-hosted fallback full `.deb` build + artifact/evidence collection (feeds `device-smoke.yml`) for maintainers without hosted-runner time budget.
- `device-smoke.yml` — manual Windows+ADB: verifies candidate deb SHA256/commit binding, runs Termux smoke, optionally promotes the release.
- `autorelease.yml` — nightly: detects latest Flutter stable, bumps `build.toml`, `sed`s the same version strings across docs (incl. this file) and installers, then pushes.
- `release-check.yml` — on PRs and `release` events: verifies release asset metadata via `scripts/ci/verify_release_asset.py`.

## Gotchas

1. **Version drift is enforced.** `scripts/ci/check_version_drift.py` and `check_repo.py` scan AGENTS.md, guides, installers, and post_install.sh — every `3.47.5` / `flutter_3.47.5-1_aarch64.deb` / patch path must match `build.toml [flutter] tag` or CI fails. `autorelease.yml` rewrites these files automatically on a bump, so don't fight the sed format. Use `check_version_drift.py --fix` to auto-rewrite from `build.toml`.
2. **Only ARM64 works** for APK gen_snapshot. `arm` fails (32-bit BoringSSL shift overflow), `x64` fails (sysroot mismatch). Packaging is ARM64-only.
3. **`utils.__MODE__ = ('release', 'debug', 'profile')`** — release first. `Output.any` picks the first existing `flutter/engine/src/out/linux_*_*` dir; it drives which dart-sdk snapshots get packaged. `debuild` asserts at least one output dir exists — build before you package.
4. **`package.yaml` variables resolve with `safe_eval()`** (package.py) using constrained globals (`root`, `arch`, `output`, `version`, substitution defines). It allowlists literals, names, attribute access, and f-strings — keep template expressions constrained. `$version` is the engine revision from `bin/internal/engine.version`. Don't drop resource keys (`flutter`, `dart_sdk`, `artifacts`, `flutter_linux_gtk_*`, `flutter_patched_sdk*`, `executable`, `profile`, `stamps`, `manifest`) — `check_repo.py` asserts them.
5. **GN flag `is_termux=true`** activates repo-specific BUILD.gn rules (`-llog -lm`, termux toolchain). `configure()` also passes `custom_sysroot`, `-I` for NDK Vulkan headers + `stubs/` headers, and `-D__ANDROID_UNAVAILABLE_SYMBOLS_ARE_WEAK__` so SwiftShader can weak-import API-29 symbols at API 26.
6. **`build()` ninja targets are contract**: `flutter` + `flutter/build/archives:artifacts`, `:dart_sdk_archive`, `:flutter_patched_sdk`, `flutter/shell/platform/linux:flutter_gtk`, `flutter/tools/font_subset`. Dropping `flutter_gtk` breaks `flutter build linux`; `test_build.py` asserts this exact target list. `patches/dart.new.patch` is a symlink to `patches/dart.patch` — edit `dart.patch` only.
7. **`sysroot/` is disposable** (gitignored). Rebuild with `python3 build.py sysroot --arch=arm64`; `sysroot.lock.json` records the pinned package set and is required by `check_repo.py`.
8. **Repo hygiene is enforced.** New docs go under `docs/` (only `AGENTS.md`, `README.md`, etc. may live at root); every `.sh` needs a `#!` shebang with LF-only endings (same for the `build.py`/`package.py`/`sysroot.py`/ci-script entrypoints); never commit `scratch/`, `*.bak`, `*.receipt.json`, or test caches; keep `post_install.sh` marker comments intact (`PLATFORM_ABI_LIST`, Gradle-cache/NDK-download patches, Termux host→Linux-artifact mapping).

## Termux runtime

`post_install.sh` (run on-device after `dpkg -i`) auto-fixes:
- compileSdk default 36 (fail-closed ladder 36→35→34) — Termux aapt2 16.0.0.4 loads android-36 `android.jar`; projects are pinned via `flutter_project_config.sh`, overridable with `TERMUX_COMPILE_SDK`/`TERMUX_TARGET_SDK`
- NDK clang/clang++ wrappers → Termux ARM64 native wrappers (dynamic clang lib version)
- NDK `llvm-objcopy`/`llvm-strip` → Termux ARM64 native binaries
- All generated wrapper scripts → shebang `#!/data/data/com.termux/files/usr/bin/sh`

Per-project config for `flutter build apk` (see `scripts/install/flutter_project_config.sh`):

- `android/gradle.properties`: `android.aapt2FromMavenOverride=/data/data/com.termux/files/usr/bin/aapt2`
- `android/app/build.gradle.kts`: `compileSdk = 36`, `targetSdk = 36`, `ndk { abiFilters += listOf("arm64-v8a") }`

## Build output

```
flutter/engine/src/out/
├── linux_debug_arm64/    # dart-sdk, gen_snapshot, libflutter_linux_gtk.so, flutter_tester (per mode)
├── linux_release_arm64/  # release variants
└── linux_profile_arm64/  # profile variants
```

## Environment

- Host: Linux x86-64 (WSL2 Ubuntu locally, `ubuntu-latest` in CI), NDK r29, API 26
- Target: aarch64, Flutter 3.47.5 (`build.toml [flutter] tag`)
- `flutter/` and `sysroot/` are gitignored build trees — never commit them
- Use PowerShell (not Git Bash) for `adb push` to avoid path mangling
