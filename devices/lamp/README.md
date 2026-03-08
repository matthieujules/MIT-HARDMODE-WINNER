# Lamp Device

5-servo SO-100 arm with RGB LED, controlled by a Cerebras LLM agent loop.

## Hardware

- **Arm**: SO-100 follower (5x Feetech STS3215 servos) via USB serial `/dev/ttyACM1`
- **LED**: PWM RGB on GPIO 17 (R), 27 (G), 22 (B) via lgpio
- **No** speaker, camera, or mic
- External motor power required (USB alone insufficient)

## Architecture

```
Control Plane (laptop)
  └─ WebSocket ──> ws_client.py (Pi)
                      ├─ Layer 1: handle_command() ── direct actions via hardware.py
                      └─ Layer 2: run_agent_loop() ── Cerebras LLM decides tools
                                    └─ hardware.py ── Robot API + LED
```

## Agent Tools (Cerebras gpt-oss-120b)

The LLM agent receives natural language instructions from the master and chooses from these tools:

| Tool | Description | Persists? |
|------|-------------|-----------|
| `pose(name)` | Move arm to a named pose/animation from `poses.json` | Yes |
| `set_color(r, g, b)` | Set LED RGB color (0-255) | Until changed |
| `set_brightness(brightness)` | Set brightness (0.0-1.0) | Until changed |
| `flash(r, g, b, duration_ms)` | Flash a color briefly, revert to previous | No |
| `pulse(r, g, b, cycles, period_ms)` | Sine-wave breathing effect | No |
| `done(detail)` | Signal task completion (required) | - |

Tools are built dynamically -- the `pose` enum reflects whatever poses exist in `poses.json`.

## Poses (poses.json)

Recorded on the Pi via `record.py`. Two formats:

- **Static**: `{"shoulder_pan.pos": 0.97, "shoulder_lift.pos": -97.45, ...}` -- smooth interpolated movement
- **Animation**: `{"type": "animation", "fps": 30, "frames": [{...}, ...]}` -- frame-by-frame playback

Current poses: `home` (static), `look_at_user` (animation, 402 frames @ 30fps)

### Recording new poses

```bash
# SSH to Pi
ssh lamp@lamphost
cd /home/lamp/Desktop/lamp

# Record a static pose (disables torque, hand-place arm, press Enter)
/home/lamp/Desktop/venv/bin/python3 record.py save my_pose

# Record an animation (move arm by hand while recording)
/home/lamp/Desktop/venv/bin/python3 record.py animate my_animation

# Test it
/home/lamp/Desktop/venv/bin/python3 move.py my_pose
/home/lamp/Desktop/venv/bin/python3 play_animation.py my_animation
```

New poses are immediately available to the agent (tools built dynamically from poses.json).

## Key Files

| File | Role |
|------|------|
| `main.py` | Entry point: `--connect` for WS runtime, `--live-serial` for real hardware |
| `ws_client.py` | WebSocket client: register, connect, heartbeat, auto-reconnect, message routing |
| `agent.py` | Cerebras LLM agent loop, tool definitions, tool execution |
| `hardware.py` | Hardware controller: lerobot Robot API (arm) + LEDController (LED) |
| `planner.py` | Regex planner for Layer 1 direct commands (COLOR_MAP, POSE_HINTS) |
| `LED_control.py` | lgpio PWM RGB LED driver |
| `SOUL.md` | Agent personality prompt |
| `config.yaml` | Device config (serial port, LED pins, network, capabilities) |
| `poses.json` | Recorded arm poses and animations (on Pi only) |
| `sync.sh` | rsync deploy script |

Standalone utilities (not used by runtime):
`move.py`, `play_animation.py`, `record.py`, `test_led.py`, `calibrate.py`, `test_hardware.py`

## Deploy & Run

```bash
# 1. Deploy code to Pi
cd devices/lamp && ./sync.sh

# 2. Start on Pi (with real hardware)
ssh -f lamp@lamphost 'cd /home/lamp/Desktop/lamp && \
  nohup /home/lamp/Desktop/venv/bin/python3 main.py --connect --live-serial \
  > /tmp/lamp.log 2>&1 &'

# 3. Check logs
ssh lamp@lamphost 'tail -f /tmp/lamp.log'

# 4. Kill
ssh lamp@lamphost 'pkill -f "main.py --connect"'
```

Control plane must be running first: `python3 -m uvicorn control_plane.app:app --host 0.0.0.0 --port 8000`

## Pi Environment

- Host: `lamphost` (Tailscale: 100.82.116.53)
- User: `lamp`
- Python: `/home/lamp/Desktop/venv/bin/python3` (3.13)
- Packages: lerobot 0.4.4, openai, websockets, lgpio, pyyaml, python-dotenv
- Servo: `/dev/ttyACM1`

## Gotchas

- `gripper.pos` key must be stripped from poses -- SO100FollowerConfig has 5 motors, no gripper. Passing it causes `StopIteration` crash.
- `robot.send_action()` moves servos instantly. `hardware.py` interpolates frames at 30fps for smooth motion.
- Cerebras cold start is 7-14s. `agent.warmup()` runs on boot to eliminate this.
- `tool_choice="required"` is mandatory on Cerebras to prevent text-only responses.
- `load_dotenv()` must run before importing `agent.py` (reads `CEREBRAS_API_KEY` at import time).
