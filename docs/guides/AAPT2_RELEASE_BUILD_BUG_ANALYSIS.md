# AAPT2 Release Build Resource Stripping Bug & Static Compilation Plan

This document records the technical investigation, root cause analysis, workarounds, and long-term follow-up plans regarding the `aapt2 optimize` resource stripping issue on Android/Termux hosts.

> **Status (2026-09-13):** Stage-B modernization is the winner. Termux now ships
> aapt2 16.0.0.4 (Android Build-Tools), which loads the android-36 `android.jar`,
> so new projects default to `compileSdk = 36` / `targetSdk = 36` via
> `post_install.sh`, with a fail-closed ladder 36 → 35 → 34 for devices whose
> aapt2 cannot load the newest platform. **The Mode-A "pin API 34" constraint is
> retired as the default**; the R8 shrinking/resource-optimization workaround
> below remains in effect for on-device build stability.

---

## 1. Background & Symptom

When building a release APK natively inside Termux using the cross-compiled Flutter SDK 3.44.2, the compilation completes successfully (exit code `0`), producing an APK of around 18MB. 

However, attempting to install the resulting APK using `adb install` fails immediately with:
```text
Failure [INSTALL_PARSE_FAILED_UNEXPECTED_EXCEPTION: Failed to parse /data/local/tmp/app-release.apk:
AndroidResourceParser: Failed to read manifest]
```

### Diagnostic Findings
Decompressing the corrupted APK reveals:
*   The `AndroidManifest.xml` is **completely missing**.
*   The `resources.arsc` table is **completely missing**.
*   The `res/` folder is empty or lacks all referenced drawables and layouts.
*   *Note:* Debug APK builds do not undergo shrinking/resource optimization tasks and install successfully.

---

## 2. Root Cause Analysis

The bug is triggered when building a release APK natively inside Termux. During release builds, the Android Gradle Plugin (AGP) runs R8 resource shrinking (`isShrinkResources = true`) and resource optimization (`android.enableResourceOptimizations = true`) to rebuild the resource package (`.ap_`).

In the Termux environment, we override AGP's bundled `aapt2` with the native Termux package (`/data/data/com.termux/files/usr/bin/aapt2`). This configuration triggers critical toolchain compatibility issues:

