#!/data/data/com.termux/files/usr/bin/bash
# Flutter Termux Post-Install Script
# Run this script after installing the deb package to configure the APK build environment

set -e

echo "=========================================="
echo "Flutter Termux Post-Install Configuration"
echo "=========================================="

# Path definitions
PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
FLUTTER_ROOT="${FLUTTER_ROOT:-$PREFIX/opt/flutter}"
ANDROID_SDK="${ANDROID_SDK:-$PREFIX/opt/android-sdk}"
DART_SDK="${DART_SDK:-$FLUTTER_ROOT/bin/cache/dart-sdk}"
if [ -z "${FLUTTER_PREBUILT_ENGINE_VERSION:-}" ] && [ -f "$FLUTTER_ROOT/bin/internal/engine.version" ]; then
	export FLUTTER_PREBUILT_ENGINE_VERSION="$(cat "$FLUTTER_ROOT/bin/internal/engine.version" 2>/dev/null | tr -d '\n\r')"
fi

export PATH="$PREFIX/bin:$PATH"
PATCH_STATE_FILE="${PATCH_STATE_FILE:-$PREFIX/share/flutter/patch_state.json}"
BACKUP_DIR="${BACKUP_DIR:-$PREFIX/share/flutter/backups}"

# SDK-level configuration: env override > package manifest > default
# TERMUX_COMPILE_SDK/TERMUX_TARGET_SDK let users force a specific API level on
# the device; the shipped manifest (package.yaml) carries the build-time default.
COMPILE_SDK="${TERMUX_COMPILE_SDK:-36}"
TARGET_SDK="${TERMUX_TARGET_SDK:-$COMPILE_SDK}"
if [ -f "$PREFIX/share/flutter/manifest.json" ]; then
	_m_sdk=$(grep -o '"compile_sdk": *[0-9]*' "$PREFIX/share/flutter/manifest.json" 2>/dev/null | grep -o '[0-9]*' || echo "")
	_m_tsdk=$(grep -o '"target_sdk": *[0-9]*' "$PREFIX/share/flutter/manifest.json" 2>/dev/null | grep -o '[0-9]*' || echo "")
	[ -n "$_m_sdk" ] && COMPILE_SDK="$_m_sdk"
	[ -n "$_m_tsdk" ] && TARGET_SDK="$_m_tsdk"
	[ -n "${TERMUX_COMPILE_SDK:-}" ] && COMPILE_SDK="$TERMUX_COMPILE_SDK"
	[ -n "${TERMUX_TARGET_SDK:-}" ] && TARGET_SDK="$TERMUX_TARGET_SDK"
fi

# NDK version the Flutter tool defaults to. The shipped package builds and
# ships exactly one NDK (build.toml [ndk] -> manifest.json "ndk_version").
# Upstream Flutter defaults to 28.2.13676358, but on an aarch64 Termux host
# AGP would auto-download "android-ndk-r28c-linux" — Google publishes only the
# x86-64 linux NDK build, whose tools cannot run on this device. Pinning the
# default to the shipped NDK (set up for the Termux aarch64 host) avoids that.
# The fallback must match build.toml [ndk] version / versions_common.sh
# NDK_VERSION; manifest.json normally overrides it.
NDK_VERSION="${TERMUX_NDK_VERSION:-30.0.14206865}"
if [ -f "$PREFIX/share/flutter/manifest.json" ]; then
	_m_ndk=$(grep -o '"ndk_version": *"[^"]*"' "$PREFIX/share/flutter/manifest.json" 2>/dev/null | cut -d'"' -f4 || echo "")
	[ -n "$_m_ndk" ] && NDK_VERSION="$_m_ndk"
	[ -n "${TERMUX_NDK_VERSION:-}" ] && NDK_VERSION="$TERMUX_NDK_VERSION"
fi

MODE="${MODE:-apply}"
if [ "${1:-}" = "--check" ]; then MODE="check"; fi
if [ "${1:-}" = "--apply" ]; then MODE="apply"; fi
if [ "${1:-}" = "--status" ]; then MODE="status"; fi
if [ "${1:-}" = "--rollback" ]; then MODE="rollback"; fi
if [ "${1:-}" = "--provision-ndk" ]; then MODE="provision-ndk"; fi
if [ "${1:-}" = "--lib" ]; then MODE="lib"; fi

if [ "$MODE" != "status" ] && [ "$MODE" != "check" ] && [ "$MODE" != "lib" ] && [ "$MODE" != "provision-ndk" ]; then
	mkdir -p "$BACKUP_DIR" "$(dirname "$PATCH_STATE_FILE")"
	if [ ! -f "$PATCH_STATE_FILE" ]; then
		echo "{}" >"$PATCH_STATE_FILE"
	fi
fi

declare -A STATE_TARGET
declare -A STATE_PREIMAGE
declare -A STATE_POSTIMAGE
declare -A STATE_STATUS
declare -a PATCH_ORDER
declare -A PATCH_FUNCS

# Parse existing state
regex_start='"([^"]+)"[[:space:]]*:[[:space:]]*\{'
regex_field='"([^"]+)"[[:space:]]*:[[:space:]]*"([^"]+)"'

if [ -f "$PATCH_STATE_FILE" ]; then
	while read -r line; do
		if [[ "$line" =~ $regex_start ]]; then
			P_NAME="${BASH_REMATCH[1]}"
		elif [[ "$line" =~ $regex_field ]]; then
			P_KEY="${BASH_REMATCH[1]}"
			P_VAL="${BASH_REMATCH[2]}"
			if [ "$P_KEY" = "target" ]; then STATE_TARGET["$P_NAME"]="$P_VAL"; fi
			if [ "$P_KEY" = "preimage" ]; then STATE_PREIMAGE["$P_NAME"]="$P_VAL"; fi
			if [ "$P_KEY" = "postimage" ]; then STATE_POSTIMAGE["$P_NAME"]="$P_VAL"; fi
			if [ "$P_KEY" = "status" ]; then STATE_STATUS["$P_NAME"]="$P_VAL"; fi
		fi
	done <"$PATCH_STATE_FILE"
fi

save_state() {
	echo "{" >"$PATCH_STATE_FILE"
	local first=1
	for patch in "${PATCH_ORDER[@]}"; do
		if [ -n "${STATE_STATUS[$patch]}" ]; then
			if [ $first -eq 0 ]; then echo "  ," >>"$PATCH_STATE_FILE"; else first=0; fi
			echo "  \"$patch\": {" >>"$PATCH_STATE_FILE"
			echo "    \"target\": \"${STATE_TARGET[$patch]}\"," >>"$PATCH_STATE_FILE"
			echo "    \"preimage\": \"${STATE_PREIMAGE[$patch]}\"," >>"$PATCH_STATE_FILE"
			echo "    \"postimage\": \"${STATE_POSTIMAGE[$patch]}\"," >>"$PATCH_STATE_FILE"
			echo "    \"status\": \"${STATE_STATUS[$patch]}\"" >>"$PATCH_STATE_FILE"
			echo -n "  }" >>"$PATCH_STATE_FILE"
		fi
	done
	echo "" >>"$PATCH_STATE_FILE"
	echo "}" >>"$PATCH_STATE_FILE"
}

register_patch() {
	local name="$1"
	local target="$2"
	local func="$3"
	PATCH_ORDER+=("$name")
	STATE_TARGET["$name"]="$target"
	PATCH_FUNCS["$name"]="$func"
}

apply_patches() {
	local any_failed=0
	for patch_name in "${PATCH_ORDER[@]}"; do
		local target_file="${STATE_TARGET[$patch_name]}"
		local patch_func="${PATCH_FUNCS[$patch_name]}"

		if [ ! -f "$target_file" ]; then
			echo "  ⚠ $patch_name: target missing ($target_file)"
			continue
		fi

		local current_hash
		current_hash=$(sha256sum "$target_file" | awk '{print $1}')
		local state_status="${STATE_STATUS[$patch_name]}"
		local state_post="${STATE_POSTIMAGE[$patch_name]}"

		if [ "$state_status" == "applied" ] && [ "$current_hash" == "$state_post" ]; then
			if [ "$MODE" == "status" ] || [ "$MODE" == "check" ]; then echo "  ✓ $patch_name: already applied"; fi
			continue
		fi

		if [ "$MODE" == "status" ] || [ "$MODE" == "check" ]; then
			local tmp_check
			tmp_check=$(mktemp 2>/dev/null || echo "${TMPDIR:-/tmp}/patch_check_${$}_${RANDOM}")
			cp "$target_file" "$tmp_check" 2>/dev/null || true
			if $patch_func "$tmp_check" 2>/dev/null && cmp -s "$target_file" "$tmp_check"; then
				echo "  ✓ $patch_name: already correct"
				rm -f "$tmp_check"
				continue
			fi
			rm -f "$tmp_check"
			echo "  + $patch_name: pending"
			continue
		fi

		if [ "$MODE" == "status" ] || [ "$MODE" == "check" ]; then
			continue
		fi

		echo "  Applying $patch_name..."
		if [ ! -f "$BACKUP_DIR/$patch_name.orig" ] || [ -z "${STATE_PREIMAGE[$patch_name]}" ] || [ "$current_hash" != "${STATE_PREIMAGE[$patch_name]}" ]; then
			cp "$target_file" "$BACKUP_DIR/$patch_name.orig"
		fi

		STATE_PREIMAGE["$patch_name"]="$current_hash"
		cp "$target_file" "$target_file.tmp"

		if ! $patch_func "$target_file.tmp"; then
			echo "  ✗ $patch_name: unknown upstream content (patch failed)"
			rm -f "$target_file.tmp"
			any_failed=1
			continue
		fi

		if cmp -s "$target_file" "$target_file.tmp"; then
			echo "  ✓ $patch_name: already correct"
			rm -f "$target_file.tmp"
			STATE_POSTIMAGE["$patch_name"]="$current_hash"
			STATE_STATUS["$patch_name"]="applied"
			continue
		fi

		mv "$target_file.tmp" "$target_file"
		local new_hash
		new_hash=$(sha256sum "$target_file" | awk '{print $1}')
		STATE_POSTIMAGE["$patch_name"]="$new_hash"
		STATE_STATUS["$patch_name"]="applied"
		echo "  ✓ $patch_name: successful"
	done

	if [ "$MODE" == "apply" ]; then
		save_state
	fi
	if [ $any_failed -eq 1 ]; then
		echo "Some patches failed. Aborting."
		exit 1
	fi
}

# NOTE: NDK backup/restore helper. This is the simple variant; the richer
# sibling that mapped both ANDROID_HOME and NDK_PATH and tracked absent
# preimages lived in install_flutter_complete.sh (the deprecated one-shot
# installer, since removed). The shared contract it was forked from: the
# `BACKUP_DIR`/`ndk_backups` preimage directory plus the exact `cp -a`
# backup/restore commands. scripts/ci/check_repo.py (check_ndk_helper_drift)
# fails CI when this copy loses that contract.
backup_ndk_file() {
	local file="$1"
	if [ -f "$file" ] || [ -L "$file" ]; then
		local rel_path="${file#$ANDROID_SDK/}"
		local backup_target="$BACKUP_DIR/ndk_backups/$rel_path"
		if [ ! -f "$backup_target" ] && [ ! -L "$backup_target" ]; then
			mkdir -p "$(dirname "$backup_target")"
			cp -a "$file" "$backup_target" 2>/dev/null || true
		fi
	fi
}

restore_ndk_backups() {
	if [ -d "$BACKUP_DIR/ndk_backups" ]; then
		echo "  Restoring NDK backups..."
		(
			cd "$BACKUP_DIR/ndk_backups"
			find . -type f -o -type l | while read -r rel; do
				local dest="$ANDROID_SDK/$rel"
				mkdir -p "$(dirname "$dest")"
				cp -a "$rel" "$dest" 2>/dev/null || true
				echo "  ✓ Restored $dest"
			done
		)
	fi
}

rollback_patches() {
	for patch_name in "${PATCH_ORDER[@]}"; do
		local target_file="${STATE_TARGET[$patch_name]}"
		if [ "${STATE_STATUS[$patch_name]}" == "applied" ]; then
			if [ -f "$BACKUP_DIR/$patch_name.orig" ]; then
				cp "$BACKUP_DIR/$patch_name.orig" "$target_file"
				echo "  ✓ $patch_name: rolled back"
				STATE_STATUS["$patch_name"]="rolled_back"
			else
				echo "  ✗ $patch_name: backup not found!"
			fi
		fi
	done
	restore_ndk_backups
	save_state
}

# --- Register Patches ---

patch_compile_sdk() {
	if grep -F -q "val compileSdkVersion: Int = $COMPILE_SDK" "$1"; then return 0; fi
	grep -q "val compileSdkVersion: Int =" "$1" || return 1
	sed -i "s/val compileSdkVersion: Int = [0-9]*/val compileSdkVersion: Int = $COMPILE_SDK/" "$1"
}
register_patch "compile_sdk" "$FLUTTER_ROOT/packages/flutter_tools/gradle/src/main/kotlin/FlutterExtension.kt" patch_compile_sdk

