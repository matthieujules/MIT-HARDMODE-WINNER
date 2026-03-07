#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SSH_USER="${SSH_USER:-pi}"
SSH_OPTS=(
  -o StrictHostKeyChecking=accept-new
  -o ConnectTimeout=10
)

usage() {
  cat <<'EOF'
Usage: ./devices/sync.sh [-lamp] [-mirror] [-radio] [-rover] [-all]

Examples:
  ./devices/sync.sh -lamp
  ./devices/sync.sh -mirror -radio
  ./devices/sync.sh -all

Optional environment overrides:
  SSH_USER=pi
  LAMP_HOST=lamp.local
  MIRROR_HOST=mirror.local
  RADIO_HOST=radio.local
  ROVER_HOST=rover.local
EOF
}

device_host() {
  case "$1" in
    lamp) echo "${LAMP_HOST:-192.168.1.101}" ;;
    mirror) echo "${MIRROR_HOST:-192.168.1.102}" ;;
    radio) echo "${RADIO_HOST:-192.168.1.103}" ;;
    rover) echo "${ROVER_HOST:-192.168.1.104}" ;;
    *)
      echo "Unknown device: $1" >&2
      exit 1
      ;;
  esac
}

sync_device() {
  local device="$1"
  local host
  local source_dir
  local remote_dir

  host="$(device_host "$device")"
  source_dir="${SCRIPT_DIR}/${device}/"
  remote_dir="/home/${SSH_USER}/Desktop/${device}"

  if [[ ! -d "$source_dir" ]]; then
    echo "Missing local device folder: $source_dir" >&2
    exit 1
  fi

  echo "Syncing ${device} -> ${SSH_USER}@${host}:${remote_dir}"
  ssh "${SSH_OPTS[@]}" "${SSH_USER}@${host}" "mkdir -p '${remote_dir}'"
  rsync -avz --delete -e "ssh ${SSH_OPTS[*]}" "${source_dir}" "${SSH_USER}@${host}:${remote_dir}/"
}

if [[ $# -eq 0 ]]; then
  usage
  exit 1
fi

declare -a devices_to_sync=()

for arg in "$@"; do
  case "$arg" in
    -lamp)
      devices_to_sync+=("lamp")
      ;;
    -mirror)
      devices_to_sync+=("mirror")
      ;;
    -radio)
      devices_to_sync+=("radio")
      ;;
    -rover)
      devices_to_sync+=("rover")
      ;;
    -all)
      devices_to_sync=("lamp" "mirror" "radio" "rover")
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $arg" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ ${#devices_to_sync[@]} -eq 0 ]]; then
  usage
  exit 1
fi

for device in "${devices_to_sync[@]}"; do
  sync_device "$device"
done
