#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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
  LAMP_HOST=100.82.116.53
  MIRROR_HOST=100.71.104.73
  RADIO_HOST=100.119.150.35
  ROVER_HOST=100.81.100.34
EOF
}

device_host() {
  case "$1" in
    lamp) echo "${LAMP_HOST:-100.82.116.53}" ;;
    mirror) echo "${MIRROR_HOST:-100.71.104.73}" ;;
    radio) echo "${RADIO_HOST:-100.119.150.35}" ;;
    rover) echo "${ROVER_HOST:-100.81.100.34}" ;;
    *)
      echo "Unknown device: $1" >&2
      exit 1
      ;;
  esac
}

device_user() {
  case "$1" in
    lamp) echo "lamp" ;;
    mirror) echo "mirror" ;;
    radio) echo "radio" ;;
    rover) echo "rover" ;;
  esac
}

sync_device() {
  local device="$1"
  local host
  local user
  local source_dir
  local remote_dir

  host="$(device_host "$device")"
  user="$(device_user "$device")"
  source_dir="${SCRIPT_DIR}/${device}/"
  remote_dir="/home/${user}/Desktop/${device}"

  if [[ ! -d "$source_dir" ]]; then
    echo "Missing local device folder: $source_dir" >&2
    exit 1
  fi

  echo "Syncing ${device} -> ${user}@${host}:${remote_dir}"
  ssh "${SSH_OPTS[@]}" "${user}@${host}" "mkdir -p '${remote_dir}'"
  rsync -avz --delete -e "ssh ${SSH_OPTS[*]}" "${source_dir}" "${user}@${host}:${remote_dir}/"
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