patch_ndk_version() {
	# Pin the Flutter Gradle plugin's default ndkVersion to the NDK shipped by
	# this package (see NDK_VERSION above). Generated projects reference
	# `flutter.ndkVersion`, which resolves to this Kotlin default at build time.
	if grep -F -q "val ndkVersion: String = \"$NDK_VERSION\"" "$1"; then return 0; fi
	grep -q "val ndkVersion: String =" "$1" || return 1
	sed -i "s/val ndkVersion: String = \"[0-9.]*\"/val ndkVersion: String = \"$NDK_VERSION\"/" "$1"
}
register_patch "ndk_version" "$FLUTTER_ROOT/packages/flutter_tools/gradle/src/main/kotlin/FlutterExtension.kt" patch_ndk_version

patch_ndk_version_dart() {
	# Same pin for the Dart-side constant (flutter_tools): it feeds create-time
	# template rendering (module/plugin projects) and NDK validation. Baked into
	# flutter_tools.snapshot by finalize_flutter_tools_cache (step 17).
	if grep -F -q "const ndkVersion = '$NDK_VERSION'" "$1"; then return 0; fi
	grep -q "const ndkVersion = " "$1" || return 1
	sed -i "s/const ndkVersion = '[0-9.]*'/const ndkVersion = '$NDK_VERSION'/" "$1"
}
register_patch "ndk_version_dart" "$FLUTTER_ROOT/packages/flutter_tools/lib/src/android/gradle_utils.dart" patch_ndk_version_dart

# No early-return idempotency guard here on purpose: this patch rewrites the
# whole file with a heredoc, and re-entrancy is handled by apply_patches()
# (cmp -s "$target_file" "$target_file.tmp" and the state-hash skip). Do not
# "restore" a removed guard.
patch_plugin_constants() {
	cat >"$1" <<'INNER_EOF'
package com.flutter.gradle

object FlutterPluginConstants {
    private const val PLATFORM_ARM32 = "android-arm"
    private const val PLATFORM_ARM64 = "android-arm64"
    private const val PLATFORM_X86_64 = "android-x64"

    private const val ARCH_ARM32 = "armeabi-v7a"
    private const val ARCH_ARM64 = "arm64-v8a"
    private const val ARCH_X86_64 = "x86_64"

    const val INTERMEDIATES_DIR = "intermediates"
    const val FLUTTER_STORAGE_BASE_URL = "FLUTTER_STORAGE_BASE_URL"
    const val DEFAULT_MAVEN_HOST = "https://storage.googleapis.com"

    @JvmStatic val PLATFORM_ARCH_MAP =
        mapOf(
            PLATFORM_ARM32 to ARCH_ARM32,
            PLATFORM_ARM64 to ARCH_ARM64,
            PLATFORM_X86_64 to ARCH_X86_64
        )

    @JvmStatic val ABI_VERSION =
        mapOf(
            ARCH_ARM32 to 1,
            ARCH_ARM64 to 2,
            ARCH_X86_64 to 4
        )

    @JvmStatic val DEFAULT_PLATFORMS =
        listOf(
            PLATFORM_ARM64
        )

    @JvmStatic val PLATFORM_ABI_LIST: List<String> =
        DEFAULT_PLATFORMS.map { platform ->
            PLATFORM_ARCH_MAP[platform] ?: error("Invalid platform: $platform")
        }
}
INNER_EOF
}
register_patch "plugin_constants" "$FLUTTER_ROOT/packages/flutter_tools/gradle/src/main/kotlin/FlutterPluginConstants.kt" patch_plugin_constants

patch_build_apk() {
	if grep -F -q "['android-arm64']" "$1"; then return 0; fi
	grep -q "_kDefaultJitArchs" "$1" || return 1
	sed -i "s/static const _kDefaultJitArchs = <String>\['android-arm', 'android-arm64', 'android-x64'\]/static const _kDefaultJitArchs = <String>['android-arm64']/" "$1"
	sed -i "s/static const _kDefaultAotArchs = <String>\['android-arm', 'android-arm64', 'android-x64'\]/static const _kDefaultAotArchs = <String>['android-arm64']/" "$1"
}
register_patch "build_apk" "$FLUTTER_ROOT/packages/flutter_tools/lib/src/commands/build_apk.dart" patch_build_apk

patch_build_aar() {
	if grep -F -q "defaultsTo: <String>['android-arm64']" "$1"; then return 0; fi
	grep -q "defaultsTo: <String>" "$1" || return 1
	sed -i "s/defaultsTo: <String>\['android-arm', 'android-arm64', 'android-x64'\]/defaultsTo: <String>['android-arm64']/" "$1"
}
register_patch "build_aar" "$FLUTTER_ROOT/packages/flutter_tools/lib/src/commands/build_aar.dart" patch_build_aar

patch_build_appbundle() {
	if grep -F -q "defaultsTo: <String>['android-arm64']" "$1"; then return 0; fi
	grep -q "defaultsTo: <String>" "$1" || return 1
	sed -i "s/defaultsTo: <String>\['android-arm', 'android-arm64', 'android-x64'\]/defaultsTo: <String>['android-arm64']/" "$1"
}
register_patch "build_appbundle" "$FLUTTER_ROOT/packages/flutter_tools/lib/src/commands/build_appbundle.dart" patch_build_appbundle

patch_plugin_utils() {
	# forceNdkDownload() patched to early return.
	# The body is replaced with a single `return` rather than injecting a
	# `return` above the first statement: an injected return leaves the rest of
	# the function unreachable, and Kotlin does not smart-cast in unreachable
	# code, so the generated `androidComponents.finalizeDsl { ... }` fails to
	# compile with "Only safe (?.) or non-null asserted (!!.) calls are allowed
	# on a nullable receiver of type 'AndroidComponentsExtension<*, *, *>?'"
	# (Flutter 3.47.8, flutter_tools/gradle). Replacing the whole body keeps the
	# same runtime behaviour (NDK is preinstalled on Termux) with no dead code.
	if grep -F -q "Termux: NDK provisioning disabled" "$1"; then return 0; fi
	grep -q "fun forceNdkDownload" "$1" || return 1
	awk '
		/^[[:space:]]*internal fun forceNdkDownload\(/ { in_sig = 1 }
		in_body && /^    }[[:space:]]*$/ { print; in_body = 0; next }
		in_body { next }
		in_sig && /\)[[:space:]]*\{[[:space:]]*$/ {
			print
			print "        return // Termux: NDK provisioning disabled (NDK preinstalled)"
			in_sig = 0; in_body = 1; next
		}
		{ print }
	' "$1" > "$1.termux_tmp" && mv "$1.termux_tmp" "$1"
}
register_patch "plugin_utils" "$FLUTTER_ROOT/packages/flutter_tools/gradle/src/main/kotlin/FlutterPluginUtils.kt" patch_plugin_utils

patch_flutter_cache() {
	if grep -F -q "_platform.isAndroid" "$1"; then return 0; fi
	grep -q "artifacts\[_platform.operatingSystem\]" "$1" || return 1
	sed -i "s|final List<String>? binaryDirs = artifacts\[_platform.operatingSystem\];|final List<String>? binaryDirs = artifacts[_platform.isAndroid ? 'linux' : _platform.operatingSystem]; // Termux: map Android host to Linux artifacts|" "$1"
}
register_patch "flutter_cache" "$FLUTTER_ROOT/packages/flutter_tools/lib/src/flutter_cache.dart" patch_flutter_cache

patch_artifacts() {
	# (1) Map the Android host platform to the Linux artifact directories.
	if ! grep -F -q "if (platform.isLinux || platform.isAndroid) { // Termux: map Android host to Linux artifacts." "$1"; then
		grep -q "if (platform.isLinux)" "$1" || return 1
		sed -i "s#if (platform.isLinux) {#if (platform.isLinux || platform.isAndroid) { // Termux: map Android host to Linux artifacts.#" "$1"
	fi
	# (2) Resolve the Android AOT gen_snapshot to the Android-target binary
	# shipped in the package, not the Linux desktop gen_snapshot. flutter_tools
	# computes <engine>/android-arm64-{release,profile}/<host>/gen_snapshot, and
	# flutter's first-run cache materialization writes the upstream linux-x64
	# files into those per-build directories (dropping the step-15 host symlink
	# on the way), so that directory cannot be trusted. The Linux desktop
	# gen_snapshot (<engine>/linux-arm64/) produces target_os=linux /
	# no-compressed-pointers snapshots that the Android runtime rejects
	# (issue #41), so it must not be reused for Android AOT. The Android-target
	# gen_snapshot is shipped at <engine>/linux-arm64-android-{mode}/gen_snapshot,
	# which flutter never writes. Existence-gated: hosts without the file keep
	# upstream behavior.
	if ! grep -F -q "// Termux: prefer package-shipped aarch64 gen_snapshot." "$1"; then
		grep -F -q "final String hostPlatformName = hostPlatform.cliName;" "$1" || return 1
		awk '
			!patched && index($0, "final String hostPlatformName = hostPlatform.cliName;") {
				print "        // Termux: prefer package-shipped aarch64 gen_snapshot."
				print "        // flutter materializes the upstream linux-x64 files into this"
				print "        // directory on the first build after install and drops the step-15"
				print "        // linux-arm64 symlink on the way, so the flutter-managed per-build"
				print "        // directory cannot be trusted. The Linux desktop gen_snapshot would"
				print "        // produce target_os=linux/no-compressed-pointers snapshots that the"
				print "        // Android runtime rejects, so the Android-target gen_snapshot ships at"
				print "        // <engine>/linux-arm64-android-{mode}/gen_snapshot instead (issue #41)."
				print "        final String termuxGenSnapshot = _fileSystem.path.join("
				print "          _fileSystem.directory(engineDir).parent.path,"
				print "          \"linux-arm64-android-${mode.cliName}\","
				print "          artifact.getFileName(_platform),"
				print "        );"
				print "        if (_fileSystem.file(termuxGenSnapshot).existsSync()) {"
				print "          return termuxGenSnapshot;"
				print "        }"
				patched = 1
			}
			{ print }
		' "$1" > "$1.termux_gen_tmp" && mv "$1.termux_gen_tmp" "$1"
	fi
}
register_patch "artifacts" "$FLUTTER_ROOT/packages/flutter_tools/lib/src/artifacts.dart" patch_artifacts

patch_build_info() {
	if grep -F -q "globals.platform.isAndroid" "$1"; then return 0; fi
	grep -q "if (globals.platform.isLinux)" "$1" || return 1
	sed -i "s#if (globals.platform.isLinux) {#if (globals.platform.isLinux || globals.platform.isAndroid) { // Termux: Android host uses Linux artifacts.#" "$1"
}
register_patch "build_info" "$FLUTTER_ROOT/packages/flutter_tools/lib/src/build_info.dart" patch_build_info

patch_chrome() {
	if grep -F -q "if (platform.isLinux || platform.isAndroid) { // Termux: use Linux Chrome lookup on Android host." "$1"; then return 0; fi
	grep -q "if (platform.isLinux)" "$1" || return 1
	sed -i "s#if (platform.isLinux) {#if (platform.isLinux || platform.isAndroid) { // Termux: use Linux Chrome lookup on Android host.#" "$1"
}
register_patch "chrome" "$FLUTTER_ROOT/packages/flutter_tools/lib/src/web/chrome.dart" patch_chrome

patch_build_linux() {
	if grep -F -q "false /* Termux" "$1"; then return 0; fi
	grep -q "if (!globals.platform.isLinux)" "$1" || return 1
	sed -i "s@if (!globals.platform.isLinux)@if (false /* Termux: allow linux build */)@" "$1"
	sed -i "s@!featureFlags.isLinuxEnabled || !globals.platform.isLinux@!featureFlags.isLinuxEnabled /* Termux: visible */@" "$1"
}
register_patch "build_linux" "$FLUTTER_ROOT/packages/flutter_tools/lib/src/commands/build_linux.dart" patch_build_linux

patch_icon_tree_shaker() {
	if grep -F -q "false /* Termux" "$1"; then return 0; fi
	grep -q "kIconTreeShakerFlag" "$1" || return 1
	sed -i "s|_environment.defines\[kIconTreeShakerFlag\] == 'true'|false /* Termux: const_finder unavailable */|g" "$1"
}
register_patch "icon_tree_shaker" "$FLUTTER_ROOT/packages/flutter_tools/lib/src/build_system/targets/icon_tree_shaker.dart" patch_icon_tree_shaker

patch_cmake_lists() {
	if grep -F -q "FlutterNDKTrick" "$1"; then return 0; fi
	cat >"$1" <<'CMAKEOF'
cmake_minimum_required(VERSION 3.6)
project(FlutterNDKTrick C CXX)
CMAKEOF
}
register_patch "cmake_lists" "$FLUTTER_ROOT/packages/flutter_tools/gradle/src/main/scripts/CMakeLists.txt" patch_cmake_lists

