#!/data/data/com.termux/files/usr/bin/bash
mkdir -p ~/.ssh
cat /sdcard/Download/ssh_pubkey.txt >> ~/.ssh/authorized_keys
chmod 700 ~/.ssh
chmod 600 ~/.ssh/authorized_keys
echo "SSH key added to authorized_keys"
