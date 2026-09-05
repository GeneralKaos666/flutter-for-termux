#!/bin/bash
cat > /usr/local/bin/vpython3 << 'SCRIPT'
#!/bin/bash
exec python3 "$@"
SCRIPT
chmod +x /usr/local/bin/vpython3
which vpython3