patch_shebang_flutter() {
	if grep -F -q "#!${PREFIX:-/data/data/com.termux/files/usr}/bin/bash" "$1"; then return 0; fi
	sed -i "1s|#!/usr/bin/env bash|#!${PREFIX:-/data/data/com.termux/files/usr}/bin/bash|" "$1"
}
patch_shebang_dart() {
	if grep -F -q "#!${PREFIX:-/data/data/com.termux/files/usr}/bin/bash" "$1"; then return 0; fi
	sed -i "1s|#!/usr/bin/env bash|#!${PREFIX:-/data/data/com.termux/files/usr}/bin/bash|" "$1"
}
patch_shebang_shared() {
	if grep -F -q "#!${PREFIX:-/data/data/com.termux/files/usr}/bin/bash" "$1"; then return 0; fi
	sed -i "1s|#!/usr/bin/env bash|#!${PREFIX:-/data/data/com.termux/files/usr}/bin/bash|" "$1"
}
patch_shebang_update_dart() {
	if grep -F -q "#!${PREFIX:-/data/data/com.termux/files/usr}/bin/bash" "$1"; then return 0; fi
	sed -i "1s|#!/usr/bin/env bash|#!${PREFIX:-/data/data/com.termux/files/usr}/bin/bash|" "$1"
}
patch_shebang_content_hash() {
	if grep -F -q "#!${PREFIX:-/data/data/com.termux/files/usr}/bin/bash" "$1"; then return 0; fi
	sed -i "1s|#!/usr/bin/env bash|#!${PREFIX:-/data/data/com.termux/files/usr}/bin/bash|" "$1"
}
patch_shebang_last_engine() {
	if grep -F -q "#!${PREFIX:-/data/data/com.termux/files/usr}/bin/bash" "$1"; then return 0; fi
	sed -i "1s|#!/usr/bin/env bash|#!${PREFIX:-/data/data/com.termux/files/usr}/bin/bash|" "$1"
}
patch_shebang_update_engine() {
	if grep -F -q "#!${PREFIX:-/data/data/com.termux/files/usr}/bin/bash" "$1"; then return 0; fi
	sed -i "1s|#!/usr/bin/env bash|#!${PREFIX:-/data/data/com.termux/files/usr}/bin/bash|" "$1"
}
patch_shebang_tool_backend() {
	if grep -F -q "#!${PREFIX:-/data/data/com.termux/files/usr}/bin/bash" "$1"; then return 0; fi
	sed -i "1s|#!/usr/bin/env bash|#!${PREFIX:-/data/data/com.termux/files/usr}/bin/bash|" "$1"
}

patch_channel_stable() {
	# Flutter derives `channel` from the local git branch (version.dart
	# getBranchName). On a Termux install that branch is not an official
	# channel, so flutter.version.json ends up with "[user-branch]" or the
	# raw branch name instead of "stable". Pin it to stable; the recompiled
	# flutter_tools.snapshot (step 17) then bakes this in permanently.
	if grep -F -q "Termux: force stable channel" "$1"; then return 0; fi
	grep -q "getBranchName(redactUnknownBranches: true)" "$1" || return 1
	sed -i "s/final String channel = getBranchName(redactUnknownBranches: true);/final String channel = 'stable' \/* Termux: force stable channel *\/;/" "$1"
}

register_patch "channel_stable" "$FLUTTER_ROOT/packages/flutter_tools/lib/src/version.dart" patch_channel_stable

register_patch "shebang_flutter" "$FLUTTER_ROOT/bin/flutter" patch_shebang_flutter
register_patch "shebang_dart" "$FLUTTER_ROOT/bin/dart" patch_shebang_dart
register_patch "shebang_shared" "$FLUTTER_ROOT/bin/internal/shared.sh" patch_shebang_shared
register_patch "shebang_update_dart" "$FLUTTER_ROOT/bin/internal/update_dart_sdk.sh" patch_shebang_update_dart
register_patch "shebang_content_hash" "$FLUTTER_ROOT/bin/internal/content_aware_hash.sh" patch_shebang_content_hash
register_patch "shebang_last_engine" "$FLUTTER_ROOT/bin/internal/last_engine_commit.sh" patch_shebang_last_engine
register_patch "shebang_update_engine" "$FLUTTER_ROOT/bin/internal/update_engine_version.sh" patch_shebang_update_engine
register_patch "shebang_tool_backend" "$FLUTTER_ROOT/packages/flutter_tools/bin/tool_backend.sh" patch_shebang_tool_backend

if [ "$MODE" == "rollback" ]; then
	echo "Rolling back patches..."
	rollback_patches
	exit 0
fi

if [ "$MODE" == "status" ] || [ "$MODE" == "check" ]; then
	echo "Checking patch status..."
	apply_patches
	exit 0
fi

