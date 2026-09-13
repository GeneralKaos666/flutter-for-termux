# Modernize Termux Toolchain — Design

**Date:** 2026-09-13
**Status:** Approved for implementation

## Problem

The flutter-for-termux repo hardcodes toolchain versions across ~20 files and
pins packages that are stale against the live (Sept 2026) Termux ecosystem:

- **aapt2 / compileSdk:** README/docs claim aapt2 "2.19" and pin `compileSdk 34`.
  Current Termux main ships `aapt2 16.0.0.4-2`, built from AOSP android-16.0.0
  (API-36) sources. The historical API 35/36 resource-table incompatibility
  (AAPT2 analysis "Issue E") is believed fixed but never verified on-device.
- **On-device toolchain:** installer forces legacy `p7zip`, dual JDKs
  (openjdk-17+21), a `clang-18`-specific symlink hack, and a stale engine
  revision fallback `77e2e94772b6…` that no longer matches the configured engine.
- **Host deps:** `requirements.txt` pins aiohttp 3.11.14, fire 0.7.0, PyYAML
  6.0.2, GitPython 3.1.44, requests 2.32.3 — current are 3.14.3 / 0.7.1 / 6.0.3
  / 3.1.62 / 2.34.2. `pytest.ini` points at a nonexistent `tests/` dir.
- **CI/CD:** action versions straddle v4→v7; `build.yml` is a legacy path that
  relies on an unmaintained `newkdev/setup-depot-tools` action and an
  `ANDROID_NDK_LATEST_HOME` env that GH-hosted runners do not set; `build-deb.yml`
  still names the retired `android-ndk-r27d`.
- **Version-drift linters** (`check_repo.py`, `check_version_drift.py`) encode
  versions as hardcoded literals with per-version guards (e.g. a `3.12.0`
  branch), so bumps require editing the linters themselves.

## Goals

1. Lift `compileSdk`/`targetSdk` to 35/36 (36 preferred) verified on-device,
   with a fail-closed fallback ladder 36→35→34 and a `TERMUX_COMPILE_SDK` escape
   hatch.
2. Make `build.toml` the single source of truth for toolchain versions,
   propagated via `package.yaml` manifest fields into on-device scripts.
3. Modernize on-device packages: `7zip` (drop `p7zip`), single `openjdk-21`,
   version-agnostic clang handling, manifest-driven engine revision (no stale
   fallback).
4. Bump host Python deps and fix the pytest harness.
5. Unify CI action versions, delete the legacy `build.yml`, fix the r27d
   reference.
6. Decouple the drift linters from hardcoded values so future bumps touch
   config, not linter code.

## Non-goals

- ARM/x64 porting (ARM64-only remains; APK gen_snapshot only works on arm64).
- Switching on-device NDK distribution (lzhiyong r29 stays; r30 `30.0.16248370`
  is road-mapped but blocked upstream on lzhiyong/termux-ndk).
- R8/resource shrinking or APK-size work.

## Approach

**1. In-place staged sweep** (chosen over full rewrite or dual-track flags):
modernize each area in dependency order within existing files. Ordering:

1. Single source of truth (`build.toml` + manifest plumbing).
2. aapt2 / compileSdk experiment (highest risk; gates the SDK default).
3. On-device toolchain versions (`versions_common.sh`, 7zip, JDK, clang).
4. Host deps + pytest harness.
5. CI/CD unification.
6. Tests (bump-agnostic `test_build.py`) and docs.

## Configuration schema

`build.toml` additions:

```toml
[ndk]
api = 26
version = '29.0.14206865'

[android]
compile_sdk = 36   # winner of Stage-B ladder; override at runtime via TERMUX_COMPILE_SDK
target_sdk  = 36

[installer]
java     = 'openjdk-21'
zip_tool = '7zip'
```

`package.yaml` manifest resource gains template fields `$ndk_version`,
`$compile_sdk`, `$target_sdk`, resolved by `package.py` from `build.toml` the
same way `$tag` already is.

On-device uninstalled scripts (installers) source a single committed
`scripts/install/versions_common.sh`; `check_repo.py` verifies it agrees with
`build.toml`.

## Drift-linter decoupling

- `check_repo.py`: read all enforced values from `build.toml`; drop the
  literal version dict and the `'3.47.4'` fallback default; verify
  `versions_common.sh`, `install_flutter_complete.sh`, and on-device scripts
  against config values. Patch-state phrase pins become tag-agnostic markers
  (the `// Termux` grep markers the patches already carry).
- `check_version_drift.py`: remove the `3.12.0`-specific guards; generic scan
  over build.toml values.
- `autorelease.yml` sed list grows to cover the new `build.toml` fields.

## Stage-B experiment (gating)

On Samsung SM-X716B (or `device-smoke.yml`):

1. `flutter create` a fresh project.
2. `compileSdk=36`, `targetSdk=36`, `aapt2FromMavenOverride=$PREFIX/bin/aapt2`.
3. `flutter build apk --release --no-tree-shake-icons`.

Result ladder: 36 pass → default 36; 36 fail → retest 35 → default 35; else
keep 34. `build.toml [android]` picks the winner; in all outcomes the
`compile_sdk` FlutterExtension patch stays configurable.

Hardening: `apt-mark hold aapt2` in post_install (opt-out
`TERMUX_NO_HOLD_AAPT2=1`) to mitigate the dynamic-`libprotobuf` rolling
breakage (AAPT2 analysis "Issue D").

## Toolchain decisions (verified against the live repo, 2026-09-13)

| Item | Before | After |
|------|--------|-------|
| aapt2 | (doc "2.19") | 16.0.0.4-2 |
| compileSdk/targetSdk | 34 | 36 (ladder 36→35→34) |
| archive pkg | p7zip | 7zip 26.03 |
| JDK | openjdk-17 + 21 | openjdk-21 |
| clang wrapper | `clang-18` literal | dynamic (readlink detect) |
| engine fallback | `77e2e94772b6…` | manifest / `bin/internal/engine.version`, fail-closed |
| NDK | r29 (scattered) | r29 `29.0.14206865` in config (r30 road-mapped) |
| aiohttp / fire / PyYAML / GitPython / requests | 3.11.14 / 0.7.0 / 6.0.2 / 3.1.44 / 2.32.3 | 3.14.3 / 0.7.1 / 6.0.3 / 3.1.62 / 2.34.2 |
| pytest | unpinned | 9.1.1 |
| checkout / setup-python / upload-artifact | v4/v5 mix | v7.0.1 / v7.0.0 / v7.0.1 |
| workflows | incl. legacy `build.yml` | build-deb.yml sole path; build.yml deleted |

## Risks

- aapt2 16.0.0.4 could still fail API-36 linking → ladder downgrades the
  default; runtime override always available.
- Flutter's own default compileSdk moves each release → explicit config holds.
- lzhiyong r30 external dependency → stays r29, documented.
- autorelease sed list must grow → update in Task 8.

## Acceptance criteria

- `python scripts/ci/check_repo.py` and `check_version_drift.py` pass with the
  linters reading from `build.toml` (no literal version dicts).
- `pytest test_build.py` passes with expectations derived from `build.toml`.
- A fresh project builds an APK on-device at the winning compileSdk with the
  Termux aapt2 override (verified via device-smoke/manual gate).
- No remaining `p7zip` / `openjdk-17` / `clang-18` / `77e2e947` / bare
  `compileSdk = 34` references, except where callouts are intentional.
- All workflows parse and use uniform action versions; `build.yml` removed and
  documented in `docs/CI_CD.md`.