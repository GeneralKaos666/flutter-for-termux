**English** | [中文](UPGRADE_GUIDE_ZH.md)

# Flutter Version Upgrade Guide

This document explains how to upgrade Termux Flutter from the current 3.47.2 to a new version, and lists the risk points in Dart / Flutter Tools / Gradle plugins that must be re-checked after 3.47.2.

---

## 📋 Upgrade Checklist

```
□ Step 1: Update the version in build.toml
□ Step 2: Clone the new Flutter version
□ Step 3: Run gclient sync
□ Step 4: Create the patch directory for the new version and rebase the patches
□ Step 5: Apply the patches (engine / dart / skia)
□ Step 6: Assemble the sysroot
□ Step 7: configure + build (debug / release / profile)
□ Step 8: Run debuild to produce the .deb
□ Step 9: Push to the device and test
□ Step 10: Update post_install.sh (if necessary)
□ Step 11: Publish a GitHub Release
```

## Must-Check Items After 3.47.2

| Item | Why it matters | How to check |
|------|------------|----------|
| `dart` / `dartvm` / `dartaotruntime` | Dart 3.10+ splits the CLI and VM runtime more clearly; packaging only `dart` will break either the snapshots or the Flutter CLI | `python3 build.py debuild --arch=arm64` must pass the artifact validator |
| Flutter Tools host platform | Termux is treated by Dart as an `android` host; official Flutter Tools normally only handles macOS/Linux/Windows | Run `flutter doctor -v` on Termux; it must not crash during the cache/artifact/device-discovery phase |
| Flutter Gradle plugin constants | As of Flutter 3.44, `FlutterPlugin.kt` directly imports `PLATFORM_ABI_LIST` | `flutter build apk --release --target-platform android-arm64 --no-tree-shake-icons` must not show Kotlin unresolved-reference errors |
| Gradle included-build cache | post-install modifies the Kotlin source; on upgrade the old cache may mix old and new sources | `post_install.sh` must clear `packages/flutter_tools/gradle/.gradle`, `build`, `bin` |
| Android SDK / aapt2 | Newer templates may raise `compileSdk`; Termux aapt2 still needs the API 34 workaround | Pin new projects to `compileSdk = 34`, `targetSdk = 34`, `android.aapt2FromMavenOverride` |

---

## Step 1: Update the Version Number

Edit `build.toml`:

```toml
[flutter]
tag = '3.XX.Y'    # ← change to the new version number
```

> Other fields usually don't need to change (NDK path, jobs, etc.).
> If the new Flutter version requires a newer NDK, update the `[ndk]` section as well.

---

## Step 2: Clone the New Flutter Version

```bash
# Run in WSL
cd /root/projects/termux-flutter
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
git apply --stat /root/projects/termux-flutter/patches/engine.patch

# Try applying it to see where it conflicts
git apply --check patches/engine.patch

# Manually fix conflicting files, then regenerate the patch
git diff > /root/projects/termux-flutter/patches/engine.patch
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

## Step 5: Apply the Patches

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

## Step 7: Configure + Build (All Three Modes)

```bash
# Debug (main mode — includes dart-sdk, gen_snapshot, etc.)
python3 build.py configure --arch=arm64 --mode=debug
python3 build.py build --arch=arm64 --mode=debug

# Release (Linux release engine)
python3 build.py configure --arch=arm64 --mode=release
python3 build.py build --arch=arm64 --mode=release

# Profile (Linux profile engine)
python3 build.py configure --arch=arm64 --mode=profile
python3 build.py build --arch=arm64 --mode=profile
```

> ⏱ Each mode takes about 30-60 minutes (24 threads)
>
> ⚠️ **Important**: the `__MODE__` tuple in `utils.py` is `('release', 'debug', 'profile')`,
> with release in the first position. `Output.any` picks the first existing directory,
> so the release (product mode) dart-sdk snapshots drive the Flutter CLI. Do not reorder modes arbitrarily.

---

## Step 8: Build the Extra Tools

```bash
# Dart binary is produced by the standard build via flutter/build/archives:dart_sdk_archive
# (no separate build_dart step in the FFT pipeline)
```

> ✔️ `dart_sdk_archive`, `flutter_patched_sdk`, and `flutter_gtk` are all included in the `build()` ninja invocation.

---

## Step 9: Android gen_snapshot

> In the FFT pipeline the standard `build()` produces the Linux engine targets; Android APK builds on Termux
> use the gen_snapshot shipped in the deb package's artifacts.
> Only ARM64 → ARM64 works; ARM32 and x64 targets cannot be compiled (see the README).

---

## Step 10: Package the .deb

```bash
python3 build.py debuild --arch=arm64
```

This will:
1. Sync the latest `scripts/`, `patches/`, `package.yaml`, `build.toml` from Windows to WSL
2. Collect all build outputs according to `package.yaml`
3. Package them into `flutter_3.XX.Y_aarch64.deb`

> Output path: `/root/projects/termux-flutter/flutter_3.XX.Y_aarch64.deb`

---

## Step 11: Device Testing

```powershell
# Copy from WSL to Windows
Copy-Item "\\wsl.localhost\Ubuntu\root\projects\termux-flutter\flutter_3.XX.Y_aarch64.deb" .