# Helper: does this NDK ship a clang driver that actually runs on this device?
#
# The official Android NDK for Linux is x86_64-only, and AGP downloads it under
# the same version number as the aarch64 repack this project ships. Its
# toolchains/llvm, build/cmake/android.toolchain.cmake and source.properties are
# all present, so every existence check passes, but each tool is an x86_64 ELF:
# execve() fails with ENOEXEC, the shell re-parses the bytes as a script, and the
# CMake compiler probe dies with
#     .../prebuilt/bin/clang: 1: Syntax error: ")" unexpected
# Running a driver is the only reliable discriminator, so callers treat an NDK
# with no runnable clang as unusable (issue #29).
ndk_clang_runs() {
	local ndk_dir="$1"
	local driver
	for driver in "$ndk_dir"/toolchains/llvm/prebuilt/*/bin/clang-[0-9]*; do
		[ -x "$driver" ] || continue
		if "$driver" --version >/dev/null 2>&1; then
			return 0
		fi
	done
	return 1
}

# Helper: validate/repair an NDK directory so AGP can use it.
# AGP refuses to configure native modules when <sdk>/ndk/<version>/source.properties
# is absent, failing with "[CXX1101] NDK at ... did not have a source.properties
# file". A partially provisioned or corrupt NDK directory can lack it.
#
# A directory is only repaired when it can actually configure a native build. A
# partially extracted tree keeps toolchains/llvm but loses
# build/cmake/android.toolchain.cmake; minting source.properties onto it would
# satisfy AGP's metadata check while leaving CMake unable to find the toolchain,
# so the broken tree would report as healthy forever. Such a directory is removed
# instead, so the installer re-provisions it.
repair_ndk_metadata() {
	local NDK_PATH="$1"
	local NDK_NAME
	NDK_NAME=$(basename "$NDK_PATH")

	if [ ! -d "$NDK_PATH/toolchains/llvm" ] ||
		[ ! -f "$NDK_PATH/build/cmake/android.toolchain.cmake" ]; then
		echo "  ⚠ Removing incomplete NDK directory $NDK_NAME (missing toolchains/llvm or CMake toolchain file)"
		rm -rf "$NDK_PATH" 2>/dev/null || true
		return
	fi

	if ! ndk_clang_runs "$NDK_PATH"; then
		echo "  ⚠ Removing unusable NDK directory $NDK_NAME (no clang driver runs on this device)"
		rm -rf "$NDK_PATH" 2>/dev/null || true
		return
	fi

	if [ -f "$NDK_PATH/source.properties" ]; then
		return
	fi

	local NDK_REVISION="$NDK_NAME"
	local NDK_RELEASE="r${NDK_NAME%%.*}"
	{
		echo "Pkg.Desc = Android NDK"
		echo "Pkg.Revision = $NDK_REVISION"
		echo "Pkg.BaseRevision = $NDK_REVISION"
		echo "Pkg.ReleaseName = $NDK_RELEASE"
	} >"$NDK_PATH/source.properties"
	echo "  ✓ Created missing source.properties for NDK $NDK_NAME (required by AGP)"
}

# Helper function to make an NDK's clang toolchain usable on Termux for any
# repack layout or NDK version.
#
# Two independent problems are fixed here:
#
# 1. The generic `clang`/`clang++` names must resolve to the repack's real
#    versioned driver (clang-18, clang-21, ...). The aarch64 repack ships a
#    native, self-contained driver; older installs of this script overwrote the
#    generic names with a shell wrapper that re-exec'd Termux's clang with an
#    extra `-L"$SYSROOT/usr/lib/<triple>"`. That directory holds only the static
#    archives (libc.a, libm.a, ...); the shared stubs (libc.so, libm.so, ...)
#    live in the per-API subdirectories (21/, 22/, ...). Because the wrapper's -L
#    is searched before the API-level directory clang derives from --sysroot,
#    -lc resolved to the static libc.a and every native link failed:
#        ld.lld: error: undefined symbol: pthread_atfork
#        >>> ... in archive .../sysroot/usr/lib/aarch64-linux-android/libc.a
#    Projects with a native CMake plugin (jni, pulled in by
#    path_provider_android) then died at :jni:configureCMake* with
#    "The C compiler ... is broken" / "[CXX1429]" (issue #29).
#
# 2. The CMake toolchain picks its host directory from ANDROID_HOST_TAG, which
#    the NDK computes itself and which changed across repacks: the r29 tarball
#    sets `linux-aarch64` on Android, while the earlier aarch64 repack set
#    `linux-arm64`. The physical host directory may be named either, so every
#    candidate tag is aliased to it (prebuilt/bin and prebuilt/sysroot cover an
#    empty tag). Without this, the computed tag can name a directory that does
#    not exist and CMake reports the compiler as "not a full path to an existing
#    compiler tool".
setup_ndk_clang_wrappers() {
	local NDK_PATH="$1"
	local NDK_NAME=$(basename "$NDK_PATH")

	if [ ! -d "$NDK_PATH/toolchains/llvm" ]; then
		echo "    ⚠ Skipping $NDK_NAME (no toolchains/llvm directory)"
		return
	fi

	if ! ndk_clang_runs "$NDK_PATH"; then
		echo "    ⚠ Skipping $NDK_NAME (no clang driver runs on this device)"
		return
	fi

	local PREBUILT="$NDK_PATH/toolchains/llvm/prebuilt"

	# Locate the real host directory: the physical one that holds the versioned
	# clang-<N> driver (symlinked tag aliases are skipped).
	local REAL_HOST=""
	local dir candidates
	for dir in "$PREBUILT"/*; do
		[ -d "$dir" ] || continue
		[ -L "$dir" ] && continue
		candidates=("$dir"/bin/clang-[0-9]*)
		[ -x "${candidates[0]}" ] || continue
		REAL_HOST="$dir"
		break
	done
	if [ -z "$REAL_HOST" ]; then
		echo "    ⚠ Skipping $NDK_NAME (no clang-<version> driver found)"
		return
	fi
	local REAL_BIN="$REAL_HOST/bin"
	local REAL_NAME
	REAL_NAME=$(basename "$REAL_HOST")

	# The versioned driver inside the real host directory.
	local DRIVER=""
	local c
	for c in "$REAL_BIN"/clang-[0-9]*; do
		DRIVER=$(basename "$c")
		break
	done

	echo "    Configuring clang toolchain for NDK $NDK_NAME (using $DRIVER)..."

	# 1. Repair the generic driver names: they must resolve to the versioned
	#    driver, never to a shell wrapper (see note 1 above).
	local f cur
	for f in clang clang++; do
		if [ -L "$REAL_BIN/$f" ]; then
			cur=$(readlink "$REAL_BIN/$f")
		else
			cur=""
		fi
		if [ "$cur" = "$DRIVER" ]; then
			continue
		fi
		backup_ndk_file "$REAL_BIN/$f"
		unlink "$REAL_BIN/$f" 2>/dev/null || rm -f "$REAL_BIN/$f" 2>/dev/null || true
		ln -sfn "$DRIVER" "$REAL_BIN/$f"
	done

	# 2. Alias every candidate host tag to the real host directory, so whichever
	#    tag the CMake toolchain computes resolves to the full host tree
	#    (including sysroot). Leave an entry that already resolves correctly.
	local tag entry
	for tag in linux-aarch64 linux-arm64 linux-x86_64; do
		if [ "$tag" = "$REAL_NAME" ]; then
			continue
		fi
		entry="$PREBUILT/$tag"
		if [ -L "$entry" ] && [ "$(readlink "$entry")" = "$REAL_NAME" ]; then
			continue
		fi
		if [ -e "$entry" ] && [ ! -L "$entry" ]; then
			# A stale directory left by an older install. Keep it only if it
			# carries its own driver; otherwise replace it with the alias.
			candidates=("$entry"/bin/clang-[0-9]*)
			[ -x "${candidates[0]}" ] && continue
			rm -rf "$entry"
		fi
		ln -sfn "$REAL_NAME" "$entry"
	done

	# 3. Fallbacks for an empty ANDROID_HOST_TAG: the compiler is then resolved
	#    as prebuilt/bin/clang and the sysroot as prebuilt/sysroot.
	mkdir -p "$PREBUILT/bin"
	for f in clang clang++; do
		backup_ndk_file "$PREBUILT/bin/$f"
		unlink "$PREBUILT/bin/$f" 2>/dev/null || rm -f "$PREBUILT/bin/$f" 2>/dev/null || true
		ln -sfn "../$REAL_NAME/bin/$DRIVER" "$PREBUILT/bin/$f"
	done
	backup_ndk_file "$PREBUILT/sysroot"
	ln -sfn "$REAL_NAME/sysroot" "$PREBUILT/sysroot"

	# 4. Patch the toolchain CMake files (host-tag detection is handled above).
	local TOOLCHAIN="$NDK_PATH/build/cmake/android-legacy.toolchain.cmake"
	if [ -f "$TOOLCHAIN" ]; then
		backup_ndk_file "$TOOLCHAIN"
		if grep -q 'list(APPEND ANDROID_LINKER_FLAGS "-static-libstdc++")' "$TOOLCHAIN" 2>/dev/null; then
			sed -i 's/list(APPEND ANDROID_LINKER_FLAGS "-static-libstdc++")/# Disabled for Termux: list(APPEND ANDROID_LINKER_FLAGS "-static-libstdc++")/' "$TOOLCHAIN"
		fi
	fi
	local MAIN_TOOLCHAIN="$NDK_PATH/build/cmake/android.toolchain.cmake"
	if [ -f "$MAIN_TOOLCHAIN" ]; then
		backup_ndk_file "$MAIN_TOOLCHAIN"
	fi

	# 5. Replace x86_64 llvm-objcopy/llvm-strip with Termux ARM64 native binaries
	#    (Gradle StripDebugSymbolsRunnable fails with x86_64 binaries on ARM64).
	if [ -f "$PREFIX/bin/llvm-objcopy" ]; then
		backup_ndk_file "$REAL_BIN/llvm-objcopy"
		backup_ndk_file "$REAL_BIN/llvm-strip"
		cp "$PREFIX/bin/llvm-objcopy" "$REAL_BIN/llvm-objcopy" 2>/dev/null || true
		cp "$PREFIX/bin/llvm-strip" "$REAL_BIN/llvm-strip" 2>/dev/null || true
		echo "    ✓ llvm-objcopy/llvm-strip replaced with ARM64 native"
	fi

	echo "    ✓ NDK $NDK_NAME configured"
}

# --- Opt-in NDK provisioning -------------------------------------------------
# The apt payload ships without the NDK (~550 MB compressed / ~1.6 GB
# extracted), so `pkg install flutter` stays fast and native-plugin builds
# print a clear hint instead of a cryptic AGP error. Run:
#
#     bash $PREFIX/share/flutter/post_install.sh --provision-ndk
#
# to download, verify and install the pinned Termux-arm64 NDK repack, then
# wire up the clang wrappers and ndkVersion pins. Idempotent: a valid NDK is
# left untouched.
#
# The archive URL and SHA256 mirror scripts/install/versions_common.sh (the
# installer's source of truth); scripts/ci/check_repo.py
# (check_ndk_constants_drift) fails CI when the two copies diverge.
NDK_R30_SHA256="aa443113433cf8a3a4a97524a94041972d1ccbb72897adc4aa48c13ae627509d"

ndk_archive_sha256() {
	case "$1" in
	30) echo "$NDK_R30_SHA256" ;;
	*) echo "" ;;
	esac
}

provision_ndk() {
	local NDK_TARGET="$ANDROID_SDK/ndk/$NDK_VERSION"
	local NDK_MAJOR="${NDK_VERSION%%.*}"
	local NDK_URL="${TERMUX_NDK_URL:-https://github.com/HomuHomu833/android-ndk-custom/releases/download/r$NDK_MAJOR/android-ndk-r$NDK_MAJOR-aarch64-linux-android.tar.xz}"
	local NDK_SHA256="${TERMUX_NDK_SHA256:-$(ndk_archive_sha256 "$NDK_MAJOR")}"

	if [ -d "$NDK_TARGET" ] && ndk_clang_runs "$NDK_TARGET"; then
		echo "=== NDK $NDK_VERSION already provisioned at $NDK_TARGET ==="
		return 0
	fi

	if [ -z "$NDK_SHA256" ]; then
		echo "Error: no pinned SHA256 for NDK major version r$NDK_MAJOR." >&2
		echo "Set TERMUX_NDK_URL and TERMUX_NDK_SHA256 to provision it explicitly." >&2
		return 1
	fi

	mkdir -p "$ANDROID_SDK/ndk"

	# The tarball, its staging copy and the extracted tree all live on the
	# target filesystem: ~550 MB download + ~1.6 GB extracted while staged.
	local avail_kb
	avail_kb=$(df -Pk "$ANDROID_SDK/ndk" | awk 'NR==2 {print $4}')
	if [ -z "$avail_kb" ] || [ "$avail_kb" -lt 2700000 ]; then
		echo "Error: need ~2.6 GB free under $ANDROID_SDK to stage the NDK (have ${avail_kb:-unknown} KB)." >&2
		return 1
	fi

	local stage tarball
	stage="$ANDROID_SDK/ndk/.ndk-stage.$$"
	tarball="$stage/android-ndk-r$NDK_MAJOR-aarch64.tar.xz"
	rm -rf "$stage"
	mkdir -p "$stage"

	echo "=== Downloading Android NDK r$NDK_MAJOR (aarch64 repack, ~550 MB) ==="
	if ! curl -fL --progress-bar -o "$tarball" "$NDK_URL"; then
		echo "Error: NDK download failed ($NDK_URL)." >&2
		rm -rf "$stage"
		return 1
	fi

	echo "=== Verifying NDK SHA256 ==="
	local actual_sha
	actual_sha=$(sha256sum "$tarball" | awk '{print $1}')
	if [ "$actual_sha" != "$NDK_SHA256" ]; then
		echo "Error: NDK archive SHA256 mismatch (expected $NDK_SHA256, got $actual_sha)." >&2
		rm -rf "$stage"
		return 1
	fi

	echo "=== Extracting NDK (staging isolation) ==="
	if ! tar -xf "$tarball" -C "$stage"; then
		echo "Error: NDK extraction failed." >&2
		rm -rf "$stage"
		return 1
	fi
	rm -f "$tarball"

	local extracted="" cand
	for cand in "android-ndk-r$NDK_MAJOR-aarch64-linux-android" "android-ndk-r$NDK_MAJOR" "android-ndk-r$NDK_MAJOR-aarch64"; do
		if [ -d "$stage/$cand" ]; then
			extracted="$stage/$cand"
			break
		fi
	done
	if [ -z "$extracted" ]; then
		extracted=$(find "$stage" -mindepth 1 -maxdepth 1 -type d | head -n 1)
	fi
	if [ -z "$extracted" ] || [ ! -d "$extracted" ]; then
		echo "Error: extracted NDK directory not found in $stage." >&2
		rm -rf "$stage"
		return 1
	fi

	rm -rf "$NDK_TARGET"
	if ! mv "$extracted" "$NDK_TARGET"; then
		echo "Error: failed to move NDK into $NDK_TARGET." >&2
		rm -rf "$stage"
		return 1
	fi
	rm -rf "$stage"

	# Validate like the normal install path: repair (or refuse) and wire up
	# the Termux clang wrappers + host-tag aliases AGP/CMake rely on.
	repair_ndk_metadata "$NDK_TARGET"
	if [ ! -d "$NDK_TARGET" ]; then
		echo "Error: extracted NDK failed validation (no runnable clang for this device)." >&2
		return 1
	fi
	setup_ndk_clang_wrappers "$NDK_TARGET"

	# Keep generated projects pinning the version actually installed.
	if [ -f "$FLUTTER_ROOT/packages/flutter_tools/gradle/src/main/kotlin/FlutterExtension.kt" ]; then
		patch_ndk_version "$FLUTTER_ROOT/packages/flutter_tools/gradle/src/main/kotlin/FlutterExtension.kt"
	fi
	if [ -f "$FLUTTER_ROOT/packages/flutter_tools/lib/src/android/gradle_utils.dart" ]; then
		patch_ndk_version_dart "$FLUTTER_ROOT/packages/flutter_tools/lib/src/android/gradle_utils.dart"
	fi

	echo "=== NDK $NDK_VERSION provisioned; native-plugin builds are ready ==="
}

# Handle library mode (for unit testing / function sourcing)
if [ "$MODE" == "lib" ]; then
	return 0 2>/dev/null || exit 0
fi

# Handle read-only modes (--status, --check) and rollback mode (--rollback) immediately
if [ "$MODE" == "status" ] || [ "$MODE" == "check" ]; then
	echo "=== Post-install read-only report ($MODE) ==="
	apply_patches
	echo "=== Post-install read-only report finished ==="
	exit 0
fi

if [ "$MODE" == "rollback" ]; then
	echo "=== Rolling back post-install patches ==="
	rollback_patches
	echo "=== Rollback finished ==="
	exit 0
fi

# Opt-in NDK provisioning (--provision-ndk): download + install only, no
# patching of the SDK tree beyond the NDK's own wrappers and version pins.
if [ "$MODE" == "provision-ndk" ]; then
	provision_ndk
	exit $?
fi

# Run apply_patches for --apply
apply_patches

# 1. Fix engine.stamp and engine.realm (required for Maven artifact resolution)
echo "[1/17] Fixing engine.stamp and engine.realm, and injecting framework version tag..."
mkdir -p "$FLUTTER_ROOT/bin/cache"
# Fail closed: never inject a stale engine revision fallback; the revision must
# come from the shipped SDK, not a hardcoded constant.
if [ ! -s "$FLUTTER_ROOT/bin/internal/engine.version" ]; then
	echo "Error: $FLUTTER_ROOT/bin/internal/engine.version missing or empty; cannot determine engine revision" >&2
	exit 1
fi
local_eng_ver="$(cat "$FLUTTER_ROOT/bin/internal/engine.version" 2>/dev/null | tr -d '\n\r')"
echo -n "$local_eng_ver" >"$FLUTTER_ROOT/bin/cache/engine.stamp" 2>/dev/null || true
echo -n "$local_eng_ver" >"$FLUTTER_ROOT/bin/cache/engine_stamp.stamp" 2>/dev/null || true
echo -n >"$FLUTTER_ROOT/bin/cache/engine.realm" 2>/dev/null || true

# Patch update_engine_version.sh to reliably read bin/internal/engine.version offline
if [ -f "$FLUTTER_ROOT/bin/internal/update_engine_version.sh" ]; then
	sed -i 's/elif.*ls-files.*engine.version.*/elif [ -f "$FLUTTER_ROOT\/bin\/internal\/engine.version" ]; then/' "$FLUTTER_ROOT/bin/internal/update_engine_version.sh" 2>/dev/null || true
fi

mkdir -p "$FLUTTER_ROOT/bin/cache/artifacts/material_fonts"
mkdir -p "$FLUTTER_ROOT/bin/cache/artifacts/gradle_wrapper/gradle/wrapper"

# Populate real gradle wrapper if missing or 0 bytes
if [ ! -s "$FLUTTER_ROOT/bin/cache/artifacts/gradle_wrapper/gradle/wrapper/gradle-wrapper.jar" ]; then
	echo "  Downloading Gradle Wrapper..."
	WRAPPER_REL_PATH=""
	if [ -f "$FLUTTER_ROOT/bin/internal/gradle_wrapper.version" ]; then
		WRAPPER_REL_PATH="$(cat "$FLUTTER_ROOT/bin/internal/gradle_wrapper.version" | tr -d '\n\r')"
	fi
	if [ -n "$WRAPPER_REL_PATH" ]; then
		cd "${TMPDIR:-$PREFIX/tmp}"
		(
			set +e
			curl -s -L "https://storage.googleapis.com/$WRAPPER_REL_PATH" | tar -xz -C "$FLUTTER_ROOT/bin/cache/artifacts/gradle_wrapper" 2>/dev/null
		) || true
		rm -f "$FLUTTER_ROOT/bin/cache/artifacts/gradle_wrapper/gradle/wrapper/gradle-wrapper.properties" 2>/dev/null || true
		rm -f "$FLUTTER_ROOT/bin/cache/artifacts/gradle_wrapper/NOTICE" 2>/dev/null || true
	fi
fi
if [ -f "$FLUTTER_ROOT/bin/cache/artifacts/gradle_wrapper/gradlew" ]; then
	sed -i "1s|.*|#!${PREFIX:-/data/data/com.termux/files/usr}/bin/bash|" "$FLUTTER_ROOT/bin/cache/artifacts/gradle_wrapper/gradlew" 2>/dev/null || true
	chmod 755 "$FLUTTER_ROOT/bin/cache/artifacts/gradle_wrapper/gradlew" 2>/dev/null || true
else
	touch "$FLUTTER_ROOT/bin/cache/artifacts/gradle_wrapper/gradlew" "$FLUTTER_ROOT/bin/cache/artifacts/gradle_wrapper/gradlew.bat" "$FLUTTER_ROOT/bin/cache/artifacts/gradle_wrapper/gradle/wrapper/gradle-wrapper.jar" 2>/dev/null || true
	sed -i "1s|.*|#!${PREFIX:-/data/data/com.termux/files/usr}/bin/bash|" "$FLUTTER_ROOT/bin/cache/artifacts/gradle_wrapper/gradlew" 2>/dev/null || true
	chmod 755 "$FLUTTER_ROOT/bin/cache/artifacts/gradle_wrapper/gradlew" 2>/dev/null || true
fi

# Populate real material fonts if missing or empty
if [ ! -d "$FLUTTER_ROOT/bin/cache/artifacts/material_fonts" ] || [ -z "$(ls -A "$FLUTTER_ROOT/bin/cache/artifacts/material_fonts" 2>/dev/null)" ]; then
	echo "  Downloading Material Fonts..."
	FONTS_REL_PATH=""
	if [ -f "$FLUTTER_ROOT/bin/internal/material_fonts.version" ]; then
		FONTS_REL_PATH="$(cat "$FLUTTER_ROOT/bin/internal/material_fonts.version" | tr -d '\n\r')"
	fi
	if [ -n "$FONTS_REL_PATH" ]; then
		cd "${TMPDIR:-$PREFIX/tmp}"
		(
			set +e
			curl -s -L "https://storage.googleapis.com/$FONTS_REL_PATH" -o fonts.zip 2>/dev/null && unzip -q -o fonts.zip -d "$FLUTTER_ROOT/bin/cache/artifacts/material_fonts" 2>/dev/null && rm -f fonts.zip
		) || true
	fi
fi

for vfile in "$FLUTTER_ROOT"/bin/internal/*.version; do
	[ -f "$vfile" ] || continue
	vname=$(basename "$vfile" .version)
	cp "$vfile" "$FLUTTER_ROOT/bin/cache/${vname}.stamp" 2>/dev/null || true
done
cat >"$FLUTTER_ROOT/bin/cache/engine_stamp.json" <<EOF
{
  "build_time_ms": 1770000000000,
  "git_revision": "$local_eng_ver",
  "git_revision_date": "2026-06-09T00:00:00Z",
  "content_hash": "$local_eng_ver"
}
EOF
echo "  ✓ engine.stamp=$(cat "$FLUTTER_ROOT/bin/cache/engine.stamp" 2>/dev/null || echo 'unknown')"
echo "  ✓ engine.realm cleared"

GIT_BIN="${PREFIX:-/data/data/com.termux/files/usr}/bin/git"
if ! [ -x "$GIT_BIN" ] && ! command -v "$GIT_BIN" >/dev/null 2>&1; then
	GIT_BIN="git"
fi

# Load canonical metadata from packaged manifest or embedded source-of-truth constants
CANONICAL_FLUTTER_VER="3.47.8"
CANONICAL_FRAMEWORK_REV="737ba8f885dc774b53b442759999a312c9461e4e"
CANONICAL_FRAMEWORK_DATE="2026-10-22 21:37:31 -0700"
CANONICAL_ENGINE_REV="$local_eng_ver"
CANONICAL_DART_VER="3.13.7"
CANONICAL_DEVTOOLS_VER="2.60.0"
CANONICAL_CHANNEL="stable"
CANONICAL_REPO_URL="https://github.com/flutter/flutter.git"

MANIFEST_LOADED=0
MANIFEST_FILE="$PREFIX/share/flutter/manifest.json"
if [ ! -f "$MANIFEST_FILE" ] && [ -f "$FLUTTER_ROOT/bin/cache/canonical_manifest.json" ]; then
	MANIFEST_FILE="$FLUTTER_ROOT/bin/cache/canonical_manifest.json"
fi

if [ -f "$MANIFEST_FILE" ]; then
	m_ver=$(grep -o '"flutter_version": *"[^"]*"' "$MANIFEST_FILE" 2>/dev/null | cut -d'"' -f4 || echo "")
	m_rev=$(grep -o '"framework_revision": *"[^"]*"' "$MANIFEST_FILE" 2>/dev/null | cut -d'"' -f4 || echo "")
	m_date=$(grep -o '"framework_commit_date": *"[^"]*"' "$MANIFEST_FILE" 2>/dev/null | cut -d'"' -f4 || echo "")
	m_eng=$(grep -o '"engine_revision": *"[^"]*"' "$MANIFEST_FILE" 2>/dev/null | cut -d'"' -f4 || echo "")
	m_dart=$(grep -o '"dart_version": *"[^"]*"' "$MANIFEST_FILE" 2>/dev/null | cut -d'"' -f4 || echo "")
	m_dev=$(grep -o '"devtools_version": *"[^"]*"' "$MANIFEST_FILE" 2>/dev/null | cut -d'"' -f4 || echo "")
	if [ -n "$m_ver" ]; then
		CANONICAL_FLUTTER_VER="$m_ver"
		MANIFEST_LOADED=1
	fi
	[ -n "$m_rev" ] && CANONICAL_FRAMEWORK_REV="$m_rev"
	[ -n "$m_date" ] && CANONICAL_FRAMEWORK_DATE="$m_date"
	[ -n "$m_eng" ] && CANONICAL_ENGINE_REV="$m_eng"
	[ -n "$m_dart" ] && CANONICAL_DART_VER="$m_dart"
	[ -n "$m_dev" ] && CANONICAL_DEVTOOLS_VER="$m_dev"
fi

# Fallback ONLY when package manifest is NOT present (e.g. legacy installation without manifest)
if [ "$MANIFEST_LOADED" -eq 0 ]; then
	if [ -f "$FLUTTER_ROOT/version" ]; then
		fixture_ver="$(cat "$FLUTTER_ROOT/version" | tr -d '\n\r')"
		if [ -n "$fixture_ver" ] && echo "$fixture_ver" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+'; then
			CANONICAL_FLUTTER_VER="$fixture_ver"
		fi
	elif [ -f "$FLUTTER_ROOT/.git/termux_synthetic" ]; then
		saved_synth_ver=$(grep -o '"version": *"[^"]*"' "$FLUTTER_ROOT/.git/termux_synthetic" 2>/dev/null | cut -d'"' -f4 || echo "")
		if [ -n "$saved_synth_ver" ] && echo "$saved_synth_ver" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+'; then
			CANONICAL_FLUTTER_VER="$saved_synth_ver"
		fi
	fi
fi

# Fail-closed change directory
cd "$FLUTTER_ROOT" || {
	echo "Error: Failed to cd to $FLUTTER_ROOT" >&2
	exit 1
}

# Helper to identify if .git was created as a synthetic termux repository
is_synthetic_repo() {
	local target_dir="$1"
	local git_dir="$target_dir/.git"
	[ -d "$git_dir" ] || return 1
	if [ -f "$git_dir/termux_synthetic" ]; then
		return 0
	fi
	local commit_count
	commit_count=$("$GIT_BIN" --git-dir="$git_dir" rev-list --count HEAD 2>/dev/null || echo "999")
	local first_msg
	first_msg=$("$GIT_BIN" --git-dir="$git_dir" log -1 --pretty=%B 2>/dev/null || echo "")
	if [ "$commit_count" -le 2 ] && [ "$first_msg" = "Init framework" ]; then
		return 0
	fi
	return 1
}

# Synthetic repo setup & contamination repair
if ! [ -d "$FLUTTER_ROOT/.git" ]; then
	echo "  ! Missing .git, creating synthetic repository on stable branch for version resolution..."
	rm -f version
	"$GIT_BIN" init -q -b stable >/dev/null 2>&1 || "$GIT_BIN" init -q >/dev/null 2>&1
	"$GIT_BIN" symbolic-ref HEAD refs/heads/stable >/dev/null 2>&1 || true
	"$GIT_BIN" config user.email "termux@example.com"
	"$GIT_BIN" config user.name "termux"
	"$GIT_BIN" add -f bin/flutter bin/internal/engine.version bin/internal/*.version >/dev/null 2>&1 || true
	"$GIT_BIN" commit -q -m "Init framework" >/dev/null 2>&1 || true
	"$GIT_BIN" branch -M stable >/dev/null 2>&1 || true
	"$GIT_BIN" tag -f "$CANONICAL_FLUTTER_VER" HEAD >/dev/null 2>&1
	echo "{\"synthetic\":true,\"package\":\"termux-flutter-wsl\",\"version\":\"$CANONICAL_FLUTTER_VER\"}" >.git/termux_synthetic
	rm -f .git/FETCH_HEAD
	echo "  ✓ Synthetic tag $CANONICAL_FLUTTER_VER created on stable branch"
elif is_synthetic_repo "$FLUTTER_ROOT"; then
	echo "  ! Verifying and repairing synthetic repository state..."
	rm -f version
	# Normalize branch to stable if needed (handles master, main, trunk, develop, custom-name, detached HEAD)
	curr_br="$("$GIT_BIN" symbolic-ref --short HEAD 2>/dev/null || echo "")"
	if [ "$curr_br" != "stable" ]; then
		"$GIT_BIN" checkout -B stable >/dev/null 2>&1 || "$GIT_BIN" branch -M stable >/dev/null 2>&1 || "$GIT_BIN" symbolic-ref HEAD refs/heads/stable >/dev/null 2>&1
	fi

	# Purge any imported tag contamination (e.g. 1100+ upstream tags fetched)
	existing_tags=$("$GIT_BIN" tag -l 2>/dev/null || echo "")
	for t in $existing_tags; do
		if [ "$t" != "$CANONICAL_FLUTTER_VER" ]; then
			"$GIT_BIN" tag -d "$t" >/dev/null 2>&1 || true
		fi
	done
	head_tag=$("$GIT_BIN" tag --points-at HEAD 2>/dev/null || echo "")
	if ! echo "$head_tag" | grep -qx "$CANONICAL_FLUTTER_VER"; then
		"$GIT_BIN" tag -f "$CANONICAL_FLUTTER_VER" HEAD >/dev/null 2>&1
	fi
	rm -f .git/FETCH_HEAD
	echo "{\"synthetic\":true,\"package\":\"termux-flutter-wsl\",\"version\":\"$CANONICAL_FLUTTER_VER\"}" >.git/termux_synthetic
	echo "  ✓ Synthetic repository sanitized: branch=stable, tag=$CANONICAL_FLUTTER_VER (contamination purged)"
else
	# Non-synthetic / real user or upstream checkout: guardrail to protect user work
	echo "  ℹ Non-synthetic / user-owned Git checkout detected at $FLUTTER_ROOT; preserving git history and refs"
	if [ -n "$("$GIT_BIN" status --porcelain 2>/dev/null)" ]; then
		echo "    (Note: working tree contains modifications)"
	fi
fi

# Fail-closed postcondition verification for synthetic repos
if is_synthetic_repo "$FLUTTER_ROOT"; then
	verified_branch="$("$GIT_BIN" symbolic-ref --short HEAD 2>/dev/null || echo "unknown")"
	if [ "$verified_branch" != "stable" ]; then
		echo "Error: Synthetic repository branch verification failed: expected 'stable', got '$verified_branch'" >&2
		exit 1
	fi
	tags_at_head="$("$GIT_BIN" tag --points-at HEAD 2>/dev/null || echo "")"
	if ! echo "$tags_at_head" | grep -qx "$CANONICAL_FLUTTER_VER"; then
		echo "Error: Synthetic repository tag verification failed: '$CANONICAL_FLUTTER_VER' not pointing at HEAD" >&2
		exit 1
	fi
	tag_count="$("$GIT_BIN" tag -l 2>/dev/null | wc -l)"
	if [ "$tag_count" -ne 1 ]; then
		echo "Error: Synthetic repository tag count verification failed: expected 1 tag, found $tag_count" >&2
		exit 1
	fi
fi

# Determine and verify semantic Dart SDK version (never engine cache stamp)
EFFECTIVE_DART_VER=""
if [ -x "$DART_SDK/bin/dart" ]; then
	EFFECTIVE_DART_VER="$("$DART_SDK/bin/dart" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || echo "")"
elif command -v dart >/dev/null 2>&1; then
	EFFECTIVE_DART_VER="$(dart --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || echo "")"
fi

if [ -n "$EFFECTIVE_DART_VER" ]; then
	if [ "$EFFECTIVE_DART_VER" != "$CANONICAL_DART_VER" ]; then
		echo "Error: Installed Dart SDK version mismatch: expected '$CANONICAL_DART_VER', got '$EFFECTIVE_DART_VER'" >&2
		exit 1
	fi
else
	# Fallback to canonical when Dart binary is not executable in minimal mock fixtures
	EFFECTIVE_DART_VER="$CANONICAL_DART_VER"
fi

# Atomic generation and validation of canonical flutter.version.json
TMP_VER_JSON="$FLUTTER_ROOT/bin/cache/flutter.version.json.tmp.$$"
cat >"$TMP_VER_JSON" <<EOF
{
  "frameworkVersion": "$CANONICAL_FLUTTER_VER",
  "channel": "$CANONICAL_CHANNEL",
  "repositoryUrl": "$CANONICAL_REPO_URL",
  "frameworkRevision": "$CANONICAL_FRAMEWORK_REV",
  "frameworkCommitDate": "$CANONICAL_FRAMEWORK_DATE",
  "engineRevision": "$CANONICAL_ENGINE_REV",
  "dartSdkVersion": "$EFFECTIVE_DART_VER",
  "devToolsVersion": "$CANONICAL_DEVTOOLS_VER",
  "flutterVersion": "$CANONICAL_FLUTTER_VER"
}
EOF

# Strict validation
if ! grep -q "\"frameworkVersion\": \"$CANONICAL_FLUTTER_VER\"" "$TMP_VER_JSON" ||
	! grep -q "\"channel\": \"stable\"" "$TMP_VER_JSON" ||
	! grep -q "\"repositoryUrl\": \"https://github.com/flutter/flutter.git\"" "$TMP_VER_JSON" ||
	! grep -q "\"frameworkRevision\": \"$CANONICAL_FRAMEWORK_REV\"" "$TMP_VER_JSON" ||
	! grep -q "\"engineRevision\": \"$CANONICAL_ENGINE_REV\"" "$TMP_VER_JSON" ||
	! grep -q "\"dartSdkVersion\": \"$EFFECTIVE_DART_VER\"" "$TMP_VER_JSON" ||
	! grep -q "\"devToolsVersion\": \"$CANONICAL_DEVTOOLS_VER\"" "$TMP_VER_JSON" ||
	! grep -q "\"flutterVersion\": \"$CANONICAL_FLUTTER_VER\"" "$TMP_VER_JSON"; then
	echo "Error: Generated flutter.version.json failed canonical validation" >&2
	rm -f "$TMP_VER_JSON"
	exit 1
fi

mv -f "$TMP_VER_JSON" "$FLUTTER_ROOT/bin/cache/flutter.version.json"
chmod 644 "$FLUTTER_ROOT/bin/cache/flutter.version.json"
echo "  ✓ Canonical flutter.version.json generated ($CANONICAL_FLUTTER_VER stable, framework=$CANONICAL_FRAMEWORK_REV)"

# Get engine version for downloads (fail closed; no stale fallback)
ENGINE_VERSION=$(cat "$FLUTTER_ROOT/bin/internal/engine.version" 2>/dev/null || true)
if [ -z "$ENGINE_VERSION" ]; then
	echo "Error: engine revision unavailable ($FLUTTER_ROOT/bin/internal/engine.version); cannot download Dart SDK snapshots" >&2
	exit 1
fi

# 2. Download official Dart SDK snapshots (fixes flutter run hot reload)
echo "[2/17] Downloading official Dart SDK snapshots (for hot reload)..."
SNAPSHOTS_URL="https://storage.googleapis.com/flutter_infra_release/flutter/${ENGINE_VERSION}/dart-sdk-linux-arm64.zip"
SNAPSHOTS_DIR=$DART_SDK/bin/snapshots

# Check if key snapshot is missing
if [ ! -f "$SNAPSHOTS_DIR/dds_aot.dart.snapshot" ]; then
	echo "  Downloading dart-sdk-linux-arm64.zip..."
	cd "${TMPDIR:-$PREFIX/tmp}"
	(
		set +e
		curl -L -o dart-sdk.zip "$SNAPSHOTS_URL" >/dev/null 2>&1
	) || true
	if [ -f dart-sdk.zip ]; then
		echo "  Extracting snapshots..."
		# Claim success only when the archive actually unpacked the key snapshot;
		# on a download or unzip failure the snapshots bundled in the deb are used.
		if unzip -o -j dart-sdk.zip 'dart-sdk/bin/snapshots/*' -d "$SNAPSHOTS_DIR" 2>/dev/null \
			&& [ -f "$SNAPSHOTS_DIR/dds_aot.dart.snapshot" ]; then
			echo "  ✓ Dart SDK snapshots installed"
		else
			echo "  ⚠ Dart SDK snapshots unavailable (using the snapshots bundled in the deb)"
		fi
		rm -f dart-sdk.zip
	else
		echo "  ⚠ Network unavailable, skipping Dart SDK snapshots download"
	fi

	# Create symlinks for non-AOT versions
	ln -sf frontend_server_aot.dart.snapshot "$SNAPSHOTS_DIR/frontend_server.dart.snapshot" 2>/dev/null || true
else
	echo "  ✓ Dart SDK snapshots already exist"
fi

# 3. Clean DT_RPATH from ELF binaries (fixes flutter run crash)
echo "[3/17] Cleaning ELF binaries (fix flutter run)..."
if ! command -v termux-elf-cleaner &>/dev/null; then
	(
		set +e
		pkg install -y termux-elf-cleaner >/dev/null 2>&1
	) || true
fi

# Clean dart binaries to remove DT_RPATH warnings that crash flutter run
if command -v termux-elf-cleaner &>/dev/null; then
	echo "  Cleaning dart-sdk binaries..."
	find $DART_SDK/bin -type f -executable 2>/dev/null | xargs -r termux-elf-cleaner 2>/dev/null || true

	echo "  Cleaning engine artifacts..."
	find $FLUTTER_ROOT/bin/cache/artifacts/engine -name "*.so" -o -name "gen_snapshot" -o -name "dart" 2>/dev/null | xargs -r termux-elf-cleaner 2>/dev/null || true

	echo "  ✓ ELF binaries cleaned"
else
	echo "  ⚠ termux-elf-cleaner not found, skipping"
fi

# 4. Install Android SDK Platform for the configured COMPILE_SDK
platform_zip_url() {
	case "$1" in
	36) printf '%s\n' 'https://dl.google.com/android/repository/platform-36_r01.zip' ;;
	35) printf '%s\n' 'https://dl.google.com/android/repository/platform-35_r01.zip' ;;
	34) printf '%s\n' 'https://dl.google.com/android/repository/platform-34-ext7_r02.zip' ;;
	*) return 1 ;;
	esac
}

echo "[4/17] Installing Android SDK Platform $COMPILE_SDK..."
if [ ! -d "$ANDROID_SDK/platforms/android-$COMPILE_SDK" ]; then
	mkdir -p $ANDROID_SDK/platforms
	cd $ANDROID_SDK/platforms
	PLATFORM_URL="$(platform_zip_url "$COMPILE_SDK" || true)"
	if [ -n "$PLATFORM_URL" ]; then
		(
			set +e
			curl -L -o platform-$COMPILE_SDK.zip "$PLATFORM_URL" >/dev/null 2>&1
		) || true
		if [ -f platform-$COMPILE_SDK.zip ] && [ -s platform-$COMPILE_SDK.zip ]; then
			unzip -q platform-$COMPILE_SDK.zip 2>/dev/null || true
			rm -f platform-$COMPILE_SDK.zip
			echo "  ✓ Platform $COMPILE_SDK installed"
		else
			echo "  ⚠ Download skipped/unavailable for Platform $COMPILE_SDK"
		fi
	fi
	# Ensure no fake symlinks remain
	if [ -L "$ANDROID_SDK/platforms/android-$COMPILE_SDK" ]; then
		rm -f "$ANDROID_SDK/platforms/android-$COMPILE_SDK"
	fi
else
	echo "  ✓ Platform $COMPILE_SDK already exists"
fi

# 5. Install required Termux build dependencies
echo "[5/17] Checking and installing Termux build dependencies..."

if ! command -v aapt2 &>/dev/null; then
	echo "  ! Termux aapt2 not found. Installing build dependencies via apt..."
	apt update >/dev/null 2>&1 || true
	apt install -y aapt2 libc++ libexpat openssl >/dev/null 2>&1 || true
fi

# Hold aapt2: it is ABI-sensitive and `apt upgrade` can silently swap in a build
# that breaks APK linking. Opt out with TERMUX_NO_HOLD_AAPT2=1.
if [ "${TERMUX_NO_HOLD_AAPT2:-false}" != "true" ] && command -v apt-mark &>/dev/null && command -v aapt2 &>/dev/null; then
	apt-mark hold aapt2 >/dev/null 2>&1 || true
	echo "  ✓ aapt2 held (release with: apt-mark unhold aapt2)"
fi

# Install d8/aidl/apksigner (required by AGP for build-tools validation)
for tool in d8 dx aidl apksigner zipalign; do
	if ! command -v $tool &>/dev/null; then
		echo "  ! $tool not found, installing..."
		apt install -y $tool >/dev/null 2>&1 || true
	fi
done

# 6. Generate package_config.json for flutter_tools
# The flutter CLI runs flutter_tools.dart in JIT mode (see shared.sh line ~200)
# and requires .dart_tool/package_config.json from pub get.
echo "[6/17] Generating flutter_tools package_config.json..."
FLUTTER_TOOLS_DIR=$FLUTTER_ROOT/packages/flutter_tools
PKG_CONFIG=$FLUTTER_TOOLS_DIR/.dart_tool/package_config.json

# Rewrite WSL build machine paths in prebuilt package_config.json to local FLUTTER_ROOT
if [ -f "$PKG_CONFIG" ]; then
	echo "  Rewriting package_config.json paths to $FLUTTER_ROOT..."
	sed -i "s|file://.*/flutter/|file://$FLUTTER_ROOT/|g" "$PKG_CONFIG" 2>/dev/null || true
	echo "  ✓ package_config.json paths updated"
fi

if [ ! -f "$PKG_CONFIG" ]; then
	echo "  Running pub get for flutter_tools..."
	cd "$FLUTTER_TOOLS_DIR"
	$DART_SDK/bin/dart pub get --offline --suppress-analytics >/dev/null 2>&1 || (
		set +e
		$DART_SDK/bin/dart pub get --suppress-analytics >/dev/null 2>&1
	) || true
	if [ -f "$PKG_CONFIG" ]; then
		echo "  ✓ package_config.json generated"
	else
		echo "  ⚠ Offline environment: package_config.json generation deferred"
	fi
fi

# (removed; former step 2) Android API 34 aapt2 workaround: compileSdk now follows COMPILE_SDK
#    (see step 4), so the forced android-34 platform download is no longer needed.

# 7. Clear stale Gradle included-build outputs after changing the Flutter Gradle plugin.
# Without this, upgrades can compile FlutterPlugin.kt against an older cached
# FlutterPluginConstants.kt and fail with unresolved PLATFORM_ABI_LIST.
echo "[7/17] Clearing Flutter Gradle plugin build cache..."
rm -rf "$FLUTTER_ROOT/packages/flutter_tools/gradle/.gradle" \
	"$FLUTTER_ROOT/packages/flutter_tools/gradle/build" \
	"$FLUTTER_ROOT/packages/flutter_tools/gradle/bin" 2>/dev/null || true
echo "  ✓ Flutter Gradle plugin cache cleared"

# 8. Configure the NDK clang toolchain (handles all installed NDK versions)
echo "[8/17] Configuring NDK clang toolchain..."

NDK_DIR="$ANDROID_SDK/ndk"
if [ -d "$NDK_DIR" ]; then
	NDK_COUNT=0
	for ndk_path in "$NDK_DIR"/*; do
		if [ -d "$ndk_path" ]; then
			repair_ndk_metadata "$ndk_path"
			# repair_ndk_metadata may remove an unusable directory
			if [ ! -d "$ndk_path" ]; then
				continue
			fi
			setup_ndk_clang_wrappers "$ndk_path"
			NDK_COUNT=$((NDK_COUNT + 1))
		fi
	done
	if [ $NDK_COUNT -eq 0 ]; then
		echo "  ⚠ No NDK found. Clang wrappers will be created when NDK is installed."
		echo "    Provision the pinned Termux NDK: bash $PREFIX/share/flutter/post_install.sh --provision-ndk"
	else
		echo "  ✓ $NDK_COUNT NDK(s) configured"
	fi
else
	echo "  ⚠ NDK directory not found. Clang wrappers will be created when NDK is installed."
	echo "    Provision the pinned Termux NDK: bash $PREFIX/share/flutter/post_install.sh --provision-ndk"
fi

# Repair the Android SDK's own CMake/Ninja packages.
#
# Google ships only x86_64 Linux host tools, so every binary the SDK carries in
# cmake/<v>/bin is an ELF that cannot run on this aarch64 device: execve() fails
# with ENOEXEC, the shell re-parses the bytes, and Gradle's CMake step dies with
#     .../cmake/<v>/bin/ninja: 1: ELF...: not found
# AGP downloads that package whenever it wants 3.22.1 and the directory is
# missing, so an empty directory is not enough either - it has to contain a
# runnable cmake. Every installed package's host tools are repointed at the
# Termux-native ones. source.properties
# is left untouched so AGP keeps recognising the package it finds.
setup_sdk_cmake() {
	local SDK_CMAKE="$ANDROID_SDK/cmake"
	local pkg f tool replaced=0
	if [ -d "$SDK_CMAKE" ]; then
		for pkg in "$SDK_CMAKE"/*; do
			[ -d "$pkg/bin" ] || continue
			for f in cmake ninja cpack ctest; do
				tool="$pkg/bin/$f"
				if [ -x "$tool" ] && "$tool" --version >/dev/null 2>&1; then
					continue
				fi
				[ -x "$PREFIX/bin/$f" ] || continue
				backup_ndk_file "$tool"
				rm -f "$tool" 2>/dev/null || true
				ln -sfn "$PREFIX/bin/$f" "$tool"
				echo "    ✓ $(basename "$pkg")/bin/$f -> Termux native"
				replaced=1
			done
		done
	fi
	# AGP asks for cmake 3.22.1 by default (jni, path_provider_android, ...).
	# Without the package already on disk it downloads Google's x86_64 build
	# again, so pre-create it from the Termux tools instead.
	if [ ! -d "$SDK_CMAKE/3.22.1" ]; then
		mkdir -p "$SDK_CMAKE/3.22.1/bin"
		ln -sfn "$PREFIX/bin/cmake" "$SDK_CMAKE/3.22.1/bin/cmake"
		ln -sfn "$PREFIX/bin/ninja" "$SDK_CMAKE/3.22.1/bin/ninja"
		echo "    ✓ cmake/3.22.1 provisioned from Termux tools"
		replaced=1
	fi
	if [ "$replaced" = 1 ]; then
		echo "  ✓ Android SDK CMake/Ninja usable"
	fi
}

echo "  Repairing Android SDK CMake/Ninja..."
setup_sdk_cmake

# Helper function to setup build-tools symlinks for any version
setup_build_tools_symlinks() {
	local BUILD_TOOLS="$1"
	local BT_NAME=$(basename "$BUILD_TOOLS")

	mkdir -p "$BUILD_TOOLS/lib"

	# Basic tools
	for tool in aapt aapt2 apksigner d8 dx zipalign aidl; do
		if [ "$tool" = "aapt2" ] && [ -L "$BUILD_TOOLS/aapt2" ] && readlink "$BUILD_TOOLS/aapt2" | grep -q "Android/Sdk"; then
			echo "    ✓ Retaining Mode B custom static aapt2 symlink"
		else
			ln -sf "$PREFIX/bin/$tool" "$BUILD_TOOLS/$tool" 2>/dev/null || true
		fi
	done

	# dexdump (from ART)
	if [ -f /apex/com.android.art/bin/dexdump ]; then
		ln -sf /apex/com.android.art/bin/dexdump "$BUILD_TOOLS/dexdump" 2>/dev/null || true
	fi

	# split-select stub
	if [ -L "$BUILD_TOOLS/split-select" ] && readlink "$BUILD_TOOLS/split-select" | grep -q "Android/Sdk"; then
		echo "    ✓ Retaining Mode B custom static split-select symlink"
	else
		cat >"$BUILD_TOOLS/split-select" <<'SPLITEOF'
#!/data/data/com.termux/files/usr/bin/sh
echo "split-select is not available on Termux ARM64"
exit 1
SPLITEOF
		chmod +x "$BUILD_TOOLS/split-select"
	fi

	# core-lambda-stubs.jar
	if [ ! -f "$BUILD_TOOLS/core-lambda-stubs.jar" ]; then
		MANIFEST_TMP="${TMPDIR:-$PREFIX/tmp}/MANIFEST.MF"
		echo "Manifest-Version: 1.0" >"$MANIFEST_TMP"
		jar cfm "$BUILD_TOOLS/core-lambda-stubs.jar" "$MANIFEST_TMP" 2>/dev/null || true
		rm -f "$MANIFEST_TMP"
	fi

	# d8.jar and dx.jar
	ln -sf "$PREFIX/share/java/d8.jar" "$BUILD_TOOLS/lib/d8.jar" 2>/dev/null || true
	ln -sf "$PREFIX/share/java/d8.jar" "$BUILD_TOOLS/lib/dx.jar" 2>/dev/null || true

	echo "    ✓ build-tools $BT_NAME configured"
}

# 9. Create build-tools symlinks (for all versions)
echo "[9/17] Creating build-tools symlinks..."
BT_DIR=$ANDROID_SDK/build-tools
mkdir -p "$BT_DIR"

# If a real build-tools version exists (e.g. 35.0.0-2 from Termux),
# copy it as 35.0.0 so AGP can validate it (AGP rejects versions like 35.0.0-2)
BT_REAL=""
for bt in "$BT_DIR"/*/; do
	if [ -f "$bt/package.xml" ]; then
		BT_REAL="$bt"
		break
	fi
done

is_mode_b=false
if [ -L "$BT_DIR/35.0.0/aapt2" ] && readlink "$BT_DIR/35.0.0/aapt2" | grep -q "Android/Sdk"; then
	is_mode_b=true
fi

if [ "$is_mode_b" = "true" ]; then
	echo "  Validating Mode B toolchain (API 35+ / AAB)..."
	AAPT2_EXE="$BT_DIR/35.0.0/aapt2"
	TMP_DIR=$(mktemp -d "${TMPDIR:-$PREFIX/tmp}/mode_b_test.XXXXXX")
	TMP_RES_DIR="$TMP_DIR/res/values"
	mkdir -p "$TMP_RES_DIR"
	echo '<resources><string name="test">test</string></resources>' >"$TMP_RES_DIR/strings.xml"
	TMP_FLAT_DIR="$TMP_DIR/flat"
	mkdir -p "$TMP_FLAT_DIR"
	TMP_APK="$TMP_DIR/test.apk"

	mode_b_valid=false
	if "$AAPT2_EXE" compile "$TMP_RES_DIR/strings.xml" -o "$TMP_FLAT_DIR/" >/dev/null 2>&1; then
		FLAT_FILE=$(find "$TMP_FLAT_DIR" -name "*.flat" 2>/dev/null | head -n 1)
		if [ -n "$FLAT_FILE" ]; then
			if "$AAPT2_EXE" link "$FLAT_FILE" -o "$TMP_APK" >/dev/null 2>&1 || [ -f "$TMP_APK" ]; then
				mode_b_valid=true
			fi
		fi
	fi
	rm -rf "$TMP_DIR"

	if [ "$mode_b_valid" = "false" ]; then
		echo "  ❌ Error: Mode B toolchain validation failed (aapt2 compile/link failed)."
		echo "  Reverting Mode B activation to Mode A..."

		# Reversible activation: Revert back to Mode A
		rm -rf "$BT_DIR/35.0.0" 2>/dev/null || true
		sed -i '/android.aapt2FromMavenOverride/d' "$HOME/.gradle/gradle.properties" 2>/dev/null || true

		echo "  Mode B reverted. Please install a working NDK (e.g. lzhiyong/termux-ndk) to use Mode B."
		is_mode_b=false
	else
		echo "  ✓ Mode B toolchain validation passed (aapt2 compile/link works)."
	fi
fi

if [ -n "$BT_REAL" ]; then
	if [ "$is_mode_b" = "false" ] && [ ! -f "$BT_DIR/35.0.0/package.xml" ]; then
		echo "  Cloning $(basename $BT_REAL) -> 35.0.0 (for AGP validation)..."
		rm -rf "$BT_DIR/35.0.0"
		cp -a "$BT_REAL" "$BT_DIR/35.0.0"
		BT_REAL_NAME=$(basename "$BT_REAL")
		sed -i "s/$BT_REAL_NAME/35.0.0/g" "$BT_DIR/35.0.0/source.properties" 2>/dev/null || true
		sed -i "s/$BT_REAL_NAME/35.0.0/g" "$BT_DIR/35.0.0/package.xml" 2>/dev/null || true
	elif [ "$is_mode_b" = "true" ] && [ ! -f "$BT_DIR/35.0.0/package.xml" ]; then
		echo "  Re-aligning package metadata for Mode B build-tools..."
		cp "$BT_REAL/package.xml" "$BT_DIR/35.0.0/" 2>/dev/null || true
		cp "$BT_REAL/source.properties" "$BT_DIR/35.0.0/" 2>/dev/null || true
		BT_REAL_NAME=$(basename "$BT_REAL")
		sed -i "s/$BT_REAL_NAME/35.0.0/g" "$BT_DIR/35.0.0/source.properties" 2>/dev/null || true
		sed -i "s/$BT_REAL_NAME/35.0.0/g" "$BT_DIR/35.0.0/package.xml" 2>/dev/null || true
	fi
fi

# Setup default version
setup_build_tools_symlinks "$BT_DIR/35.0.0"

# Create source.properties if missing (required by AGP)
if [ ! -f "$BT_DIR/35.0.0/source.properties" ]; then
	printf "Pkg.Revision=35.0.0\nPkg.Path=build-tools;35.0.0\nPkg.Desc=Android SDK Build-Tools 35\n" >"$BT_DIR/35.0.0/source.properties"
fi

# Also setup any other versions Gradle may have downloaded
for bt_path in "$BT_DIR"/*; do
	if [ -d "$bt_path" ] && [ "$(basename "$bt_path")" != "35.0.0" ]; then
		setup_build_tools_symlinks "$bt_path"
	fi
done

echo "  ✓ Build-tools symlinks created"

# 10. Install cmdline-tools (so flutter can detect Android devices)
echo "[10/17] Installing cmdline-tools..."
if [ ! -d "$ANDROID_SDK/cmdline-tools/latest" ]; then
	mkdir -p $ANDROID_SDK/cmdline-tools
	cd $ANDROID_SDK/cmdline-tools
	curl -L -o tools.zip 'https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip'
	unzip -q tools.zip
	mv cmdline-tools latest
	rm tools.zip
	echo "  ✓ cmdline-tools installed"
else
	echo "  ✓ cmdline-tools already exists"
fi

# 11. Create platform-tools symlinks (adb)
# Note: Gradle may download x86_64 platform-tools, so we force overwrite
echo "[11/17] Creating platform-tools symlinks..."
mkdir -p "$ANDROID_SDK/platform-tools"
# Remove any x86_64 binaries Gradle may have downloaded
rm -f "$ANDROID_SDK/platform-tools/adb" "$ANDROID_SDK/platform-tools/fastboot" 2>/dev/null || true
ln -sf "$PREFIX/bin/adb" "$ANDROID_SDK/platform-tools/adb" 2>/dev/null || true
ln -sf "$PREFIX/bin/fastboot" "$ANDROID_SDK/platform-tools/fastboot" 2>/dev/null || true
echo "  ✓ platform-tools symlinks created"

# 12. Accept Android licenses
echo "[12/17] Accepting Android licenses..."
mkdir -p $ANDROID_SDK/licenses
echo -e "\n24333f8a63b6825ea9c5514f83c2829b004d1fee" >$ANDROID_SDK/licenses/android-sdk-license
echo -e "\n84831b9409646a918e30573bab4c9c91346d8abd" >$ANDROID_SDK/licenses/android-sdk-preview-license
echo "  ✓ Android licenses accepted"

# 13. Configure ANDROID_HOME in flutter config
echo "[13/17] Setting Android SDK path in Flutter config..."
mkdir -p "$HOME" 2>/dev/null || true
if [ -f "$HOME/.flutter_settings" ]; then
	# Rewrite via python3 so the result is always valid JSON. The previous
	# grep/append approach dropped the closing brace, then appended a leading
	# comma, producing a doubled comma whenever "android-sdk" was the last key
	# (e.g. `false,\n  ,"android-sdk": ...`) and corrupting the file.
	if ! python3 - "$HOME/.flutter_settings" "$ANDROID_SDK" <<'PYEOF'
import json
import os
import sys

path, sdk = sys.argv[1], sys.argv[2]
try:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
except (OSError, ValueError):
    data = {}
if not isinstance(data, dict):
    data = {}
data["android-sdk"] = sdk
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
    fh.write("\n")
os.replace(tmp, path)
PYEOF
	then
		# python3 unavailable or failed: fall back to writing a fresh, valid
		# settings file (the same shape as the no-file branch below).
		cat >"$HOME/.flutter_settings" <<SETTINGS
{
  "android-sdk": "$ANDROID_SDK",
  "analytics": false
}
SETTINGS
	fi
else
	cat >"$HOME/.flutter_settings" <<SETTINGS
{
  "android-sdk": "$ANDROID_SDK",
  "analytics": false
}
SETTINGS
fi
echo "  ✓ ANDROID_HOME=$ANDROID_SDK"

# 14. Copy VM snapshots (for debug mode)
echo "[14/17] Checking engine artifacts..."
ENGINE_DIR=$FLUTTER_ROOT/bin/cache/artifacts/engine/linux-arm64

if [ ! -f "$ENGINE_DIR/vm_isolate_snapshot.bin" ]; then
	echo "  ⚠ vm_isolate_snapshot.bin not found - debug APK builds may fail"
	echo "    Please copy from WSL build: flutter/engine/src/out/linux_debug_arm64/gen/flutter/lib/snapshot/"
else
	echo "  ✓ VM snapshots present"
fi

# 15. Provision the Android-target gen_snapshot host symlinks.
# flutter_tools resolves the AOT gen_snapshot for android targets to
# <engine>/android-arm64-{release,profile}/<host>/gen_snapshot
# (getCurrentHostPlatform() returns linux_arm64 on Termux, see the
# "Termux: Android host uses Linux artifacts" patch in build_info.dart).
# Upstream only ships a linux-x64/ (x86_64, unrunnable on ARM64 Termux)
# subdir there. The Linux desktop gen_snapshot (<engine>/linux-arm64/)
# produces target_os=linux/no-compressed-pointers snapshots that the Android
# runtime rejects (issue #41), so the Android-target gen_snapshot is shipped at
# <engine>/linux-arm64-android-{release,profile}/gen_snapshot. The
# patch_artifacts gen_snapshot hunk above resolves directly there, which
# flutter's first-run materialization cannot touch. These symlinks remain a
# fallback for any lookup that still reads the per-build directories directly.
# Idempotent and re-runnable.
echo "[15/17] Creating host platform symlinks..."
ENG_ART=$FLUTTER_ROOT/bin/cache/artifacts/engine
for mode in release profile; do
	dir="android-arm64-$mode"
	src="linux-arm64-android-$mode"
	if [ ! -f "$ENG_ART/$src/gen_snapshot" ]; then
		echo "  ⚠ $src/gen_snapshot missing (deb incomplete?), skipping $dir"
		continue
	fi
	# On a fresh install these directories do not exist yet: flutter's cache
	# only creates them on its first artifact download, which happens during
	# the first `flutter build apk` - i.e. AFTER this script runs. Skipping
	# them here made that first build fail with "Failed to find
	# .../android-arm64-release/linux-arm64/gen_snapshot". Create the
	# directories ourselves. NOTE: flutter's first-run materialization of
	# the upstream linux-x64 files is observed to drop this symlink again;
	# the patch_artifacts gen_snapshot hunk is what actually guarantees the
	# first build, the symlink is a fallback for direct directory lookups.
	if [ ! -d "$ENG_ART/$dir" ]; then
		mkdir -p "$ENG_ART/$dir"
		echo "  ✓ $dir/ created"
	fi
	if [ -e "$ENG_ART/$dir/linux-arm64" ] && [ ! -L "$ENG_ART/$dir/linux-arm64" ]; then
		echo "  ⚠ $dir/linux-arm64 exists as a real directory, leaving untouched"
	elif [ ! -e "$ENG_ART/$dir/linux-arm64" ]; then
		ln -sf "../$src" "$ENG_ART/$dir/linux-arm64"
		echo "  ✓ $dir/linux-arm64 -> ../$src"
	else
		echo "  ✓ $dir/linux-arm64 already present"
	fi
done
# Also create top-level linux-x64 -> linux-arm64 symlink for general artifacts
if [ -d "$ENG_ART/linux-arm64" ] && [ ! -e "$ENG_ART/linux-x64" ]; then
	ln -sf linux-arm64 "$ENG_ART/linux-x64"
	echo "  ✓ linux-x64 -> linux-arm64"
fi

# 16. Create api-level.h for CMake system detection
# CMake's CMakeDetermineSystem.cmake reads $PREFIX/include/android/api-level.h
# Without this file, cmake fails with "file failed to open for reading"
# Derive __ANDROID_API__ from the highest installed SDK platform so api-level.h
# tracks the on-device android.jar without a hardcoded level. Falls back to 35
# only when no platform is installed yet.
echo "[16/17] Creating api-level.h for CMake..."
mkdir -p "$PREFIX/include/android" 2>/dev/null
if [ ! -f "$PREFIX/include/android/api-level.h" ]; then
	API_LEVEL=0
	for platform_dir in "$ANDROID_SDK"/platforms/android-*; do
		[ -d "$platform_dir" ] || continue
		api_num="${platform_dir##*android-}"
		case "$api_num" in
		'' | *[!0-9]*) continue ;;
		esac
		[ "$api_num" -gt "$API_LEVEL" ] && API_LEVEL="$api_num"
	done
	if [ "$API_LEVEL" -eq 0 ]; then
		echo "  ⚠ No Android platform found under $ANDROID_SDK/platforms; defaulting __ANDROID_API__ to 35"
		API_LEVEL=35
	fi
	cat >"$PREFIX/include/android/api-level.h" <<HEADER
#ifndef __ANDROID_API_LEVEL_H__
#define __ANDROID_API_LEVEL_H__
#define __ANDROID_API__ $API_LEVEL
#endif
HEADER
	echo "  ✓ api-level.h created (__ANDROID_API__=$API_LEVEL)"
else
	echo "  ✓ api-level.h already exists"
fi

# 17. Recompile flutter_tools.snapshot and stamp to guarantee offline flutter CLI execution
finalize_flutter_tools_cache() {
	echo "[17/17] Finalizing flutter_tools.snapshot and stamp..."
	local FLUTTER_TOOLS_DIR="$FLUTTER_ROOT/packages/flutter_tools"
	local SNAPSHOT_PATH="$FLUTTER_ROOT/bin/cache/flutter_tools.snapshot"
	local STAMP_PATH="$FLUTTER_ROOT/bin/cache/flutter_tools.stamp"
	local DART_BIN="$DART_SDK/bin/dart"
	local ENTRY_POINT="$FLUTTER_TOOLS_DIR/bin/flutter_tools.dart"
	local PKG_CONFIG="$FLUTTER_TOOLS_DIR/.dart_tool/package_config.json"
	local PUBSPEC_LOCK="$FLUTTER_TOOLS_DIR/pubspec.lock"
	local PUBSPEC_YAML="$FLUTTER_TOOLS_DIR/pubspec.yaml"

	# Remove any existing snapshot and stamp before generating
	rm -f "$SNAPSHOT_PATH" "$STAMP_PATH"

	if [ ! -f "$DART_BIN" ] && [ ! -x "$DART_BIN" ]; then
		echo "  ❌ Error: Dart compiler missing at $DART_BIN" >&2
		return 1
	fi
	if [ ! -f "$ENTRY_POINT" ]; then
		echo "  ❌ Error: flutter_tools entry point missing at $ENTRY_POINT" >&2
		return 1
	fi

	local REVISION=""
	if [ -f "$FLUTTER_ROOT/bin/internal/engine.version" ]; then
		REVISION=$(cat "$FLUTTER_ROOT/bin/internal/engine.version" 2>/dev/null | tr -d '\n\r')
	elif [ -n "${FLUTTER_PREBUILT_ENGINE_VERSION:-}" ]; then
		REVISION="$FLUTTER_PREBUILT_ENGINE_VERSION"
	fi
	if [ -z "$REVISION" ]; then
		if [ -x "$PREFIX/bin/git" ] && [ -d "$FLUTTER_ROOT/.git" ]; then
			REVISION=$("$PREFIX/bin/git" -C "$FLUTTER_ROOT" rev-parse HEAD 2>/dev/null || true)
		elif command -v git >/dev/null 2>&1 && [ -d "$FLUTTER_ROOT/.git" ]; then
			REVISION=$(git -C "$FLUTTER_ROOT" rev-parse HEAD 2>/dev/null || true)
		fi
	fi
	if [ -z "$REVISION" ]; then
		echo "  ❌ Error: Failed to determine Flutter SDK revision" >&2
		return 1
	fi

	local COMPILE_KEY="${REVISION}:"

	# Ensure pubspec.lock exists and is strictly newer than pubspec.yaml
	if [ -f "$PUBSPEC_YAML" ]; then
		touch -t 202701010000 "$PUBSPEC_LOCK" 2>/dev/null || touch "$PUBSPEC_LOCK"
	fi

	# Ensure bin/cache directory exists
	mkdir -p "$FLUTTER_ROOT/bin/cache"

	# Ensure environment variables are active during snapshot generation
	export PATH="$PREFIX/bin:$PATH"
	export TMPDIR="${TMPDIR:-$PREFIX/tmp}"
	if [ -n "$REVISION" ]; then
		export FLUTTER_PREBUILT_ENGINE_VERSION="$REVISION"
	fi

	# Compile snapshot with --snapshot-kind="app-jit" and --no-enable-mirrors matching shared.sh
	# Note: passing "--version" as argument allows flutter_tools.dart to execute and produce complete App-JIT snapshot
	local COMPILE_OK=0
	if [ -f "$PKG_CONFIG" ]; then
		if "$DART_BIN" --verbosity=error --snapshot="$SNAPSHOT_PATH" --snapshot-kind="app-jit" --packages="$PKG_CONFIG" --no-enable-mirrors "$ENTRY_POINT" "--version" >/dev/null 2>&1; then
			COMPILE_OK=1
		fi
	fi
	if [ "$COMPILE_OK" -ne 1 ]; then
		if "$DART_BIN" --verbosity=error --snapshot="$SNAPSHOT_PATH" --snapshot-kind="app-jit" --no-enable-mirrors "$ENTRY_POINT" "--version" >/dev/null 2>&1; then
			COMPILE_OK=1
		fi
	fi

	if [ "$COMPILE_OK" -ne 1 ] || [ ! -s "$SNAPSHOT_PATH" ]; then
		echo "  ❌ Error: Failed to compile flutter_tools.snapshot" >&2
		"$DART_BIN" --verbosity=error --snapshot="$SNAPSHOT_PATH" --snapshot-kind="app-jit" --packages="$PKG_CONFIG" --no-enable-mirrors "$ENTRY_POINT" "--version" || true
		rm -f "$SNAPSHOT_PATH" "$STAMP_PATH"
		return 1
	fi

	# Write stamp ONLY after snapshot compilation and size verification succeeds
	echo -n "$COMPILE_KEY" >"$STAMP_PATH"
	if [ ! -s "$STAMP_PATH" ] || [ "$(<"$STAMP_PATH")" != "$COMPILE_KEY" ]; then
		echo "  ❌ Error: Failed to write valid flutter_tools.stamp" >&2
		rm -f "$SNAPSHOT_PATH" "$STAMP_PATH"
		return 1
	fi

	echo "  ✓ flutter_tools.snapshot and flutter_tools.stamp finalized (key=$COMPILE_KEY)"
	return 0
}

ensure_profile_env() {
	local profile_dir="${PREFIX:-/data/data/com.termux/files/usr}/etc/profile.d"
	local profile_file="$profile_dir/flutter.sh"
	mkdir -p "$profile_dir" 2>/dev/null || true
	if [ ! -f "$profile_file" ]; then
		echo "Creating environment profile script at $profile_file..."
		cat >"$profile_file" <<'EOF'
PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
export PATH=${PREFIX}/opt/flutter/bin:${PATH}
export TMPDIR="${PREFIX}/tmp"
if [ -z "${ANDROID_NDK_HOME:-}" ]; then
  for ndk in ${PREFIX}/opt/android-sdk/ndk/*/; do
    [ -d "$ndk" ] && export ANDROID_NDK_HOME="${ndk%/}" && break
  done
fi
if [ -z "${JAVA_HOME:-}" ] && [ -d "${PREFIX}/lib/jvm" ]; then
  _jvm=$(find "${PREFIX}/lib/jvm" -maxdepth 1 -type d -name 'java-*-openjdk' 2>/dev/null | sort -V | tail -1)
  [ -n "$_jvm" ] && export JAVA_HOME="$_jvm"
  unset _jvm
fi
EOF
		chmod 755 "$profile_file" 2>/dev/null || true
		echo "  ✓ Generated $profile_file"
	fi
}

