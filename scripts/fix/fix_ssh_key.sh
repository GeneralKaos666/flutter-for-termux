#!/data/data/com.termux/files/usr/bin/bash
mkdir -p ~/.ssh
rm -f ~/.ssh/authorized_keys
cat /sdcard/Download/ssh_pubkey.txt | tr -d '\r' > ~/.ssh/authorized_keys
chmod 700 ~/.ssh
chmod 600 ~/.ssh/authorized_keys
echo "Contents of authorized_keys:"
cat ~/.ssh/authorized_keys
echo ""
echo "Restarting sshd..."
pkill sshd
sshd
echo "Done. SSH should now work with key auth."
