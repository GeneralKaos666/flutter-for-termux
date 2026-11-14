# Maintenance Guide

Move the package to a new Flutter release, understand CI and the device lab,
and read the AAPT2 release-build analysis. Replaces `docs/CI_CD.md`,
`docs/guides/UPGRADE_GUIDE.md` and
`docs/guides/AAPT2_RELEASE_BUILD_BUG_ANALYSIS.md`.

## Upgrading Flutter

This document explains how to upgrade Termux Flutter from the current 3.50.0
to a new version, and lists the risk points in Dart / Flutter Tools / Gradle
plugins that must be re-checked after 3.50.0.

Build commands and their expected behavior are explained in the
[Build guide](BUILD.md); build internals (patches, GN configs,
sysroot) are documented in the [Build process](BUILD.md).

---

### Upgrading via apt (end users)

If Flutter was installed from the apt repository
([README quick start](../README.md#quick-start)), upgrading is:

```bash
pkg update && apt upgrade flutter
```

Each release re-downloads the full payload (~640 MB) and re-verifies its
checksum and size before anything on disk is touched. There is no apt progress
bar while the package configures, so watch the script's own output.

If the configure step fails, `dpkg` has already recorded the new version while
the previous payload is restored on disk — the two converge when the idempotent
`postinst` runs again:

```bash
apt --fix-broken install
```

`flutter upgrade`, `flutter downgrade` and `flutter channel <name>` are blocked
inside the shipped `flutter_tools` build (`patches/engine.patch`, exit status 1);
they would replace the patched, cross-built tree with upstream's checkout and
break the SDK.

The source-rebuild checklist below is maintainer-facing — it produces a new
package from a new upstream Flutter tag, and is not the end-user upgrade path.

---

### Upgrade Checklist

Each checklist item maps to one step below:

```text
□ Step 1:  Update the version in build.toml
□ Step 2:  Clone the new Flutter version
□ Step 3:  Run gclient sync (patches apply automatically)
□ Step 4:  Rebase the patches if they no longer apply
□ Step 5:  Confirm the patches are applied
□ Step 6:  Assemble the sysroot
□ Step 7:  Configure + build (per runtime mode)
□ Step 8:  Package the .deb
□ Step 9:  Push to the device and test
□ Step 10: Update post_install.sh (if necessary)
□ Step 11: Publish the GitHub Release
□ Step 12: Publish the apt repository
```

### Must-Check Items After 3.50.0

| Item | Why it matters | How to check |
|------|------------|----------|
| `dart` / `dartvm` / `dartaotruntime` | Dart 3.10+ splits the CLI and VM runtime more clearly; packaging only `dart` will break either the snapshots or the Flutter CLI | `python3 build.py debuild --arch=arm64` must pass the artifact validator |
| Android-target `gen_snapshot` | APK AOT needs a `target_os=android` build; the Linux desktop one white-screens the app (issue #41) | Release APK installs and opens; `linux-arm64-android-release/gen_snapshot` exists in the deb |
| Flutter Tools host platform | Termux is treated by Dart as an `android` host; official Flutter Tools normally only handles macOS/Linux/Windows | Run `flutter doctor -v` on Termux; it must not crash during the cache/artifact/device-discovery phase |
| Flutter Gradle plugin constants | As of Flutter 3.44, `FlutterPlugin.kt` directly imports `PLATFORM_ABI_LIST` | `flutter build apk --release --target-platform android-arm64 --no-tree-shake-icons` must not show Kotlin unresolved-reference errors |
| Gradle included-build cache | post-install modifies the Kotlin source; on upgrade the old cache may mix old and new sources | `post_install.sh` must clear `packages/flutter_tools/gradle/.gradle`, `build`, `bin` |
| Android SDK / aapt2 | Newer templates may raise `compileSdk`; Termux aapt2 (16.0.0.4) loads android-36 `android.jar` | Default new projects to `compileSdk = 36`, `targetSdk = 36`, `android.aapt2FromMavenOverride`; post-install falls back 36 → 35 → 34 when aapt2 cannot load the newest platform |

---

### Step 1: Update the Version Number

Edit `build.toml`:

```toml
[flutter]
tag = '3.XX.Y'    # ← change to the new version number

[package]
pkg_rel = '1'     # reset to 1 for a new upstream tag, increment for a respin
```

> Other fields usually don't need to change (NDK path, jobs, etc.).
> If the new Flutter version requires a newer NDK, update the `[ndk]` section
> as well.

---

### Step 2: Clone the New Flutter Version

```bash
# Run in WSL
cd ~/termux-flutter
python3 build.py clone
```

This `git clone`s the Flutter repo at the specified tag into `./flutter/`.

---

### Step 3: Run gclient Sync

```bash
python3 build.py sync
```

This will:
1. Copy `.gclient` to the engine directory
2. Run `gclient sync -DR --no-history` (downloads ~30GB engine + deps)
3. Automatically replace the prebuilt dart-sdk with the matching version

> ⏱ Takes about 30-60 minutes (depending on network speed)

---

### Step 4: Rebase the Patches

Patches live flat in `patches/` (engine.patch, dart.patch, skia.patch) and are tag-agnostic.

#### Patch Sources and Purpose

| Patch file | Target | Purpose |
|------------|------|------|
| `engine.patch` | Engine BUILD.gn | Adds the `-llog -lm` link flags and the `is_termux` GN variable |
| `dart.patch` | Dart VM | Fixes the bionic TLS alignment issue |
| `skia.patch` | Skia graphics library | Fixes ARM64 bionic compilation issues |

#### How to Rebase the Patches

**Method A: Try to apply the existing patches**

The `.gclient` `custom_hooks` apply the patches automatically during
`python3 build.py sync`. If you instead want to test them against a fresh
checkout, run them manually (reset first if a prior `sync` already applied
them):

```bash
python3 build.py patch --file=./patches/engine.patch
python3 build.py patch --file=./patches/dart.patch --path=engine/src/flutter/third_party/dart
python3 build.py patch --file=./patches/skia.patch --path=engine/src/flutter/third_party/skia
```

If the patch fails to apply (offset/conflict), you need to rebase it manually:

**Method B: Rebase the patch manually**

```bash
cd flutter/engine/src/flutter
# See which files the original patch touches
git apply --stat ~/termux-flutter/patches/engine.patch

# Try applying it to see where it conflicts
git apply --check patches/engine.patch

# Manually fix conflicting files, then regenerate the patch
git diff > ~/termux-flutter/patches/engine.patch
```

#### Key Modification Points for Each Patch

<details>
<summary><b>engine.patch — what must be changed</b></summary>

1. **`build/config/termux/BUILD.gn`** — add the Termux runtime library flags:
   ```gn
   ldflags = ["-stdlib=libstdc++", "-Wl,--warn-shared-textrel", "-llog", "-lm"]
   ```

2. **`build/toolchain/custom/BUILD.gn`** — defines the `is_termux` flag

3. **`flutter/BUILD.gn`** — add `-llog -lm` under the Termux condition

4. **`shell/platform/linux/`** — GTK embedding fixes for bionic

</details>

<details>
<summary><b>dart.patch — TLS fix</b></summary>

Fixes the Thread-Local Storage alignment issue in the Dart VM. The Android bionic linker requires the TLS segment to be correctly aligned.

</details>

<details>
<summary><b>skia.patch — compilation fix</b></summary>

Fixes Skia compilation errors on the ARM64 bionic environment.

</details>

<details>
<summary><b>ARM64-only APK — install-time CLI modification</b></summary>

`post_install.sh` modifies `build_apk.dart` / `build_aar.dart` / `build_appbundle.dart` and the Flutter Gradle plugin
so that the default `targetPlatform` is `[arm64]` only (disables arm/x64 gen_snapshot on Termux).

> ⚠️ This is applied to the installed Flutter SDK on the device, not at build time.
> Each new version's build_apk.dart may have a different structure.

</details>

---

### Step 5: Confirm the Patches Are Applied

Patches are applied automatically by the `.gclient` `custom_hooks` during
`python3 build.py sync` — no manual step is needed after a successful sync.
The command below is only for re-testing a patch against a fresh checkout
(reset the tree first if the hooks already applied it):

```bash
python3 build.py patch --file=./patches/engine.patch
python3 build.py patch --file=./patches/dart.patch --path=engine/src/flutter/third_party/dart
python3 build.py patch --file=./patches/skia.patch --path=engine/src/flutter/third_party/skia
```

> ARM64-only APK enforcement is handled at install time by `post_install.sh`
> (via the `build_apk`, `build_aar`, `build_appbundle`, and `plugin_utils` patches),
> not as a build-time patch.

---

### Step 6: Assemble the Sysroot

```bash
python3 build.py sysroot --arch=arm64
```

Downloads `.deb` packages from the Termux apt repo and extracts them into the sysroot directory.

> Usually the sysroot package list in `build.toml` does not need to be modified,
> unless the new Flutter version introduces new system dependencies (for example, adding GTK4).

---

### Step 7: Configure + Build (per Runtime Mode)

The default pipeline runs only the modes in `build.toml [build] runtime` (currently `['release']`). Configure + build additional modes explicitly only if you are producing those engine variants (needed for the corresponding dart-sdk snapshots):

```bash
# Release (main mode — includes dart-sdk, gen_snapshot, linux engine, etc.)
python3 build.py configure --arch=arm64 --mode=release
python3 build.py build --arch=arm64 --mode=release

# Debug (for flutter run -d linux debug mode; only if produced)
python3 build.py configure --arch=arm64 --mode=debug
python3 build.py build --arch=arm64 --mode=debug

# Profile (Linux profile engine)
python3 build.py configure --arch=arm64 --mode=profile
python3 build.py build --arch=arm64 --mode=profile
```

> ⏱ Each mode takes about 30-60 minutes (24 threads)
>
> ⚠️ **Important**: the `__MODE__` tuple in `utils.py` is `('release', 'debug', 'profile')`,
> with release in the first position. `Output.any` picks the first existing directory,
> so the release (product mode) dart-sdk snapshots drive the Flutter CLI. Do not reorder modes arbitrarily.

Then build the Android-target `gen_snapshot` for each mode in
`build.toml [build] android_runtime` (release and profile by default). This is
a separate GN configuration and only builds the `gen_snapshot` target:

```bash
python3 build.py configure_android --arch=arm64 --mode=release
python3 build.py build_android --arch=arm64 --mode=release

python3 build.py configure_android --arch=arm64 --mode=profile
python3 build.py build_android --arch=arm64 --mode=profile
```

**No extra tool steps are needed:**

> ✔️ `dart_sdk_archive`, `flutter_patched_sdk`, and `flutter_gtk` are all
> included in the `build()` ninja invocation — the Dart binary is produced by
> the standard build via `flutter/build/archives:dart_sdk_archive` (no
> separate `build_dart` step in the FFT pipeline).
>
> ✔️ The Android `gen_snapshot` is a separate build by design. The Linux
> desktop `gen_snapshot` emits `target_os=linux` snapshots without compressed
> pointers, which the Android VM rejects, so `flutter build apk` needs the
> `--android` build. Only ARM64 → ARM64 works; ARM32 and x64 targets cannot be
> compiled (see the [Build guide](BUILD.md#8-known-limitations)).

---

### Step 8: Package the .deb

```bash
python3 build.py debuild --arch=arm64
```

This will:
1. Collect every build output declared in `package.yaml` (SDK, engine
   artifacts, both Android `gen_snapshot` builds, scripts)
2. Render `manifest.json` and the package metadata from `build.toml`
3. Package everything into the deb

> Output path: `release/flutter_<tag>-<pkg_rel>_aarch64.deb`
> (for example `release/flutter_3.50.0-5_aarch64.deb`).

---

### Step 9: Device Testing

```powershell
# Copy from WSL to Windows
Copy-Item "\\wsl.localhost\Ubuntu\home\YOUR_USER\termux-flutter\release\flutter_3.XX.Y-1_aarch64.deb" .

# Push to the device
adb push flutter_3.XX.Y-1_aarch64.deb /data/local/tmp/

# Install in Termux
dpkg -i /data/local/tmp/flutter_3.XX.Y-1_aarch64.deb
apt-get install -f
bash $PREFIX/share/flutter/post_install.sh
source $PREFIX/etc/profile.d/flutter.sh

# Verify
flutter doctor -v
flutter create testapp && cd testapp
bash $PREFIX/share/flutter/flutter_project_config.sh
flutter build apk --release --target-platform android-arm64 --no-tree-shake-icons
flutter build linux --release
```

> **Note:** the Android build needs `--no-tree-shake-icons` because Termux Dart
> runs in JIT mode and cannot execute Flutter's AOT icon tree-shaker snapshot.

---

### Step 10: Update post_install.sh (If Necessary)

`scripts/install/post_install.sh` contains many `sed` modifications against the Flutter source code.
If the new Flutter version changes any of these locations, they need to be updated:

| Modification target | Purpose | When it may need updating |
|---------|------|-------------------|
| `build_apk.dart` | Disables arm/x64 platforms | Flutter refactored the APK build logic |
| `build_linux.dart` | Bypasses the `isLinux` check | Flutter changed how the platform is checked |
| `FlutterPluginConstants.kt` | ARM64-only NDK filter | The Gradle plugin structure changed |
| `artifacts.dart` | Resolves the Android AOT `gen_snapshot` to the package-shipped binary | Flutter changed `_getAndroidArtifactPath` or the host-platform lookup |
| `compileSdk` / `targetSdk` defaults | Termux aapt2 platform support (36 → 35 → 34 ladder) | aapt2 gains or loses platform support |
| `tool_backend.sh` | Shebang fix | Flutter regenerated this script |
| NDK clang driver | Termux ARM64 clang | A new NDK version |

**Testing**: run `post_install.sh` on the device and watch the output for `⚠` warnings or errors.

---

### Step 11: Publish the GitHub Release

```powershell
# Update version
$VER = "3.XX.Y"
$REL = "1"

# Create the release (tag is the Flutter version, no "v" prefix)
gh release create "$VER" `
  --title "Flutter $VER for Termux ARM64" `
  --notes-file docs/releases/RELEASE_NOTES.md `
  "release/flutter_${VER}-${REL}_aarch64.deb"
```

Remember to update:
- `docs/releases/CHANGELOG.md`
- `docs/releases/RELEASE_NOTES.md`
- `scripts/install/versions_common.sh` (version, pkg rel, SHA256)
- `README.md`

> CI enforces version consistency via `scripts/ci/check_version_drift.py` —
> if a reference was missed, the `Repository sanity` job fails and names the
> file. `scripts/ci/generate_versions.py --check` separately asserts that
> `versions_common.sh` matches `build.toml`.

---

### Step 12: Publish the flutter Stub to termux-apt-repo

Once the release carries the payload `.deb`, regenerate the `Architecture:
all` stub and publish it through the unified
[termux-apt-repo](https://github.com/GeneralKaos666/termux-apt-repo):

```bash
python3 scripts/release/apt_repo.py stub
```

Then drop the resulting `apt-repo-work/flutter_<ver>_all.deb` into the
termux-apt-repo `debs/` directory and republish that repository (its own
`README.md` documents the `./termux-apt-repo debs docs stable main --sign`
flow).

- The stub fetches the payload from the GitHub release, verifies its SHA256
  and size, and unpacks it on device, so the metadata can never advertise a
  package that does not exist.
- Verify on a device: `apt update && apt policy flutter` shows the new
  version.

---

### One-Command Upgrade (If the Patches Apply Cleanly)

If the patches can be applied directly, the whole process can be run with a single command:

```bash
python3 build.py
```

`python3 build.py` (Build.__call__) runs the following in order:
1. config
2. clone
3. sync
4. per arch: sysroot, then configure + build for each mode in `build.toml [build] runtime` (currently `['release']`), including `dart_sdk_archive`
5. per arch: configure + build the Android-target `gen_snapshot` for each mode in `build.toml [build] android_runtime` (release and profile)
6. debuild

> ⏱ The whole thing takes about 2-4 hours

---

### Common Upgrade Problems

Each entry shows the exact error (**Symptom**), why it happens (**Cause**),
and the fix (**Fix**).

#### 1. Patch conflicts

- **Symptom**: `git apply` fails
- **Cause**: upstream changed files covered by the patch
- **Fix**: modify the source manually and regenerate the patch (see Step 4)

#### 2. New system dependencies

- **Symptom**: a header or library is missing at compile time
- **Cause**: the new Flutter version needs a system package the sysroot does not ship yet
- **Fix**: add the new package to `[sysroot.termux-main]` or `[sysroot.termux-x11]` in `build.toml`

#### 3. GN flag changes

- **Symptom**: `gn gen` errors with an unknown variable
- **Cause**: Flutter refactored BUILD.gn
- **Fix**: check the `build/config/` structure of the new engine and adjust `engine.patch`

#### 4. Dart SDK version mismatch

- **Symptom**: `package_config.json` language version error
- **Cause**: the prebuilt dart-sdk replacement no longer matches the new engine
- **Fix**: `build.py sync` handles this automatically, but if the version number changed, check the download URL

#### 5. post_install.sh sed failure

- **Symptom**: `sed` cannot find the target string
- **Cause**: the new Flutter version changed the Dart source code that was being modified
- **Fix**: manually inspect the target files on the device and update the `sed` search patterns

---

### Patch Maintenance Tips

1. **Keep patches minimal**: only modify what is necessary
2. **Add comments**: explain in the patch why the modification is needed
3. **Version isolation**: each Flutter version has its own patch directory
4. **Record changes**: update docs/releases/CHANGELOG.md

---

### Project Structure Reference

See the merged tree in
[Build process — File Structure Reference](BUILD.md#file-structure-reference).

## CI, CD and the device lab

This repository uses GitHub Actions for both lightweight validation and the full Flutter Engine build:

1. **GitHub-hosted workflows** for fast, free, public-repository validation **and** the full `.deb` build on `ubuntu-latest`.
2. **Self-hosted workflows** as fallbacks for the expensive build and for Android tablet smoke tests.

The goal is to keep pull requests cheap and safe while still making release builds reproducible.

In this document:

1. [GitHub Actions cost and limits](#github-actions-cost-and-limits)
2. [Workflow map](#workflow-map)
3. [PR / push CI](#pr--push-ci)
4. [Full deb build](#full-deb-build)
5. [Release policy](#release-policy)
6. [Device smoke](#device-smoke)
7. [Release check](#release-check)
8. [Security model](#security-model)
9. [Branch protection and repository governance](#branch-protection-and-repository-governance)
10. [Local equivalents](#local-equivalents)

### GitHub Actions cost and limits

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

### Workflow map

| Workflow | File | Runner | Trigger | Purpose |
|----------|------|--------|---------|---------|
| CI | `.github/workflows/ci.yml` | `ubuntu-latest` | PR, push to `main`, manual | Python/shell/PowerShell syntax, pytest, ruff, shellcheck, actionlint, version drift, package/docs/workflow sanity, whitespace checks |
| Validate | `.github/workflows/validate.yml` | `ubuntu-latest` | PR/push touching patches, `build.py`, `build.toml`, `test_build.py`, `utils.py`, `stubs/`, `requirements.txt` | Runs the Python unit tests and checks that `engine.patch` applies to the configured Flutter tag |
| Build | `.github/workflows/build.yml` | `ubuntu-latest` | `workflow_dispatch`, or `CI` success on `main` + `gate` (builds only when the `build.toml` tag has no published release yet) | Full `build.py` pipeline on GitHub-hosted runner, `.deb` packaging, auto-publish release |
| Build deb (self-hosted) | `.github/workflows/build-deb.yml` | self-hosted Linux/WSL | manual | Full `build.py` pipeline, `.deb` packaging, optional release publishing (fallback) |
| Device smoke | `.github/workflows/device-smoke.yml` | self-hosted Windows + ADB tablet | manual | Install deb in Termux, run `post_install.sh`, `flutter doctor`, create/build APK/Linux smoke |
| Release check | `.github/workflows/release-check.yml` | `ubuntu-latest` | PR, release publish/edit, manual | Verify release asset name, size, and SHA256 digest (lightweight structural check on PRs) |

The legacy `build.yml` GitHub-hosted build path was restored in favor of a
modernized version: the old one depended on
`newkdev/setup-depot-tools@v1.0.1` (unmaintained) — now replaced with an
inline `depot_tools` clone — and used NDK detection from GitHub-hosted runner
env (`ANDROID_NDK` / `ANDROID_NDK_LATEST_HOME` / `ANDROID_NDK_HOME`), which
the ubuntu images do set. **Build (GitHub-hosted)** is thus the primary build
path, and Build deb (self-hosted) remains as a fallback.

### Why the split exists

Public repositories can use standard GitHub-hosted runners for free, so the
full build runs there by default. Still, this project's full build is not a
normal CI job:

- `gclient sync` downloads tens of GB.
- Flutter Engine builds can take hours (multi-hour on the 4-vCPU runner).
- The build needs an Android NDK (hosted images ship one; the env vars
  `ANDROID_NDK` / `ANDROID_NDK_LATEST_HOME` / `ANDROID_NDK_HOME` point at it).
  `build.toml [ndk] version` (r30) is used for packaging metadata; self-hosted
  installs pin it at `/opt/android-ndk-r30`.
- Real release confidence requires an attached Android/Termux tablet.

Therefore:

- **PR CI must stay lightweight** and never touch self-hosted device hardware.
- **The full build is the `ubuntu-latest` `Build` workflow**, triggered on
  `CI` success on `main` or manual dispatch — but a `gate` job skips the
  multi-hour build unless the `build.toml` tag has no published release
  yet (i.e. the version was bumped).
- **Device smoke is a manual self-hosted gate** run by a maintainer.

### PR / push CI

`ci.yml` runs on every PR and push to `main`. In order:

```text
python -m py_compile <every tracked .py file>
pytest test_build.py test_apt_repo.py
ruff check build.py package.py sysroot.py utils.py test_build.py scripts/ci/ scripts/release/ test_apt_repo.py
bash -n scripts/install/*.sh scripts/test/gh_e2e_test.sh scripts/device/termux_smoke.sh
shellcheck --severity=error scripts/release/stub/* scripts/install/*.sh ...
actionlint .github/workflows/*.yml
PowerShell parser check for scripts/device/run_termux_smoke.ps1
python scripts/ci/check_version_drift.py
python scripts/ci/generate_versions.py --check
python scripts/ci/check_repo.py
git diff --check
```

`ruff format --check` also runs, but is non-blocking.

`check_repo.py` validates repo-specific contracts, including:

- workflow YAML parses, and self-hosted workflows are not triggered by `pull_request`
- `package.yaml` still ships the required runtime resources, including `post_install`
- `post_install.sh` still contains the Flutter 3.44 `PLATFORM_ABI_LIST`, Android-host, and Android `gen_snapshot` patches
- installer defaults remain on Flutter 3.50.0 and NDK r30 for Termux installs
- release/download docs do not regress to stale 3.41.5 commands

### Full deb build

Primary workflow: **Build** (`.github/workflows/build.yml`).

Runs on `ubuntu-latest` when `CI` succeeds on `main` (or on manual dispatch),
reusing the NDK that ships on GitHub-hosted runners. A `gate` job runs
first and skips the build when the `build.toml` tag already has a
published release with the expected `.deb` — so only version bumps (and
manual dispatches) trigger the multi-hour pipeline. It:

1. Installs host deps and bootstraps `depot_tools` (inline clone).
2. Detects the NDK from the runner env (`ANDROID_NDK` → `ANDROID_NDK_LATEST_HOME` → `ANDROID_NDK_HOME`).
3. Runs the full pipeline, which is equivalent to the step-by-step commands
   in the [Build guide](BUILD.md#3-step-by-step-build):

   ```bash
   python3 build.py
   ```

   That covers config, clone, sync, sysroot, the Linux release build, the
   Android-target `gen_snapshot` build for release and profile, and `debuild`.
   Patches are applied automatically by the `.gclient` `custom_hooks` during
   `sync`, so no explicit `patch_*` step is needed. The `patch_*` helpers are
   only for manually re-applying / rebasing a patch to a fresh checkout;
   running them right after `sync` fails with "already exists".
4. Publishes a GitHub Release tagged with the Flutter version containing
   `**/*.deb`.

If the hosted runner hits its 6-hour cap (or you want build metadata), fall
back to **Build deb (self-hosted)** (`.github/workflows/build-deb.yml`):

- Runs on `${{ inputs.runner_labels_json }}` (default `["self-hosted","linux"]`).
- Requires `/opt/android-ndk-r30` (or `ANDROID_NDK`/`NDK_PATH` env) and a Linux/WSL runner with 100GB+ disk.
- Uploads the deb plus `sha256`/`size.txt`, `build_metadata.json`, `build_evidence.json`, and `inventory.txt` as a workflow artifact (feeds `device-smoke.yml`).

That self-hosted workflow bootstraps `depot_tools` if `gclient` is missing,
then runs the same patched pipeline (see above) before uploading:

- `flutter_3.50.0-5_aarch64.deb`
- `flutter_3.50.0-5_aarch64.deb.sha256`
- `flutter_3.50.0-5_aarch64.deb.size.txt`
- `inventory.txt`
- `build_metadata.json`
- `build_evidence.json`

### Release policy

Merging to `main` publishes a GitHub Release through the `Build` workflow
**only when the version was bumped**: after `CI` passes, the `gate` job
checks whether the `build.toml` tag already has a published release with
the expected `.deb`. If it does, the build is skipped; otherwise a
multi-hour engine build runs on `ubuntu-latest` and the resulting `.deb`
is published under the Flutter version tag. This is the intended
automation for this public repo.

The release flow:

1. Merge only after PR CI passes.
2. **Build** runs on `CI` success, but its `gate` job skips everything
   unless the `build.toml` tag is unreleased — i.e. only version bumps
   (or a manual `workflow_dispatch` on the chosen commit/tag) trigger a
   real build.
3. The release description is taken from `docs/releases/CHANGELOG.md`:
   the section matching the tag (or whatever history is current under
   `Unreleased`), falling back to the last commit message. Each
   `autorelease` bump also records a pointer to the upstream Flutter
   stable notes for that series under `Unreleased`, so this changelog
   follows upstream.
4. After publishing, **Build** syncs the installer pins (`EXPECTED_SHA256`
   defaults, doc size/hash tables) to the published asset via
   `scripts/ci/sync_release_pins.py` and pushes a `chore(release)` commit,
   so pins stay current without manual refreshes. That commit re-triggers
   CI, but the `gate` skips the rebuild.
5. Run device smoke against the produced or published `.deb`.
6. Let **Release check** verify the release asset metadata after publish/edit.

If you need a dry build (no auto publish) or richer build metadata, use the
self-hosted **Build deb (self-hosted)** workflow instead.

### Device smoke

Manual workflow: **Device smoke (self-hosted)**

The workflow tests a `build-deb` candidate artifact: it downloads the artifact by run id and name, then verifies the deb against the pinned SHA256 before running the smoke. The three required inputs are:

```text
artifact_run_id: <build-deb workflow run id>
artifact_name: <uploaded artifact name>
expected_sha256: 8a5ee969c1c671360054bf26b992b93b9770372ae6673dfdc6155bd4191dc981
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
APT_REPAIR_STATUS=0
INSTALL_STATUS=0
POST_INSTALL_STATUS=0
FLUTTER_VERSION_STATUS=0
DART_VERSION_STATUS=0
DARTVM_VERSION_STATUS=0
DOCTOR_STATUS=0
CREATE_STATUS=0
CONFIG_VERIFY_STATUS=0
BUILD_APK_STATUS=0
APK_MANIFEST_STATUS=0
APK_RESOURCES_STATUS=0
APK_COPY_STATUS=0
BUILD_LINUX_STATUS=0
BUILD_AAB_STATUS=0
AAB_COPY_STATUS=0
DONE
```

The workflow turns `svc power stayon` back off before exiting.

### Release check

`release-check.yml` runs on `pull_request`, `workflow_dispatch`, and `release`
(published/edited) events, and verifies release metadata:

- expected tag exists
- expected asset exists
- asset size is plausible
- asset digest matches the expected SHA256 when GitHub exposes the digest

The PR path is lightweight: on `pull_request` the workflow sets
`LIGHTWEIGHT_CHECK=1`, so `verify_release_asset.py` confirms the manifest
resolves and the expected SHA256 is well-formed (validating a local asset if
one is present) and skips the GitHub API lookup. The
`release`/`workflow_dispatch` path performs the full API verification.

This workflow is safe to run on GitHub-hosted runners because it only reads public release metadata (`permissions: contents: read, actions: read`).

### Security model

- Fork PRs get the GitHub-hosted `ci.yml` and `release-check.yml`, both
  read-only (no publish credentials); `Build` is `workflow_run`-guarded or
  `workflow_dispatch`-only.
- Self-hosted `Build deb (self-hosted)`/`Device smoke` workflows are
  `workflow_dispatch` only.
- Device smoke does not run untrusted PR code automatically.
- Release publishing requires `contents: write`: the GitHub-hosted `Build`
  workflow publishes automatically after `CI` succeeds on `main`.
- Flutter is published through the unified
  [termux-apt-repo](https://github.com/GeneralKaos666/termux-apt-repo), a
  separate repository that signs its `InRelease` with its own key. Its
  fingerprint is `1DED 0840 4574 B867 B058 A7BB 476F C230 ECBB FDF0`; users pin
  it against the published `key.gpg` (see the README quick start). Rotation is
  handled in that repository — regenerate the key, update its secrets, and
  re-record the fingerprint there and in the README.
- Commits pushed by the repository's own workflows are signed with a
  dedicated CI key (`4C99 EB10 CEAF 9A70 86AF 1B11 7565 5171 87EB EB47`),
  separate from the maintainer's personal key. Its public half is registered
  on the maintainer's GitHub account and its armored private half lives in the
  `GPG_PRIVATE_KEY` repository secret. Rotation: generate a new key, upload the
  public half, replace the secret, and revoke the old key.

### Branch Protection and Repository Governance

The repository governance rules for the `main` branch are codified in `.github/rulesets/main_protection_ruleset.json`:

- **Pull Request Requirements**: A PR is required and every review thread must be resolved before merge; approvals are **not** required (`required_approving_review_count: 0`, `required_review_thread_resolution: true`).
- **Status Checks**: Strict status checks (`strict_required_status_checks_policy: true`) require the `Repository sanity` job (`ci.yml`) and the `Verify release metadata` job (`release-check.yml`) to pass before merging.
- **History & Integrity**: Linear git history is enforced; force pushes (`non_fast_forward`) and branch deletion are blocked.
- **Commit Signatures**: `required_signatures` rejects any unsigned commit pushed to `main`. Human commits are signed with the maintainer's local GPG key; the autorelease and pin-sync commits are signed in CI with the dedicated CI signing key stored as the `GPG_PRIVATE_KEY` secret. Both workflows keep the bot as the commit author and set the signing identity (`GeneralKaos666 <xkiller1177@gmail.com>`) as the committer, because GitHub verifies against the committer identity. Repository admins bypass the ruleset so the automated PAT pushes still land; anything signed with an unregistered key is marked `Unverified` by GitHub instead of being rejected locally.
- **Repository Hygiene**: Automated pre-merge checks prevent scratch artifacts, test caches, backups, and stage receipts from leaking into git tracking.
- **Local Enforcement**: Checked-in hooks under `.githooks/` mirror the signature rule on the developer machine. Enable them once per clone with `git config core.hooksPath .githooks`; `pre-commit` refuses a commit that git would not sign, and `post-commit` re-verifies `HEAD` and prints `git commit --amend -S --no-edit` when the signature is missing or invalid. `check_repo.py` validates the hook files and warns (outside CI) when `core.hooksPath` is not set.

### Local equivalents

Run the same local checks as CI, in this order. (CI also runs `shellcheck`,
`actionlint`, and a PowerShell parser check; those live in `ci.yml`.)

1. Compile-check the Python entry points, tests, and CI/release scripts:

   ```bash
   python -m py_compile build.py package.py sysroot.py utils.py test_build.py test_apt_repo.py scripts/ci/*.py scripts/release/*.py
   ```

2. Run the Python unit tests (mirrors `ci.yml`):

   ```bash
   pytest test_build.py test_apt_repo.py
   ```

3. Lint the Python sources (`ruff format` is non-blocking in CI):

   ```bash
   ruff check build.py package.py sysroot.py utils.py test_build.py test_apt_repo.py scripts/ci/ scripts/release/
   ```

4. Syntax-check the shell scripts:

   ```bash
   bash -n scripts/install/*.sh scripts/test/gh_e2e_test.sh scripts/device/termux_smoke.sh
   ```

5. Run the repository contract check — expected output: `Repository sanity check passed.`

   ```bash
   python scripts/ci/check_repo.py
   ```

6. Run the version drift check — expected output: `Version drift check PASSED (all files aligned with build.toml).`

   ```bash
   python scripts/ci/check_version_drift.py
   ```

7. Confirm `scripts/install/versions_common.sh` still matches `build.toml`:

   ```bash
   python scripts/ci/generate_versions.py --check
   ```

8. Check for whitespace errors:

   ```bash
   git diff --check
   ```

9. Regenerate the flutter stub package locally (it is then dropped into
   termux-apt-repo and republished there):

   ```bash
   python3 scripts/release/apt_repo.py stub   # → apt-repo-work/flutter_<ver>_all.deb
   ```

Manual Termux release E2E test inside Termux:

```bash
bash scripts/test/gh_e2e_test.sh
```

Manual Windows-to-tablet smoke:

```powershell
scripts/device/run_termux_smoke.ps1 `
  -AdbPath "C:\Users\aa223\AppData\Local\Android\Sdk\platform-tools\adb.exe" `
  -DebPath ".\candidate\flutter_3.50.0-5_aarch64.deb" `
  -ExpectedSha256 "8a5ee969c1c671360054bf26b992b93b9770372ae6673dfdc6155bd4191dc981"
```

`-DebPath` is how `device-smoke.yml` invokes the script (via `$env:RESOLVED_DEB`,
the artifact it just downloaded and hash-verified).

## AAPT2 release-build analysis

Release APKs built inside Termux used to install and then fail to parse. This
document records what caused that, which workarounds the project ships, and
what a full fix would require.

> **Status (2026-09-13):** Termux now ships aapt2 16.0.0.4 from Android
> Build-Tools, which loads the android-36 `android.jar`. New projects therefore
> default to `compileSdk = 36` / `targetSdk = 36` through `post_install.sh`,
> with a fail-closed ladder 36 → 35 → 34 for devices whose aapt2 cannot load
> the newest platform. The old "pin API 34" constraint is retired as the
> default. The R8 shrinking / resource-optimization workaround described below
> is still in effect for on-device build stability.

---

### 1. Background and Symptom

Building a release APK natively inside Termux with the cross-compiled Flutter
SDK 3.44.2 completed successfully (exit code `0`) and produced an APK of about
18 MB. Installing it with `adb install` failed immediately:

```text
Failure [INSTALL_PARSE_FAILED_UNEXPECTED_EXCEPTION: Failed to parse /data/local/tmp/app-release.apk:
AndroidResourceParser: Failed to read manifest]
```

Decompressing the broken APK showed the damage:

- `AndroidManifest.xml` was completely missing.
- The `resources.arsc` table was completely missing.
- `res/` was empty or lacked every referenced drawable and layout.

Debug builds skip shrinking and resource optimization, and they installed
normally.

---

### 2. Root Cause Analysis

The bug appears only for release builds. AGP runs R8 resource shrinking
(`isShrinkResources = true`) and resource optimization
(`android.enableResourceOptimizations = true`) in that path, rebuilding the
resource package (`.ap_`).

In Termux, AGP's bundled `aapt2` is overridden with the Termux package at
`/data/data/com.termux/files/usr/bin/aapt2`. Five separate problems follow from
that substitution.

#### A. AGP resolves aapt2 by OS, not by CPU architecture

Google publishes no ARM64 host build of `aapt2` on Google Maven
([Google issue 227219818](https://issuetracker.google.com/issues/227219818)).
AGP also maps host platforms by OS alone (`SdkConstants.currentPlatform()`),
ignoring the CPU. On any Linux host it therefore requests
`com.android.tools.build:aapt2:<version>:linux`, which resolves to an x86_64
glibc ELF binary. That binary cannot run on ARM64 Bionic inside Termux.

#### B. The replacement toolchain breaks modern R8 packaging

In AGP 8.0+, R8 unifies code and resource shrinking ("optimized resource
shrinking"). The missing manifest and corrupt resource tables are not caused
by static analysis pruning active resources. They come from a low-level
toolchain packaging bug: the overridden, mismatched native `aapt2` crashes, or
fails to handle command-line arguments and ZIP stream formats, during the
re-zipping and linking phases.

Because this is a packaging crash rather than a pruning decision, custom keep
rules (`keep.xml`, ProGuard rules) have no effect.

#### C. `split-select` is missing

During resource optimization AGP calls `split-select` to generate
configuration split APKs. Google publishes no ARM64 build of `split-select`
and Termux does not package it. The step fails silently or corrupts the
intermediate `.ap_` archive, which `aapt2` then cannot parse.

#### D. Rolling-release protobuf instability

The Termux `aapt2` links dynamically against `libprotobuf.so`. Termux is a
rolling release, so `pkg upgrade` frequently bumps `libprotobuf.so.XX`. When
that happens, `aapt2` dies on a dynamic-linker error and the resource
compile/link step exits quietly, leaving truncated or empty assets.

#### E. Android 15/16 resource table format changes

Android 15 (API 35) and 16 (API 36) changed the binary layout of
`resources.arsc`. Termux `aapt2` builds based on older AOSP Build-Tools
sources reject the new structure (`RES_TABLE_TYPE_TYPE entry offsets overlap
actual entry data`) and strip the resources they cannot parse.

---

### 3. Deployment Modes and Solution Strategies

The project supports two tracks.

#### Mode A: Verified local APK build (default)

- **Target:** native on-device builds, side-loading, and quick debug/test
  compiles.
- **SDK constraints:** default `compileSdk = 36`, `targetSdk = 36`.
  `post_install.sh` falls back 36 → 35 → 34 when the installed aapt2 cannot
  load the newest platform.
- **Optimization:** R8 code/resource shrinking and resource optimization are
  disabled, which avoids the packaging crashes and keeps memory use inside
  what a phone JVM heap can handle.
- **Status:** verified and stable on Android 16 (Samsung SM-X716B). The API-34
  pin is retired — aapt2 16.0.0.4 loads the android-36 `android.jar`, and only
  the shrinking-disabled workaround remains.

#### Mode B: Experimental publish toolchain (future / CI target)

- **Target:** Google Play compliance, App Bundle (`.aab`) output, and full
  code/resource optimization.
- **SDK constraints:** API 35+ (Play Console has required an Android 15 target
  SDK for uploads since August 2025).
- **Optimization:** full R8 minification and resource optimization enabled.
- **Toolchain:** replaces the dynamic system `aapt2` with an
  architecture-matched, statically linked ARM64 Android Build-Tools
  environment, for example from an upstream project such as
  [`lzhiyong/termux-ndk`](https://github.com/lzhiyong/termux-ndk).
- **Status:** experimental. It must pass automated matrix checks before it
  becomes a default.

---

### 4. Comparison of Solutions

| Strategy | How it works | Pros | Cons and risks |
|:---|:---|:---|:---|
| **1. Disable optimizations (Mode A workaround)** | Set `shrink=false` and `android.enableResourceOptimizations=false`, and turn shrinking off in project `buildTypes`. | Stable and reliable; avoids dynamic library crashes; lowers JVM heap use on device. | Larger release APKs (a few MB of dead code); no Play-style resource optimization, so it is not a store-ready configuration. |
| **2. Stub `split-select` and swap** | Drop a dummy `split-select` script that exits `0` into `build-tools/` so validation passes. | Gets simple verification tasks past the check. | Fragile placeholder only. R8 resource shrinking still links corrupt layouts without real asset-split mappings. |
| **3. Bundle static ARM64 Build-Tools (Mode B target)** | Ship statically compiled ARM64 Build-Tools (v35+) and override paths through global Gradle properties. | No dynamic dependencies, so protobuf bumps cannot break it; meets Play API 35/36 requirements; enables full R8 shrinking. | High maintenance cost: every upstream package update has to be tracked and verified. |

#### Emulation and container workarounds (Box64 / PRoot)

- **Box64 / QEMU user-mode:** ruled out. Android mounts
  `/proc/sys/fs/binfmt_misc` read-only, so user space cannot register the
  x86_64 emulator handler the JVM would need without root.
- **PRoot distro (Ubuntu container):** ruled out as a general workflow. It
  stabilizes dynamic library dependencies but does not close the CPU
  architecture gap (aapt2 still needs a manual override), adds a 2×–5×
  compile slowdown from `ptrace` syscall interception, and costs 5 GB+ of
  storage.

---

### 5. Current Developer Workaround (Mode A)

For stable on-device builds, apply the project settings below. Both the Gradle
properties and the `buildTypes` overrides are required until matrix smoke
tests show the properties alone are enough.

#### 1. `android/gradle.properties`

```properties
# Force Gradle to use the native Termux AAPT2
android.aapt2FromMavenOverride=/data/data/com.termux/files/usr/bin/aapt2

# Turn off R8 resource optimizations to avoid AAPT2 repackaging failures
android.enableResourceOptimizations=false

# Disable R8 shrinking in the Flutter Gradle Plugin
shrink=false

# Keep the JVM heap small enough to avoid OOM crashes in Termux
org.gradle.jvmargs=-Xmx2048m -XX:MaxMetaspaceSize=512m -Dfile.encoding=UTF-8
```

#### 2. `android/app/build.gradle.kts`

```kotlin
android {
    compileSdk = 36 // default; post-install falls back to 35/34 as needed

    defaultConfig {
        targetSdk = 36
        ndk {
            abiFilters += listOf("arm64-v8a") // ARM64 only
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            isShrinkResources = false
        }
    }
}
```

#### 3. Release build command

The Termux Dart VM runs in JIT mode, so it cannot execute Flutter's AOT icon
tree-shaker snapshot (`const_finder.dart.snapshot`). Release builds must skip
icon tree shaking:

```bash
flutter build apk --release --target-platform android-arm64 --no-tree-shake-icons
```

---

### 6. Follow-Up Plan: Static Toolchain Packaging (Mode B)

To make Play-compliant (API 35+) optimized builds work without per-project
edits, the project would add a decoupled, optional toolchain installer.

1. **Pick and verify an upstream candidate.**
   - Start with `lzhiyong/termux-ndk` release assets (native `aarch64`
     Android SDK Build-Tools).
   - Verify each candidate locally with `readelf -d`, `aapt2 version`, and a
     minimal API 35 smoke build before committing to it.
2. **Add a decoupled installation hook.**
   - Provide an opt-in flag (for example
     `post_install.sh --enable-modern-android-tools`) or a standalone script.
   - It downloads, extracts, and verifies the pinned Build-Tools package, and
     keeps the main Flutter `.deb` at its current ~640 MB.
3. **Inject a global user override.**
   - Instead of editing project directories, write the override into the
     user's own `~/.gradle/gradle.properties`:

     ```properties
     android.aapt2FromMavenOverride=/data/data/com.termux/files/home/Android/Sdk/build-tools/35.0.0/aapt2
     ```

   - This keeps repositories clean and buildable on a PC or CI server
     unchanged, while enabling Mode B for the Termux user everywhere.