### A. AGP's Architecture-Agnostic Classifier Resolution
Google does not publish an ARM64 host binary of `aapt2` on Google Maven (see [Google Issue 227219818](https://issuetracker.google.com/issues/227219818)). Furthermore, AGP maps host platforms solely by OS (`SdkConstants.currentPlatform()`), ignoring the CPU architecture. On any Linux host, AGP unconditionally requests:
`com.android.tools.build:aapt2:<version>:linux` (which resolves strictly to the `x86_64` glibc ELF binary). Running this binary on ARM64 Bionic libc inside Termux immediately fails.

### B. Toolchain Packaging Bug (Modern R8 Shrinking)
In AGP 8.0+, R8 performs **Optimized Resource Shrinking** where code and resource shrinking are unified. The missing `AndroidManifest.xml` and corrupted resource tables are **not** caused by static analysis pruning (R8 labeling active resources as unused). Instead, it is a **low-level toolchain packaging bug** where the overridden, mismatched native `aapt2` crashes or fails to process command-line arguments and ZIP stream formats during the re-zipping/linking phases.
*Note: Because this is a toolchain packaging crash rather than a pruning issue, custom keep rules (`keep.xml` or Proguard rules) have **no effect**.*

### C. Missing `split-select` Toolchain
During resource optimization, AGP invokes `split-select` to generate configuration split APKs. Google does not publish an ARM64 version of `split-select`, and Termux does not package it. This step fails silently or corrupts the intermediate `.ap_` archive which `aapt2` is then unable to parse.

### D. Rolling-Release Protobuf Instability
The native Termux `aapt2` is dynamically linked to the system's `libprotobuf.so`. Because Termux is a rolling-release environment, `pkg upgrade` often bumps the `libprotobuf.so.XX` version. When this occurs, `aapt2` immediately crashes with dynamic linker errors, causing resource compile/link steps to silently exit and write truncated/empty assets.

### E. Android 15/16+ Binary Format Incompatibility
Android 15 (API 35) and 16 (API 36) updated the binary structure of `resources.arsc` resource tables. Native Termux `aapt2` packages (often based on older AOSP Build-Tools sources) throw validation errors (`RES_TABLE_TYPE_TYPE entry offsets overlap actual entry data`) when parsing updated structures, stripping out resources they cannot parse.

---

## 3. Deployment Modes & Solution Strategies

To resolve these toolchain limitations, the project establishes two distinct support tracks:

### Mode A: Verified Termux Local APK Build (Default)
*   **Target:** Native local execution, side-loading, and fast debug/test compiles on Termux.
*   **SDK constraints:** Default `compileSdk = 36`, `targetSdk = 36`; `post_install.sh` falls back 36 → 35 → 34 when the installed aapt2 cannot load the newest platform.
*   **Optimization status:** R8 code/resource shrinking and resource optimizations are **completely disabled** to bypass packaging crashes and prevent Out-Of-Memory (OOM) compilation crashes under mobile JVM heap constraints.
*   **Status:** **Verified and stable** (tested on Android 16 Samsung SM-X716B). The API-34 pin is **retired** — aapt2 16.0.0.4 loads android-36 `android.jar`; only the shrinking-disabled workaround remains.

### Mode B: Experimental Publish Toolchain (Future / CI Target)
*   **Target:** Google Play Store compliance, App Bundle (`.aab`) generation, and full code/resource optimization.
*   **SDK constraints:** Targets API 35+ (Android 15 target SDK mandatory for Play Console uploads starting August 2025).
*   **Optimization status:** Enables full R8 minification and resource optimization.
*   **Toolchain requirements:** Replaces the dynamic system `aapt2` with an architecture-matched, statically linked ARM64 Android Build-Tools environment (e.g. from upstream projects like `lzhiyong/termux-ndk`).
*   **Status:** **Experimental**. Must pass automated matrix checks before being promoted to default.

---

## 4. Comparison of Solutions

| Solution Strategy | Implementation Details | Pros | Cons / Risks |
|:---|:---|:---|:---|
| **Strategy 1: Disable Optimizations (Mode A Workaround)** | Set properties (`shrink=false`, `android.enableResourceOptimizations=false`) and modify project `buildTypes` to disable shrinking. | *   **100% stable and reliable**.<br>*   Avoids dynamic library crashes.<br>*   Reduces JVM heap usage on device. | *   Larger release APK size (+3-5MB of dead code).<br>*   Pinning `targetSdk=34` is incompatible with Play Store upload policies. |
| **Strategy 2: Stub `split-select` & Swap** | Inject a dummy `split-select` script that exits `0` in `build-tools/` to pass validation. | *   Bypasses simple verification tasks. | *   **Fragile placeholder only.**<br>*   Does not support R8 resource shrinking (links corrupt layouts without proper asset-split mappings). |
| **Strategy 3: Bundle Static ARM64 Build-Tools (Mode B Target)** | Integrate statically compiled native ARM64 Build-Tools (v35+) and automate path overrides via global Gradle properties. | *   **No dynamic dependencies** (protobuf-immune).<br>*   Matches Play Store API 35/36 requirements.<br>*   Enables full R8 shrinking. | *   High maintainer overhead to track and verify upstream packages (`lzhiyong/termux-ndk`). |

### Note on Emulation & Container Workarounds (Box64 / PRoot)
*   **Box64 / QEMU User-Mode:** Ruled out. Android's kernel blocks user-space configuration of `/proc/sys/fs/binfmt_misc` (read-only), preventing the JVM from transparently wrapping and executing x86_64 binaries through the emulator without root access.
*   **PRoot Distro (Ubuntu Container):** Ruled out as a general workflow. While it stabilizes dynamic library dependencies, it does not bypass the CPU architecture gap (requires manual `aapt2` overrides). It introduces a **2x to 5x compile slowdown** due to `ptrace` system call interception and consumes massive storage (5GB+).

---

## 5. Current Developer Workaround (Mode A)

For stable on-device builds in Termux, apply the following project configurations. Both Gradle properties and `buildTypes` overrides are required for maximum safety until matrix smoke tests verify properties alone are sufficient.

### 1. `android/gradle.properties`
```properties
# Force Gradle to use native Termux AAPT2
android.aapt2FromMavenOverride=/data/data/com.termux/files/usr/bin/aapt2

# Turn off R8 resource optimizations to bypass AAPT2 repackaging failures
android.enableResourceOptimizations=false

# Disable R8 shrinking in Flutter Gradle Plugin
shrink=false

# Optimize JVM heap to prevent OOM compilation crashes in Termux
org.gradle.jvmargs=-Xmx2048m -XX:MaxMetaspaceSize=512m -Dfile.encoding=UTF-8
```

### 2. `android/app/build.gradle.kts`
```kotlin
android {
    compileSdk = 36 // Default API 36; post-install falls back to 35/34 as needed

    defaultConfig {
        targetSdk = 36
        ndk {
            abiFilters += listOf("arm64-v8a") // Pin to ARM64 target only
        }
    }

    buildTypes {
        release {
            // Explicitly disable minification and shrinking
            isMinifyEnabled = false
            isShrinkResources = false
        }
    }
}
```

### 3. Flutter Release Compile Command
Because the Termux Dart VM runs strictly in JIT mode, it is unable to execute Flutter's AOT icon tree-shaker snapshot (`const_finder.dart.snapshot`). Release builds must bypass icon tree shaking:
```bash
flutter build apk --release --target-platform android-arm64 --no-tree-shake-icons
```

---

## 6. Follow-Up Plan: Static Toolchain Packaging (Mode B)

To enable Play Store compliant (API 35+) Optimized builds without manual project modifications, the project will implement a decoupled, optional toolchain installer.

### Task List
1.  **Select & Verify Candidate Upstream:**
    *   Target `lzhiyong/termux-ndk` release assets (e.g. native `aarch64` Android SDK build-tools).
    *   Verify the candidate binaries locally using `readelf -d`, `aapt2 version`, and minimal API 35 smoke compilation before committing.
2.  **Decoupled Installation Hook:**
    *   Provide an optional setup flag (e.g. `post_install.sh --enable-modern-android-tools`) or a standalone installation script.
    *   This script will download, extract, and verify the pinned Build-Tools package, keeping the main Flutter `.deb` package lightweight (~630MB).
3.  **Global User Override Injection:**
    *   Rather than hacking project-level directories, the post-install setup will write the global override path into the Termux user's personal Gradle properties file at `~/.gradle/gradle.properties`:
        ```properties
        android.aapt2FromMavenOverride=/data/data/com.termux/files/home/Android/Sdk/build-tools/35.0.0/aapt2
        ```
    *   This ensures build portability (local repositories remain clean and can be compiled on PCs or CI/CD servers unchanged) while automatically enabling Mode B capability globally for the Termux user.

