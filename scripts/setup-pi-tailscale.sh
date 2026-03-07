#!/usr/bin/env bash
# Setup Tailscale on a Raspberry Pi for ClaudeHome
# Usage: ssh pi@<pi-ip> 'bash -s' < scripts/setup-pi-tailscale.sh <hostname>
# Example: ssh pi@192.168.1.101 'bash -s' < scripts/setup-pi-tailscale.sh lamp-pi

set -euo pipefail

HOSTNAME="${1:?Usage: $0 <tailscale-hostname> (lamp-pi|mirror-pi|radio-pi|rover-pi)}"

echo "=== Installing Tailscale on $(hostname) ==="
curl -fsSL https://tailscale.com/install.sh | sh

echo "=== Starting Tailscale as ${HOSTNAME} ==="
sudo tailscale up --hostname="${HOSTNAME}"

echo "=== Verifying ==="
tailscale status
echo ""
echo "Done. This Pi is now reachable as ${HOSTNAME} on your tailnet."
echo "Control plane: http://claude-master:8000"
