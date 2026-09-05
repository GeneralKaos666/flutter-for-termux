#!/data/data/com.termux/files/usr/bin/bash
#
# Flutter APK first-time build script
# First-time APK Build Script for Termux
#
# Usage: run in your Flutter project directory
#        ./build_first_apk.sh
#
# This script will automatically:
#   1. Configure the project (NDK, gradle.properties)
#   2. Run the first build (triggers Gradle download)
#   3. Fix AAPT2 (x86_64 → ARM64)
#   4. Run the final build
#

set -e

# Color definitions
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

NDK_VERSION="29.0.14206865"
ANDROID_HOME="$PREFIX/opt/android-sdk"

echo -e "${BLUE}"
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║     Flutter APK First Build                               ║"
echo "║     Auto-configure and Build APK                          ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# ========================================
# Check environment
# ========================================
echo -e "${GREEN}[CHECK]${NC} Verifying environment..."

# Check if in the Flutter project directory
if [ ! -f "pubspec.yaml" ]; then
	echo -e "${RED}Error: Not in a Flutter project directory${NC}"
	echo "Please run this script from your Flutter project root"
	echo ""
	echo "Example:"
	echo "  flutter create myapp"
	echo "  cd myapp"
	echo "  bash build_first_apk.sh"
	exit 1
fi

if [ ! -d "android" ]; then
	echo -e "${RED}Error: android directory not found${NC}"
	echo "Run 'flutter create .' to generate Android files."
	exit 1
fi

# Check Flutter
if ! command -v flutter &>/dev/null; then
	echo -e "${RED}Error: Flutter is not installed${NC}"
	echo "Please run install_termux_flutter.sh first."
	exit 1
fi

# Check Android SDK
if [ ! -d "$ANDROID_HOME" ]; then
	echo -e "${RED}Error: Android SDK is not installed${NC}"
	echo "Please run setup_android_sdk.sh first."
	exit 1
fi

# Check NDK
if [ ! -d "$ANDROID_HOME/ndk/$NDK_VERSION" ]; then
	echo -e "${YELLOW}Warning: NDK $NDK_VERSION not found${NC}"
	echo "APK build may fail."
	echo ""
fi

echo "  Flutter: $(flutter --version 2>/dev/null | head -1 || echo 'OK')"
echo "  Android SDK: $ANDROID_HOME"
echo "  NDK: $NDK_VERSION"
echo ""

TOTAL_STEPS=4

# ========================================
# Step 1: Configure project
# ========================================
echo -e "${GREEN}[1/${TOTAL_STEPS}]${NC} Configuring project..."

# Configure local.properties
LOCAL_PROPS="android/local.properties"
if ! grep -q "ndk.dir" "$LOCAL_PROPS" 2>/dev/null; then
	echo "ndk.dir=$ANDROID_HOME/ndk/$NDK_VERSION" >>"$LOCAL_PROPS"
	echo "  ✓ NDK path added to local.properties"
else
	echo "  ✓ NDK path already configured"
fi

# Configure gradle.properties
GRADLE_PROPS="android/gradle.properties"

if ! grep -q "android.useAndroidX" "$GRADLE_PROPS" 2>/dev/null; then
	echo "android.useAndroidX=true" >>"$GRADLE_PROPS"
fi

if ! grep -q "android.enableJetifier" "$GRADLE_PROPS" 2>/dev/null; then
	echo "android.enableJetifier=true" >>"$GRADLE_PROPS"
fi

# Limit Gradle memory usage (Termux environment)
if ! grep -q "org.gradle.jvmargs" "$GRADLE_PROPS" 2>/dev/null; then
	echo "org.gradle.jvmargs=-Xmx768m -XX:MaxMetaspaceSize=384m" >>"$GRADLE_PROPS"
fi

echo "  ✓ gradle.properties configured"

# Configure build.gradle.kts
BUILD_GRADLE="android/app/build.gradle.kts"
BUILD_GRADLE_GROOVY="android/app/build.gradle"

if [ -f "$BUILD_GRADLE" ]; then
	if grep -q "flutter.ndkVersion" "$BUILD_GRADLE" 2>/dev/null; then
		sed -i 's/ndkVersion = flutter.ndkVersion/ndkVersion = "'"$NDK_VERSION"'"/g' "$BUILD_GRADLE"
		echo "  ✓ build.gradle.kts NDK version updated"
	elif ! grep -q "ndkVersion" "$BUILD_GRADLE" 2>/dev/null; then
		sed -i 's/android {/android {\n    ndkVersion = "'"$NDK_VERSION"'"/' "$BUILD_GRADLE"
		echo "  ✓ build.gradle.kts NDK version added"
	else
		echo "  ✓ build.gradle.kts NDK configured"
	fi
