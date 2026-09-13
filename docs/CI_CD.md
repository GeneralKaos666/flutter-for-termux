# CI/CD and Device Lab

This repository uses GitHub Actions for both lightweight validation and the full Flutter Engine build:

1. **GitHub-hosted workflows** for fast, free, public-repository validation **and** the full `.deb` build on `ubuntu-latest`.
2. **Self-hosted workflows** as fallbacks for the expensive build and for Android tablet smoke tests.

The goal is to keep pull requests cheap and safe while still making release builds reproducible.

## GitHub Actions cost and limits

This is a public open-source repository, so standard GitHub-hosted runner
minutes are free for the lightweight `CI`/`Release check` workflows **and** the
full `Build` workflow. The self-hosted build/device fallbacks also do not
consume GitHub-hosted runner minutes.

That does **not** mean Actions are unlimited:

- GitHub still enforces workflow, queue, API, concurrency, cache, and artifact
  limits. For example, GitHub-hosted jobs have a 6-hour execution limit, and
  self-hosted jobs have a 5-day execution limit.
- The full `Build` workflow is a multi-hour, tens-of-GB job; on the 4-vCPU / 14
  GB standard runner it can approach or exceed the 6-hour cap. If it times
  out, switch `runs-on` to a larger (paid) runner, or use the self-hosted
  `Build deb (self-hosted)` fallback.
- Artifacts and caches should be kept small and short-lived. Large `.deb`
  release payloads belong in GitHub Releases, not as long-retained workflow
  artifacts.
- Self-hosted runner capacity is limited by the maintainer's own WSL/Windows
  machine, disk space, tablet availability, and network.

References:

