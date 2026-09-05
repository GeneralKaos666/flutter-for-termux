#!/bin/bash
export PATH=/home/iml1s/projects/termux-flutter/depot_tools:/usr/local/bin:/usr/bin:/bin
cd /home/iml1s/projects/termux-flutter/flutter/engine/src/out/android_release_arm64
ninja flutter/third_party/dart/runtime/bin:gen_snapshot
