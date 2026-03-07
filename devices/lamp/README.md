# Lamp / LEM Arm

The current Lamp implementation is a 4-joint SO-101 robot arm with a LEMP RGB light mounted at the end effector.

## Hardware

- Arm controller: SO-101 motor board over USB serial
- Joints: `base_yaw`, `shoulder`, `elbow`, `wrist`
- Light: PWM RGB on three GPIO pins
- No speech, no camera, no mic

## Config

[`config.yaml`](/Users/ethrbt/code/MIT-HARDMODE-HACKATHON/devices/lamp/config.yaml) defines:

- GPIO pins for the LEMP RGB channels
- USB serial settings for the SO-101 controller
- Joint limits, home angles, servo IDs
- Arm geometry values so link lengths and offsets can be changed without editing code
- Named presets for fast poses like `focus`, `relax`, and `alert`

## Development Loop

Run the local simulator with:

```bash
python3 devices/lamp/main.py
```

This accepts typed instructions from the terminal, resolves them into a structured plan, previews the resulting joint targets and RGB output, and in simulation mode prints the serial payload that would be sent to the SO-101 board.
