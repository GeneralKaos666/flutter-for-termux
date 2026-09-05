#!/bin/bash
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd /home/iml1s/projects/termux-flutter/flutter/engine/src/out/linux_profile_arm64
exec ninja flutter flutter/shell/platform/linux:flutter_gtk -j24
