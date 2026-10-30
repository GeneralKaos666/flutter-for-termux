# Flutter Version Upgrade Guide

This document explains how to upgrade Termux Flutter from the current 3.47.9
to a new version, and lists the risk points in Dart / Flutter Tools / Gradle
plugins that must be re-checked after 3.47.9.

Build commands and their expected behavior are explained in the
[Build guide](BUILD_GUIDE.md); build internals (patches, GN configs,
sysroot) are documented in the [Build process](BUILD_PROCESS.md).

---

## Upgrading via apt (end users)

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

Never run `flutter upgrade` on this install; it replaces the patched,
cross-built tree with upstream's checkout and breaks the SDK.

The source-rebuild checklist below is maintainer-facing — it produces a new
package from a new upstream Flutter tag, and is not the end-user upgrade path.

---

## Upgrade Checklist

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

## Must-Check Items After 3.47.9

| Item | Why it matters | How to check |
|------|------------|----------|
| `dart` / `dartvm` / `dartaotruntime` | Dart 3.10+ splits the CLI and VM runtime more clearly; packaging only `dart` will break either the snapshots or the Flutter CLI | `python3 build.py debuild --arch=arm64` must pass the artifact validator |
| Android-target `gen_snapshot` | APK AOT needs a `target_os=android` build; the Linux desktop one white-screens the app (issue #41) | Release APK installs and opens; `linux-arm64-android-release/gen_snapshot` exists in the deb |
| Flutter Tools host platform | Termux is treated by Dart as an `android` host; official Flutter Tools normally only handles macOS/Linux/Windows | Run `flutter doctor -v` on Termux; it must not crash during the cache/artifact/device-discovery phase |
| Flutter Gradle plugin constants | As of Flutter 3.44, `FlutterPlugin.kt` directly imports `PLATFORM_ABI_LIST` | `flutter build apk --release --target-platform android-arm64 --no-tree-shake-icons` must not show Kotlin unresolved-reference errors |
| Gradle included-build cache | post-install modifies the Kotlin source; on upgrade the old cache may mix old and new sources | `post_install.sh` must clear `packages/flutter_tools/gradle/.gradle`, `build`, `bin` |
| Android SDK / aapt2 | Newer templates may raise `compileSdk`; Termux aapt2 (16.0.0.4) loads android-36 `android.jar` | Default new projects to `compileSdk = 36`, `targetSdk = 36`, `android.aapt2FromMavenOverride`; post-install falls back 36 → 35 → 34 when aapt2 cannot load the newest platform |

---

## Step 1: Update the Version Number

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

## Step 2: Clone the New Flutter Version

```bash
# Run in WSL
cd ~/termux-flutter
python3 build.py clone
```

This `git clone`s the Flutter repo at the specified tag into `./flutter/`.

---

## Step 3: Run gclient Sync

```bash
python3 build.py sync
```

This will:
1. Copy `.gclient` to the engine directory
2. Run `gclient sync -DR --no-history` (downloads ~30GB engine + deps)
3. Automatically replace the prebuilt dart-sdk with the matching version

> ⏱ Takes about 30-60 minutes (depending on network speed)

---

## Step 4: Rebase the Patches

Patches live flat in `patches/` (engine.patch, dart.patch, skia.patch) and are tag-agnostic.

### Patch Sources and Purpose

| Patch file | Target | Purpose |
|------------|------|------|
| `engine.patch` | Engine BUILD.gn | Adds the `-llog -lm` link flags and the `is_termux` GN variable |
| `dart.patch` | Dart VM | Fixes the bionic TLS alignment issue |
| `skia.patch` | Skia graphics library | Fixes ARM64 bionic compilation issues |

### How to Rebase the Patches

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

### Key Modification Points for Each Patch

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

## Step 5: Confirm the Patches Are Applied

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

## Step 6: Assemble the Sysroot

```bash
python3 build.py sysroot --arch=arm64
```

Downloads `.deb` packages from the Termux apt repo and extracts them into the sysroot directory.

> Usually the sysroot package list in `build.toml` does not need to be modified,
> unless the new Flutter version introduces new system dependencies (for example, adding GTK4).

---

## Step 7: Configure + Build (per Runtime Mode)

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
> compiled (see the [Build guide](BUILD_GUIDE.md#8-known-limitations)).

---

## Step 8: Package the .deb

```bash
python3 build.py debuild --arch=arm64
```

This will:
1. Collect every build output declared in `package.yaml` (SDK, engine
   artifacts, both Android `gen_snapshot` builds, scripts)
2. Render `manifest.json` and the package metadata from `build.toml`
3. Package everything into the deb

> Output path: `release/flutter_<tag>-<pkg_rel>_aarch64.deb`
> (for example `release/flutter_3.47.9-1_aarch64.deb`).

---

## Step 9: Device Testing

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

## Step 10: Update post_install.sh (If Necessary)

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

## Step 11: Publish the GitHub Release

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

## Step 12: Publish the flutter Stub to termux-apt-repo

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

## One-Command Upgrade (If the Patches Apply Cleanly)

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

## Common Upgrade Problems

Each entry shows the exact error (**Symptom**), why it happens (**Cause**),
and the fix (**Fix**).

### 1. Patch conflicts

- **Symptom**: `git apply` fails
- **Cause**: upstream changed files covered by the patch
- **Fix**: modify the source manually and regenerate the patch (see Step 4)

### 2. New system dependencies

- **Symptom**: a header or library is missing at compile time
- **Cause**: the new Flutter version needs a system package the sysroot does not ship yet
- **Fix**: add the new package to `[sysroot.termux-main]` or `[sysroot.termux-x11]` in `build.toml`

### 3. GN flag changes

- **Symptom**: `gn gen` errors with an unknown variable
- **Cause**: Flutter refactored BUILD.gn
- **Fix**: check the `build/config/` structure of the new engine and adjust `engine.patch`

### 4. Dart SDK version mismatch

- **Symptom**: `package_config.json` language version error
- **Cause**: the prebuilt dart-sdk replacement no longer matches the new engine
- **Fix**: `build.py sync` handles this automatically, but if the version number changed, check the download URL

### 5. post_install.sh sed failure

- **Symptom**: `sed` cannot find the target string
- **Cause**: the new Flutter version changed the Dart source code that was being modified
- **Fix**: manually inspect the target files on the device and update the `sed` search patterns

---

## Patch Maintenance Tips

1. **Keep patches minimal**: only modify what is necessary
2. **Add comments**: explain in the patch why the modification is needed
3. **Version isolation**: each Flutter version has its own patch directory
4. **Record changes**: update docs/releases/CHANGELOG.md

---

## Project Structure Reference

See the merged tree in
[Build process — File Structure Reference](BUILD_PROCESS.md#file-structure-reference).
