#!/bin/bash
# Start control plane + all 4 device agents locally (sim mode)
# Devices connect via WebSocket, run real Cerebras agent loops, no hardware needed
#
# Usage:
#   bash tests/start_full_local.sh          # start everything
#   bash tests/start_full_local.sh --kill   # kill all processes
#
# Then run the dinner test:
#   bash tests/run_dinner_live.sh

set -e
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

MASTER_URL="http://localhost:8000"
PIDS_FILE="/tmp/claudehome_local_pids"

kill_all() {
    echo "Killing all ClaudeHome local processes..."
    if [ -f "$PIDS_FILE" ]; then
        while read -r pid; do
            kill "$pid" 2>/dev/null && echo "  Killed PID $pid" || true
        done < "$PIDS_FILE"
        rm -f "$PIDS_FILE"
    fi
    # Also kill by pattern in case pids file is stale
    pkill -f "uvicorn control_plane.app:app" 2>/dev/null || true
    pkill -f "devices/lamp/main.py" 2>/dev/null || true
    pkill -f "devices/mirror/main.py" 2>/dev/null || true
    pkill -f "devices/rover/main.py" 2>/dev/null || true
    pkill -f "devices/radio/RASPi/main.py" 2>/dev/null || true
    echo "Done."
}

if [ "$1" = "--kill" ]; then
    kill_all
    exit 0
fi

# Kill any existing processes first
kill_all 2>/dev/null
sleep 1

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║     ClaudeHome — Full Local Test (Sim Mode)         ║"
echo "║     All devices run Cerebras agent loops locally     ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# Wipe previous state/logs for a clean test run
echo "Wiping previous state and logs..."
echo '{"mode":"solo","mood":"neutral","people_count":0}' > "$ROOT/data/state.json"
echo '[]' > "$ROOT/data/devices.json"
> "$ROOT/data/event_log.jsonl"
> "$ROOT/data/master_log.jsonl"
echo "  State, devices, event log, and master log cleared."
echo ""

> "$PIDS_FILE"

# 1. Control plane
echo "Starting control plane..."
python3 -m uvicorn control_plane.app:app --host 0.0.0.0 --port 8000 \
    > /tmp/claudehome_control.log 2>&1 &
echo $! >> "$PIDS_FILE"
echo "  PID $! → /tmp/claudehome_control.log"

# Wait for control plane to be ready
echo "  Waiting for control plane..."
for i in $(seq 1 15); do
    if curl -s http://localhost:8000/state > /dev/null 2>&1; then
        echo "  Control plane ready."
        break
    fi
    sleep 1
done

# 2. Lamp (sim mode — no --live-serial)
echo "Starting lamp agent (sim mode)..."
cd "$ROOT/devices/lamp"
MASTER_URL="$MASTER_URL" python3 main.py --connect \
    > /tmp/claudehome_lamp.log 2>&1 &
echo $! >> "$PIDS_FILE"
echo "  PID $! → /tmp/claudehome_lamp.log"
cd "$ROOT"

# 3. Mirror (headless, no camera)
echo "Starting mirror agent (headless, no camera)..."
cd "$ROOT/devices/mirror"
MASTER_URL="$MASTER_URL" MIRROR_HEADLESS=1 python3 main.py --connect --skip-camera \
    > /tmp/claudehome_mirror.log 2>&1 &
echo $! >> "$PIDS_FILE"
echo "  PID $! → /tmp/claudehome_mirror.log"
cd "$ROOT"

# 4. Rover (sim mode — no lgpio on mac)
echo "Starting rover agent (sim mode)..."
cd "$ROOT/devices/rover"
MASTER_URL="$MASTER_URL" python3 main.py --connect \
    > /tmp/claudehome_rover.log 2>&1 &
echo $! >> "$PIDS_FILE"
echo "  PID $! → /tmp/claudehome_rover.log"
cd "$ROOT"

# 5. Radio (sim mode — no audio/dial hardware, RADIO_SIM skips actual playback)
echo "Starting radio agent (sim mode)..."
cd "$ROOT/devices/radio/RASPi"
MASTER_URL="$MASTER_URL" RADIO_SIM=1 python3 main.py --connect \
    > /tmp/claudehome_radio.log 2>&1 &
echo $! >> "$PIDS_FILE"
echo "  PID $! → /tmp/claudehome_radio.log"
cd "$ROOT"

# Wait for devices to register
echo ""
echo "Waiting for devices to register..."
sleep 5

# Check device status
echo ""
echo "Registered devices:"
curl -s http://localhost:8000/devices | python3 -c "
import sys, json
devices = json.load(sys.stdin)
for d in devices:
    did = d.get('device_id', '?')
    name = d.get('device_name', '?')
    status = d.get('status', '?')
    print(f'  {did:>10}: {name} ({status})')
if not devices:
    print('  (none registered yet — check logs)')
" 2>/dev/null

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  All processes started. Logs at /tmp/claudehome_*.log"
echo ""
echo "  Dashboard:    http://localhost:8000"
echo "  Run dinner:   bash tests/run_dinner_live.sh"
echo "  Check logs:   tail -f /tmp/claudehome_lamp.log"
echo "  Kill all:     bash tests/start_full_local.sh --kill"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
