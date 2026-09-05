#!/data/data/com.termux/files/usr/bin/bash

echo "=== Testing gen_snapshot for Termux ==="

# Copy gen_snapshot to Termux bin
cp /data/local/tmp/gen_snapshot $PREFIX/bin/
chmod +x $PREFIX/bin/gen_snapshot

# Test version
echo ""
echo ">>> gen_snapshot --version"
gen_snapshot --version

# Check if Flutter is installed
echo ""
echo ">>> flutter --version"
flutter --version 2>/dev/null || echo "Flutter not installed yet"

echo ""
echo "=== Test complete ==="
