#!/bin/bash
# Clean PATH
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/iml1s/projects/termux-flutter/depot_tools

cd /home/iml1s/projects/termux-flutter/flutter/engine/src
exec ninja -C out/android_profile_arm64 -j24 gen_snapshot
