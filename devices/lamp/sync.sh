#!/usr/bin/env bash
# Sync LAMP_TASC folder to the Lamp Pi.
#
# Usage:
#   ./sync.sh                     # sync to lamphost
#   ./sync.sh 192.168.1.101       # sync to a specific IP
#   DRYRUN=1 ./sync.sh            # preview what would be sent

set -euo pipefail

HOST="${1:-lamphost}"
REMOTE_USER="lamp"
REMOTE_DIR="/home/${REMOTE_USER}/Desktop/LAMP_TASC"
LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)/"

EXTRA_FLAGS=""
if [ "${DRYRUN:-}" = "1" ]; then
  EXTRA_FLAGS="--dry-run"
fi

echo "Syncing ${LOCAL_DIR} -> ${REMOTE_USER}@${HOST}:${REMOTE_DIR}"

rsync -avz --progress \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.DS_Store' \
  $EXTRA_FLAGS \
  "${LOCAL_DIR}" \
  "${REMOTE_USER}@${HOST}:${REMOTE_DIR}"

echo "Done. Files on ${HOST}:${REMOTE_DIR}"