elif [ -f "$BUILD_GRADLE_GROOVY" ]; then
	if grep -q "flutter.ndkVersion" "$BUILD_GRADLE_GROOVY" 2>/dev/null; then
		sed -i 's/ndkVersion flutter.ndkVersion/ndkVersion "'"$NDK_VERSION"'"/g' "$BUILD_GRADLE_GROOVY"
		echo "  ✓ build.gradle NDK version updated"
	elif ! grep -q "ndkVersion" "$BUILD_GRADLE_GROOVY" 2>/dev/null; then
		sed -i 's/android {/android {\n    ndkVersion "'"$NDK_VERSION"'"/' "$BUILD_GRADLE_GROOVY"
		echo "  ✓ build.gradle NDK version added"
	else
		echo "  ✓ build.gradle NDK configured"
	fi
fi

echo ""

# ========================================
# Step 2: First build (triggers Gradle download)
# ========================================
echo -e "${GREEN}[2/${TOTAL_STEPS}]${NC} First build (downloading Gradle dependencies)..."
echo "  This may take several minutes, please be patient..."
echo ""

# Run the build; it may fail (AAPT2 issue)
flutter build apk --release 2>&1 | tee /tmp/flutter_build.log || true

# Check for AAPT2 errors
if grep -q "EM_X86_64" /tmp/flutter_build.log 2>/dev/null; then
	echo ""
	echo -e "${YELLOW}  AAPT2 architecture issue detected, fixing...${NC}"
	NEED_AAPT2_FIX=true
elif grep -q "Built build/app/outputs" /tmp/flutter_build.log 2>/dev/null; then
	echo ""
	echo -e "${GREEN}  First build succeeded!${NC}"
	NEED_AAPT2_FIX=false
else
	echo ""
	echo -e "${YELLOW}  Build may have failed, trying to fix AAPT2...${NC}"
	NEED_AAPT2_FIX=true
fi

echo ""

# ========================================
# Step 3: Fix AAPT2
# ========================================
if [ "$NEED_AAPT2_FIX" = true ]; then
	echo -e "${GREEN}[3/${TOTAL_STEPS}]${NC} Fixing AAPT2..."

	# Find and replace AAPT2
	AAPT2_FIXED=false
	while IFS= read -r aapt2_path; do
		if [ -n "$aapt2_path" ]; then
			rm -f "$aapt2_path"
			ln -s "$ANDROID_HOME/build-tools/35.0.0/aapt2" "$aapt2_path"
			echo "  ✓ Fixed: $aapt2_path"
			AAPT2_FIXED=true
		fi
	done < <(find ~/.gradle/caches -name "aapt2" -path "*aapt2-*-linux*" 2>/dev/null)

	if [ "$AAPT2_FIXED" = false ]; then
		echo "  No AAPT2 found that needs fixing"
	fi

	echo ""
else
	echo -e "${GREEN}[3/${TOTAL_STEPS}]${NC} AAPT2 does not need fixing"
	echo ""
fi

# ========================================
# Step 4: Final build
# ========================================
echo -e "${GREEN}[4/${TOTAL_STEPS}]${NC} Building APK..."
echo ""

if flutter build apk --release; then
	echo ""
	echo -e "${GREEN}╔═══════════════════════════════════════════════════════════╗${NC}"
	echo -e "${GREEN}║     APK Build Successful!                                 ║${NC}"
	echo -e "${GREEN}╚═══════════════════════════════════════════════════════════╝${NC}"
	echo ""

	APK_PATH="build/app/outputs/flutter-apk/app-release.apk"
	if [ -f "$APK_PATH" ]; then
		APK_SIZE=$(ls -lh "$APK_PATH" | awk '{print $5}')
		echo -e "APK location: ${BLUE}$APK_PATH${NC}"
		echo -e "APK size: ${BLUE}$APK_SIZE${NC}"
		echo ""
		echo "To install on device:"
		echo -e "  ${BLUE}adb install $APK_PATH${NC}"
	fi
else
	echo ""
	echo -e "${RED}╔═══════════════════════════════════════════════════════════╗${NC}"
	echo -e "${RED}║     APK Build Failed                                      ║${NC}"
	echo -e "${RED}╚═══════════════════════════════════════════════════════════╝${NC}"
	echo ""
	echo "Please check the error messages and refer to the documentation:"
	echo "  https://github.com/GeneralKaos666/flutter-for-termux#構建-android-apk"
	exit 1
fi

echo ""
