#!/data/data/com.termux/files/usr/bin/bash
# Fluttermux-style Gradle fix for Termux Flutter builds
# Run this in your Flutter project directory

if [ ! -f "pubspec.yaml" ]; then
    echo "Error: Not in a Flutter project directory (no pubspec.yaml found)"
    exit 1
fi

echo "Applying Gradle fixes for Termux..."

# 1. Add AAPT2 override to gradle.properties
GRADLE_PROPS="android/gradle.properties"
AAPT2_LINE="android.aapt2FromMavenOverride=/data/data/com.termux/files/usr/bin/aapt2"

if ! grep -q "aapt2FromMavenOverride" "$GRADLE_PROPS" 2>/dev/null; then
    echo "$AAPT2_LINE" >> "$GRADLE_PROPS"
    echo "[OK] Added AAPT2 override to gradle.properties"
else
    echo "[SKIP] AAPT2 override already present"
fi

# 2. Fix gradlew permissions and shebang
cd android
if command -v termux-fix-shebang &> /dev/null; then
    termux-fix-shebang gradlew
    echo "[OK] Fixed gradlew shebang"
fi
chmod +x gradlew
echo "[OK] Set gradlew executable"
cd ..

# 3. Update Gradle distribution URL to compatible version
WRAPPER_PROPS="android/gradle/wrapper/gradle-wrapper.properties"
if [ -f "$WRAPPER_PROPS" ]; then
    # Use Gradle 7.6 which is compatible with Termux
    sed -i 's#^distributionUrl=.*#distributionUrl=https\\://services.gradle.org/distributions/gradle-7.6-all.zip#' "$WRAPPER_PROPS"
    echo "[OK] Updated Gradle wrapper to 7.6"
fi

echo ""
echo "==================================="
echo "Gradle fixes applied!"
echo "Now try: flutter build apk --debug"
echo "==================================="