finalize_flutter_tools_cache || {
	echo "❌ Failed to finalize flutter_tools cache" >&2
	exit 1
}
ensure_profile_env

echo ""

echo "=========================================="
echo "Post-install configuration complete!"
echo "=========================================="
echo ""
echo "=== Quick Start ==="
echo "  source $PREFIX/etc/profile.d/flutter.sh"
echo "  flutter create myapp && cd myapp"
echo ""
echo "=== IMPORTANT: Project Setup (REQUIRED for each Flutter project) ==="
echo "  1. Fix gradlew shebang:"
echo "     sed -i '1s|#!/usr/bin/env bash|#!'"$PREFIX"'/bin/bash|' android/gradlew"
echo ""
echo "  2. Edit android/app/build.gradle.kts:"
echo "     compileSdk = $COMPILE_SDK"
echo "     targetSdk = $TARGET_SDK"
echo "     ndk { abiFilters += listOf(\"arm64-v8a\") }"
echo ""
echo "  3. Add to android/gradle.properties:"
echo "     android.aapt2FromMavenOverride=$PREFIX/bin/aapt2"
echo ""
echo "  4. Set JAVA_HOME before building:"
echo "     export JAVA_HOME=\$(find \$PREFIX/lib/jvm -maxdepth 1 -type d -name 'java-*-openjdk' | sort -V | tail -1)"
echo ""
echo ""
echo "  5. Build APK:"
echo "     flutter build apk --release --target-platform android-arm64"
echo ""
echo "=== Linux Desktop Build (optional) ==="
echo "  1. Add to linux/CMakeLists.txt (first line, before cmake_minimum_required):"
echo "     set(CMAKE_SYSTEM_NAME Linux)"
echo ""
echo "  2. Build:"
echo "     flutter build linux --release"
echo ""
echo "=== Flutter Run (hot reload on device) ==="
echo "  1. Install android-tools:  pkg install android-tools"
echo "  2. Enable ADB TCP (from PC):  adb tcpip 5555"
echo "  3. Connect in Termux:  adb connect localhost:5555"
echo "     (Accept the 'Allow USB debugging?' dialog on screen)"
echo "  4. Run:  flutter run -d emulator-5554"
echo ""
