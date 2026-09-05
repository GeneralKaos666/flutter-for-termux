#!/data/data/com.termux/files/usr/bin/bash
set -e

cd ~/testapp

echo "=== Fluttermux Gradle Fix ==="

# 1. Add AAPT2 override
GRADLE_PROPS="android/gradle.properties"
AAPT2_LINE="android.aapt2FromMavenOverride=/data/data/com.termux/files/usr/bin/aapt2"
if ! grep -q "aapt2FromMavenOverride" "$GRADLE_PROPS" 2>/dev/null; then
    echo "$AAPT2_LINE" >> "$GRADLE_PROPS"
    echo "[1/4] Added AAPT2 override"
else
    echo "[1/4] AAPT2 override already present"
fi

# 2. Fix gradlew shebang
cd android
if command -v termux-fix-shebang &> /dev/null; then
    termux-fix-shebang gradlew
    echo "[2/4] Fixed gradlew shebang"
else
    echo "[2/4] termux-fix-shebang not found, skipping"
fi

# 3. Set gradlew executable
chmod +x gradlew
echo "[3/4] Set gradlew executable"

# 4. Update Gradle wrapper to 7.6
WRAPPER_PROPS="gradle/wrapper/gradle-wrapper.properties"
if [ -f "$WRAPPER_PROPS" ]; then
    sed -i 's#^distributionUrl=.*#distributionUrl=https\\://services.gradle.org/distributions/gradle-7.6-all.zip#' "$WRAPPER_PROPS"
    echo "[4/4] Updated Gradle wrapper to 7.6"
    echo "Current wrapper config:"
    cat "$WRAPPER_PROPS"
fi

cd ..
echo ""
echo "=== Fix Complete ==="
echo "Now running: flutter build apk --debug"
echo ""

flutter build apk --debug
