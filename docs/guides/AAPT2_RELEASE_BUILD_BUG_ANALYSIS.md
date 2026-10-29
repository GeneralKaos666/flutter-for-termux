# AAPT2 Release Build Resource Stripping Bug and Static Compilation Plan

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

## 1. Background and Symptom

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

## 2. Root Cause Analysis

The bug appears only for release builds. AGP runs R8 resource shrinking
(`isShrinkResources = true`) and resource optimization
(`android.enableResourceOptimizations = true`) in that path, rebuilding the
resource package (`.ap_`).

In Termux, AGP's bundled `aapt2` is overridden with the Termux package at
`/data/data/com.termux/files/usr/bin/aapt2`. Five separate problems follow from
that substitution.

### A. AGP resolves aapt2 by OS, not by CPU architecture

Google publishes no ARM64 host build of `aapt2` on Google Maven
([Google issue 227219818](https://issuetracker.google.com/issues/227219818)).
AGP also maps host platforms by OS alone (`SdkConstants.currentPlatform()`),
ignoring the CPU. On any Linux host it therefore requests
`com.android.tools.build:aapt2:<version>:linux`, which resolves to an x86_64
glibc ELF binary. That binary cannot run on ARM64 Bionic inside Termux.

### B. The replacement toolchain breaks modern R8 packaging

In AGP 8.0+, R8 unifies code and resource shrinking ("optimized resource
shrinking"). The missing manifest and corrupt resource tables are not caused
by static analysis pruning active resources. They come from a low-level
toolchain packaging bug: the overridden, mismatched native `aapt2` crashes, or
fails to handle command-line arguments and ZIP stream formats, during the
re-zipping and linking phases.

Because this is a packaging crash rather than a pruning decision, custom keep
rules (`keep.xml`, ProGuard rules) have no effect.

### C. `split-select` is missing

During resource optimization AGP calls `split-select` to generate
configuration split APKs. Google publishes no ARM64 build of `split-select`
and Termux does not package it. The step fails silently or corrupts the
intermediate `.ap_` archive, which `aapt2` then cannot parse.

### D. Rolling-release protobuf instability

The Termux `aapt2` links dynamically against `libprotobuf.so`. Termux is a
rolling release, so `pkg upgrade` frequently bumps `libprotobuf.so.XX`. When
that happens, `aapt2` dies on a dynamic-linker error and the resource
compile/link step exits quietly, leaving truncated or empty assets.

### E. Android 15/16 resource table format changes

Android 15 (API 35) and 16 (API 36) changed the binary layout of
`resources.arsc`. Termux `aapt2` builds based on older AOSP Build-Tools
sources reject the new structure (`RES_TABLE_TYPE_TYPE entry offsets overlap
actual entry data`) and strip the resources they cannot parse.

---

## 3. Deployment Modes and Solution Strategies

The project supports two tracks.

### Mode A: Verified local APK build (default)

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

### Mode B: Experimental publish toolchain (future / CI target)

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

## 4. Comparison of Solutions

| Strategy | How it works | Pros | Cons and risks |
|:---|:---|:---|:---|
| **1. Disable optimizations (Mode A workaround)** | Set `shrink=false` and `android.enableResourceOptimizations=false`, and turn shrinking off in project `buildTypes`. | Stable and reliable; avoids dynamic library crashes; lowers JVM heap use on device. | Larger release APKs (a few MB of dead code); no Play-style resource optimization, so it is not a store-ready configuration. |
| **2. Stub `split-select` and swap** | Drop a dummy `split-select` script that exits `0` into `build-tools/` so validation passes. | Gets simple verification tasks past the check. | Fragile placeholder only. R8 resource shrinking still links corrupt layouts without real asset-split mappings. |
| **3. Bundle static ARM64 Build-Tools (Mode B target)** | Ship statically compiled ARM64 Build-Tools (v35+) and override paths through global Gradle properties. | No dynamic dependencies, so protobuf bumps cannot break it; meets Play API 35/36 requirements; enables full R8 shrinking. | High maintenance cost: every upstream package update has to be tracked and verified. |

### Emulation and container workarounds (Box64 / PRoot)

- **Box64 / QEMU user-mode:** ruled out. Android mounts
  `/proc/sys/fs/binfmt_misc` read-only, so user space cannot register the
  x86_64 emulator handler the JVM would need without root.
- **PRoot distro (Ubuntu container):** ruled out as a general workflow. It
  stabilizes dynamic library dependencies but does not close the CPU
  architecture gap (aapt2 still needs a manual override), adds a 2×–5×
  compile slowdown from `ptrace` syscall interception, and costs 5 GB+ of
  storage.

---

## 5. Current Developer Workaround (Mode A)

For stable on-device builds, apply the project settings below. Both the Gradle
properties and the `buildTypes` overrides are required until matrix smoke
tests show the properties alone are enough.

### 1. `android/gradle.properties`

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

### 2. `android/app/build.gradle.kts`

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

### 3. Release build command

The Termux Dart VM runs in JIT mode, so it cannot execute Flutter's AOT icon
tree-shaker snapshot (`const_finder.dart.snapshot`). Release builds must skip
icon tree shaking:

```bash
flutter build apk --release --target-platform android-arm64 --no-tree-shake-icons
```

---

## 6. Follow-Up Plan: Static Toolchain Packaging (Mode B)

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
