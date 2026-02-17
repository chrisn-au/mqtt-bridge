#!/usr/bin/env bash
# One-time bootstrap for ansible-pull on mqtt-bridge Pi.
# Run this manually via SSH: ssh chris@mqtt-bridge.local 'bash -s' < ansible/ansible-pull-setup.sh
set -euo pipefail

REPO_URL="https://github.com/chrisn-au/mqtt-bridge.git"
PLAYBOOK="ansible/playbook.yml"
LOG="/var/log/ansible-pull.log"

echo "=== ansible-pull bootstrap ==="

# Install ansible
if ! command -v ansible-pull &>/dev/null; then
    echo "Installing ansible..."
    sudo apt-get update
    sudo apt-get install -y ansible
else
    echo "ansible already installed: $(ansible --version | head -1)"
fi

# Create log file
sudo touch "$LOG"
sudo chown chris:chris "$LOG"

# Set up cron job (every 5 minutes)
CRON_LINE="*/5 * * * * /usr/bin/ansible-pull -U $REPO_URL -i localhost, $PLAYBOOK >> $LOG 2>&1"

# Remove any existing ansible-pull cron entry, then add ours
( crontab -l 2>/dev/null | grep -v 'ansible-pull' ; echo "$CRON_LINE" ) | crontab -

echo "Cron job installed:"
crontab -l | grep ansible-pull

# Run once now to verify
echo ""
echo "=== Running ansible-pull now to verify ==="
sudo ansible-pull -U "$REPO_URL" -i localhost, "$PLAYBOOK" 2>&1 | tee -a "$LOG"

echo ""
echo "=== Done ==="
echo "Logs at: $LOG"
echo "Check status: mosquitto_sub -t 'ansible/status'"