- [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
- [GitHub Actions limits](https://docs.github.com/en/actions/reference/limits)

## Workflow map

| Workflow | File | Runner | Trigger | Purpose |
|----------|------|--------|---------|---------|
| CI | `.github/workflows/ci.yml` | `ubuntu-latest` | PR, push to `main`, manual | Python/shell/PowerShell syntax, package/docs/workflow sanity, whitespace checks |
| Build | `.github/workflows/build.yml` | `ubuntu-latest` | `workflow_dispatch`, or `CI` success on `main` | Full `build.py` pipeline on GitHub-hosted runner, `.deb` packaging, auto-publish release |
| Build deb (self-hosted) | `.github/workflows/build-deb.yml` | self-hosted Linux/WSL | manual | Full `build.py` pipeline, `.deb` packaging, optional release publishing (fallback) |
| Device smoke | `.github/workflows/device-smoke.yml` | self-hosted Windows + ADB tablet | manual | Install deb in Termux, run `post_install.sh`, `flutter doctor`, create/build APK/Linux smoke |
| Release check | `.github/workflows/release-check.yml` | `ubuntu-latest` | release publish/edit, manual | Verify release asset name, size, and SHA256 digest |

The legacy `build.yml` GitHub-hosted build path was restored in favor of a
modernized version: the old one depended on
`newkdev/setup-depot-tools@v1.0.1` (unmaintained) — now replaced with an
inline `depot_tools` clone — and used NDK detection from GitHub-hosted runner
env (`ANDROID_NDK` / `ANDROID_NDK_LATEST_HOME` / `ANDROID_NDK_HOME`), which
the ubuntu images do set. **Build (GitHub-hosted)** is thus the primary build
path, and Build deb (self-hosted) remains as a fallback.

## Why the split exists

Public repositories can use standard GitHub-hosted runners for free, so the
full build runs there by default. Still, this project's full build is not a
normal CI job:

- `gclient sync` downloads tens of GB.
- Flutter Engine builds can take hours (multi-hour on the 4-vCPU runner).
- The build needs an Android NDK (hosted images ship one; the env vars
  `ANDROID_NDK` / `ANDROID_NDK_LATEST_HOME` / `ANDROID_NDK_HOME` point at it).
  `build.toml [ndk] version` (r29) is used for packaging metadata; self-hosted
  installs pin it at `/opt/android-ndk-r29`.
- Real release confidence requires an attached Android/Termux tablet.

Therefore:

- **PR CI must stay lightweight** and never touch self-hosted device hardware.
- **The full build is the `ubuntu-latest` `Build` workflow**, auto-triggered on
  `CI` success on `main` or manual dispatch.
- **Device smoke is a manual self-hosted gate** run by a maintainer.

## PR / push CI

`ci.yml` runs on every PR and push to `main`:

```text
python -m py_compile build.py package.py sysroot.py utils.py scripts/ci/check_repo.py scripts/ci/check_version_drift.py scripts/ci/verify_release_asset.py
bash -n install_flutter_complete.sh scripts/install/*.sh scripts/test/gh_e2e_test.sh scripts/device/termux_smoke.sh
PowerShell parser check for scripts/device/run_termux_smoke.ps1
python scripts/ci/check_repo.py
git diff --check
```

`check_repo.py` validates repo-specific contracts, including:

- workflow YAML parses
- self-hosted workflows are not triggered by `pull_request`
- `package.yaml` still packages `dart`, `dartvm`, `dartaotruntime`, and `post_install`
- `post_install.sh` still contains the Flutter 3.44 `PLATFORM_ABI_LIST` and Android-host patches
- installer defaults remain on Flutter 3.47.4 and NDK r29 for Termux installs
- release/download docs do not regress to stale 3.41.5 commands

## Full deb build

Primary workflow: **Build** (`.github/workflows/build.yml`).

Runs on `ubuntu-latest` when `CI` succeeds on `main` (or on manual dispatch),
reusing the NDK that ships on GitHub-hosted runners. It:

1. Installs host deps and bootstraps `depot_tools` (inline clone).
2. Detects the NDK from the runner env (`ANDROID_NDK` → `ANDROID_NDK_LATEST_HOME` → `ANDROID_NDK_HOME`).
3. Runs the documented pipeline with the termux patches applied in order:

   ```bash
   python3 build.py clone
   python3 build.py sync
   python3 build.py patch_engine
   python3 build.py patch_dart
   python3 build.py patch_skia
   python3 build.py sysroot --arch=arm64
   python3 build.py configure --arch=arm64 --mode=debug
   python3 build.py build    --arch=arm64 --mode=debug
   python3 build.py debuild  --arch=arm64
   ```

   Patches are applied **after** `sync` (a bare `python3 build.py` would
   re-run `gclient sync -DR` and wipe them), matching the sequence above.
4. Publishes a GitHub Release tagged with the Flutter version containing
   `**/*.deb`.

If the hosted runner hits its 6-hour cap (or you want build metadata), fall
back to **Build deb (self-hosted)** (`.github/workflows/build-deb.yml`):

- Runs on `${{ inputs.runner_labels_json }}` (default `["self-hosted","linux"]`).
- Requires `/opt/android-ndk-r29` (or `ANDROID_NDK`/`NDK_PATH` env) and a Linux/WSL runner with 100GB+ disk.
- Uploads the deb plus `sha256`/`size.txt`, `build_metadata.json`, `build_evidence.json`, and `inventory.txt` as a workflow artifact (feeds `device-smoke.yml`).

That self-hosted workflow bootstraps `depot_tools` if `gclient` is missing,
then runs the same patched pipeline (see above) before uploading:

- `flutter_3.47.4_aarch64.deb`
- `flutter_3.47.4_aarch64.deb.sha256`
- `flutter_3.47.4_aarch64.deb.size.txt`

## Release policy

Merging to `main` **does** publish a GitHub Release through the auto-triggered
`Build` workflow: after `CI` passes, a multi-hour engine build runs on
`ubuntu-latest` and the resulting `.deb` is published under the Flutter
version tag. This is the intended automation for this public repo.

The release flow:

1. Merge only after PR CI passes.
2. **Build** auto-runs on `CI` success (or trigger it manually via
   `workflow_dispatch` on the chosen commit/tag).
3. Run device smoke against the produced or published `.deb`.
4. Let **Release check** verify the release asset metadata after publish/edit.

If you need a dry build (no auto publish) or richer build metadata, use the
self-hosted **Build deb (self-hosted)** workflow instead.

## Device smoke

Manual workflow: **Device smoke (self-hosted)**

Default input tests the published 3.47.4 release asset:

```text
deb_url: https://github.com/GeneralKaos666/flutter-for-termux/releases/download/3.47.4/flutter_3.47.4_aarch64.deb
expected_sha256: 6994580359002c6e0f6eb074d17a8ab3f9578e480e2aad83aa443474da3c9800
```

Required self-hosted environment:

- Windows runner with ADB installed
- Android tablet connected and authorized for USB debugging
- Termux installed and launchable as `com.termux`
- Tablet awake/unlocked before the run; secure lock screens block ADB text injection into Termux
- Enough tablet storage for the deb, Android SDK/NDK, Gradle caches, APK, and Linux build

The PowerShell driver intentionally keeps the tablet awake:

```powershell
adb shell svc power stayon true
adb shell input keyevent 224
adb shell wm dismiss-keyguard
```

It then pushes the deb and `scripts/device/termux_smoke.sh`, launches Termux, injects:

```text
sh /sdcard/Download/termux_ci_smoke.sh
```

and polls `/sdcard/Download/termux_ci_smoke.txt` until `DONE` or timeout.

Required success markers:

```text
INSTALL_STATUS=0
POST_INSTALL_STATUS=0
FLUTTER_VERSION_STATUS=0
DART_VERSION_STATUS=0
DARTVM_VERSION_STATUS=0
DOCTOR_STATUS=0
CREATE_STATUS=0
BUILD_APK_STATUS=0
APK_MANIFEST_STATUS=0
APK_RESOURCES_STATUS=0
APK_COPY_STATUS=0
BUILD_LINUX_STATUS=0
DONE
```

The workflow turns `svc power stayon` back off before exiting.

## Release check

`release-check.yml` verifies release metadata from GitHub:

- expected tag exists
- expected asset exists
- asset size is plausible
- asset digest matches the expected SHA256 when GitHub exposes the digest

This workflow is safe to run on GitHub-hosted runners because it only reads public release metadata.

## Security model

- Fork PRs only get `ci.yml` on GitHub-hosted runners; `Build`/`Release check`
  are `workflow_run`-guarded or `workflow_dispatch`-only.
- Self-hosted `Build deb (self-hosted)`/`Device smoke` workflows are
  `workflow_dispatch` only.
- Device smoke does not run untrusted PR code automatically.
- Release publishing requires `contents: write`: the GitHub-hosted `Build`
  workflow publishes automatically after `CI` succeeds on `main`.

## Branch Protection and Repository Governance

The repository governance rules for the `main` branch are codified in `.github/rulesets/main_protection_ruleset.json`:

- **Pull Request Requirements**: Mandatory PR review and thread resolution before merge.
- **Status Checks**: Strict status checks require `ci.yml` (Python/Shell/Actionlint sanity, contract validation, version drift checks) to pass cleanly before merging.
- **History & Integrity**: Linear git history is enforced; force pushes (`non_fast_forward`) and branch deletion are blocked.
- **Repository Hygiene**: Automated pre-merge checks prevent scratch artifacts, test caches, backups, and stage receipts from leaking into git tracking.

## Local equivalents

Fast local checks:

```bash
python -m py_compile build.py package.py sysroot.py utils.py scripts/ci/check_repo.py scripts/ci/check_version_drift.py scripts/ci/verify_release_asset.py
bash -n install_flutter_complete.sh scripts/install/*.sh scripts/test/gh_e2e_test.sh scripts/device/termux_smoke.sh
python scripts/ci/check_repo.py
python scripts/ci/check_version_drift.py
git diff --check
```

Manual Termux release E2E test inside Termux:

```bash
bash scripts/test/gh_e2e_test.sh
```

Manual Windows-to-tablet smoke:

```powershell
scripts/device/run_termux_smoke.ps1 `
  -AdbPath "C:\Users\aa223\AppData\Local\Android\Sdk\platform-tools\adb.exe" `
  -DebUrl "https://github.com/GeneralKaos666/flutter-for-termux/releases/download/3.47.4/flutter_3.47.4_aarch64.deb"
```
