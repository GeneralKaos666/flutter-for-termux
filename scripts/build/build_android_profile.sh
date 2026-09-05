#!/bin/bash
export PATH=/home/iml1s/projects/termux-flutter/depot_tools:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd /home/iml1s/projects/termux-flutter/flutter/engine/src

# Configure Android profile arm64
./flutter/tools/gn --android --android-cpu=arm64 --runtime-mode=profile --no-goma --target-toolchain=//build/toolchain/termux:arm64 --target-sysroot=/home/iml1s/projects/termux-flutter/sysroot --target-triple=aarch64-linux-android --termux-prefix=/data/data/com.termux/files/usr

# Build gen_snapshot for profile
ninja -C out/android_profile_arm64 -j24 gen_snapshot