# Push to the device
adb push flutter_3.XX.Y_aarch64.deb /data/local/tmp/

# Install in Termux
dpkg -i /data/local/tmp/flutter_3.XX.Y_aarch64.deb
apt-get install -f
bash $PREFIX/share/flutter/post_install.sh
source $PREFIX/etc/profile.d/flutter.sh

# Verify
flutter doctor -v
flutter create testapp && cd testapp
flutter build apk --release --target-platform android-arm64
flutter build linux --release
```

---

## Step 12: Update post_install.sh (If Necessary)

`scripts/install/post_install.sh` contains many `sed` modifications against the Flutter source code.
If the new Flutter version changes any of these locations, they need to be updated:

| Modification target | Purpose | When it may need updating |
|---------|------|-------------------|
| `build_apk.dart` | Disables arm/x64 platforms | Flutter refactored the APK build logic |
| `build_linux.dart` | Bypasses the `isLinux` check | Flutter changed how the platform is checked |
| `FlutterPluginConstants.kt` | ARM64-only NDK filter | The Gradle plugin structure changed |
| `compileSdkVersion` downgrade | Termux aapt2 limitation | aapt2 got updated to support newer APIs |
| `tool_backend.sh` | Shebang fix | Flutter regenerated this script |
| NDK clang wrapper | Termux ARM64 clang | A new NDK version |

**Testing**: run `post_install.sh` on the device and watch the output for `⚠` warnings or errors.

---

## Step 13: Publish the GitHub Release

```powershell
# Update version
$VER = "3.XX.Y"

# Create the release
gh release create "v$VER" `
  --title "Flutter $VER for Termux ARM64" `
  --notes-file docs/releases/RELEASE_NOTES.md `
  "flutter_${VER}_aarch64.deb"
```

Remember to update:
- The version number in `README.md`
- `docs/releases/CHANGELOG.md`
- `docs/releases/RELEASE_NOTES.md`
- `FLUTTER_VERSION` in `scripts/install/install_termux_flutter.sh`
- The version number in `install_flutter_complete.sh`

---

## 🔄 One-Command Upgrade (If the Patches Apply Cleanly)

If the patches can be applied directly, the whole process can be run with a single command:

```bash
python3 build.py
```

`python3 build.py` (Build.__call__) runs the following in order:
1. config
2. clone
3. sync
4. per arch: sysroot → configure + build (debug → release → profile), including `dart_sdk_archive`
5. debuild

> ⏱ The whole thing takes about 2-4 hours

---

## ⚠️ Common Upgrade Problems

### 1. Patch conflicts

**Symptom**: `git apply` fails

**Cause**: upstream changed files covered by the patch

**Fix**: modify the source manually and regenerate the patch (see Step 4)

### 2. New system dependencies

**Symptom**: a header or library is missing at compile time

**Fix**: add the new package to `[sysroot.termux-main]` or `[sysroot.termux-x11]` in `build.toml`

### 3. GN flag changes

**Symptom**: `gn gen` errors with an unknown variable

**Cause**: Flutter refactored BUILD.gn

**Fix**: check the `build/config/` structure of the new engine and adjust `engine.patch`

### 4. Dart SDK version mismatch

**Symptom**: `package_config.json` language version error

**Fix**: `build.py sync` handles this automatically, but if the version number changed, check the download URL

### 5. post_install.sh sed failure

**Symptom**: `sed` cannot find the target string

**Cause**: the new Flutter version changed the Dart source code that was being modified

**Fix**: manually inspect the target files on the device and update the `sed` search patterns

---

## 📁 Project Structure Reference

```
flutter-for-termux/
├── build.py                  # Main CLI (Python Fire)
├── build.toml                # All config: version, NDK, sync paths
├── package.py                # .deb packaging logic
├── package.yaml              # Declarative: build output → deb install paths
├── sysroot.py                # Downloads Termux apt packages to assemble the sysroot
├── utils.py                  # arch mapping, output paths
├── .gclient                  # gclient sync config
│
├── patches/
│       └── engine.patch       # Flat, tag-agnostic patches
│           dart.patch
│           skia.patch
│
├── scripts/
│   ├── install/
│   │   ├── post_install.sh   # On-device post-install configuration
│   │   └── install_termux_flutter.sh  # One-command install script
│   ├── ci/check_repo.py      # Lightweight repo contract check
│   ├── device/               # ADB → Termux smoke automation
│   ├── test/gh_e2e_test.sh   # GitHub Release E2E test script
│   └── ...
│
├── install_flutter_complete.sh # Complete install script (includes Android SDK)
│
├── README.md                 # English (primary) documentation
├── README_ZH.md              # Chinese translation
├── docs/guides/UPGRADE_GUIDE.md # ← This file
├── docs/releases/CHANGELOG.md   # Version changelog
└── docs/releases/RELEASE_NOTES.md # Release notes
```
