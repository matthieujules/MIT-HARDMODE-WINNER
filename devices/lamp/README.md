# Lamp / LEM Arm

The current Lamp implementation is a 5-servo SO-101-style arm with a LEMP RGB light mounted at the end effector.

## Hardware

- Motor bus: Feetech / STS3215-compatible chain over USB serial
- Joints: `base_yaw`, `shoulder_pitch`, `elbow`, `wrist_pitch`, `wrist_roll`
- Light: PWM RGB on three GPIO pins
- No speech, no camera, no mic

## Important

- The arm must have its external motor power connected. USB alone is not enough for a reliable SO-101/Feetech bus session.
- The default serial path is `/dev/ttyACM0`. If possible, replace it with a stable `/dev/serial/by-id/...` path in [`config.yaml`](/Users/ethrbt/code/MIT-HARDMODE-HACKATHON/devices/lamp/config.yaml).

## Config

[`config.yaml`](/Users/ethrbt/code/MIT-HARDMODE-HACKATHON/devices/lamp/config.yaml) defines:

- GPIO pins for the LEMP RGB channels
- Feetech bus serial settings
- Joint limits, home angles, and servo IDs
- Arm geometry values so link lengths and offsets can be changed without editing code
- Named presets for fast poses like `focus`, `relax`, and `alert`

## Install On Pi

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
pip install -r lamp/requirements.txt
```

If `lerobot[feetech]` does not install cleanly from PyPI on the Pi, follow the official LeRobot install docs and install the Feetech extra from source.

## Calibrate First

Before using `pose_recorder.py`, calibrate the Feetech bus once on the Pi:

```bash
lerobot-calibrate --robot.type=so101_follower --robot.port=/dev/ttyACM0 --robot.id=lamp_arm
```

That should create a calibration file at:

```bash
~/.cache/huggingface/lerobot/calibration/robots/so101_follower/lamp_arm.json
```

The Lamp runtime now expects that calibration file by default. You can override the path in [`config.yaml`](/Users/ethrbt/code/MIT-HARDMODE-HACKATHON/devices/lamp/config.yaml) if needed.

## Pose Teaching

Print current joint positions:

```bash
python3 lamp/pose_recorder.py print
```

Disable torque and hand-place the arm:

```bash
python3 lamp/pose_recorder.py torque_off
```

Re-enable torque:

```bash
python3 lamp/pose_recorder.py torque_on
```

Recommended capture flow:

```bash
python3 lamp/pose_recorder.py capture home
python3 lamp/pose_recorder.py capture look_at_user
python3 lamp/pose_recorder.py list
python3 lamp/pose_recorder.py play home
python3 lamp/pose_recorder.py play look_at_user
python3 lamp/pose_recorder.py interpolate --steps 20 --segment-ms 120
```

`capture` disables torque, waits for you to move the arm by hand, reads the current positions from the Feetech bus, saves the named pose, then re-enables torque.

## Light Control

The light path is separate from the arm path. It accepts either:

- one RGB value: `r, g, b`
- an animation sequence of 4-tuples: `(r, g, b, t_ms)`

Static color test:

```bash
python3 lamp/light_demo.py rgb 255 0 0 --live
```

Animation test:

```bash
python3 lamp/light_demo.py animate "255,0,0,80;255,127,0,80;255,255,0,80;0,255,0,80;0,0,255,80" --live
```

That frame string format is the central-agent contract for light animations: each tuple is `RGBT`, where `T` is how long that frame should stay on in milliseconds.

The Lamp planner also accepts animation-style instructions such as:

```bash
python3 lamp/main.py --once "frames 255,0,0,40;0,255,0,40;0,0,255,40"
```

## Development Loop

Run the local planner simulator with:

```bash
python3 lamp/main.py
```

This accepts typed instructions from the terminal, resolves them into a structured plan, previews the resulting joint targets and RGB output, and in simulation mode prints the Feetech goal positions that would be sent to the arm.
