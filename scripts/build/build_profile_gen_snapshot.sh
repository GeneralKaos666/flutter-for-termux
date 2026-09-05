#!/bin/bash
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/iml1s/projects/termux-flutter/depot_tools

# Check vpython3
if ! which vpython3 > /dev/null 2>&1; then
    echo "Creating vpython3 wrapper..."
    cat > /tmp/vpython3 << 'SCRIPT'
#!/bin/bash
exec python3 "$@"
SCRIPT
    chmod +x /tmp/vpython3
    sudo mv /tmp/vpython3 /usr/local/bin/vpython3
fi

echo "vpython3 path: $(which vpython3)"

cd /home/iml1s/projects/termux-flutter/flutter/engine/src
ninja -C out/android_profile_arm64 -j24 gen_snapshot
