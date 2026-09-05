#!/data/data/com.termux/files/usr/bin/bash
cd ~/testapp/android
echo "android.aapt2FromMavenOverride=/data/data/com.termux/files/usr/bin/aapt2" >> gradle.properties
cat gradle.properties
