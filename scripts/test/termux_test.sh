#!/data/data/com.termux/files/usr/bin/bash
# Flutter APK Build Test Script
cd ~/testapp

# Kill any existing gradle processes
pkill -f gradle 2>/dev/null
sleep 2

# Clear previous build
rm -rf build

# Set memory limits for Gradle
export GRADLE_OPTS="-Xmx512m -XX:MaxMetaspaceSize=256m"

# Build and log
echo "=== Flutter APK Build Test ===" > ~/build_log.txt
echo "Starting build at $(date)" >> ~/build_log.txt
echo "" >> ~/build_log.txt

flutter build apk --release --no-tree-shake-icons 2>&1 | tee -a ~/build_log.txt

echo "" >> ~/build_log.txt
echo "Build finished at $(date)" >> ~/build_log.txt
echo "Exit code: $?" >> ~/build_log.txt

# Check result
if [ -f "build/app/outputs/flutter-apk/app-release.apk" ]; then
    echo "SUCCESS: APK built!" >> ~/build_log.txt
    ls -la build/app/outputs/flutter-apk/*.apk >> ~/build_log.txt
else
    echo "FAILED: No APK found" >> ~/build_log.txt
fi
