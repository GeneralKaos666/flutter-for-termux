#!/data/data/com.termux/files/usr/bin/bash
# Setup SSH for remote access

# Install openssh if not present
pkg install -y openssh

# Generate host keys if not exist
if [ ! -f ~/.ssh/ssh_host_rsa_key ]; then
    ssh-keygen -A
fi

# Start sshd
sshd

# Show connection info
echo "==================================="
echo "SSH Server Started on port 8022"
echo "Username: $(whoami)"
echo "==================================="
echo "On your PC run:"
echo "  adb forward tcp:8022 tcp:8022"
echo "  ssh $(whoami)@localhost -p 8022"
echo "==================================="

# Set password if not set
echo ""
echo "If you haven't set a password, run: passwd"
