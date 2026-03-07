#!/usr/bin/env python3
"""
Move the arm between saved poses. Uses raw servo positions (no calibration needed).

Usage:
    python move.py                               # home -> look_at_user -> home
    python move.py home                          # move to home only
    python move.py look_at_user home             # custom sequence
    python move.py --speed 50 --pause 3.0        # slower
"""

import argparse
import json
import time
from pathlib import Path

import numpy as np

from compat import TorqueMode, make_bus, DEFAULT_PORT

POSES_PATH = Path(__file__).parent / "poses.json"


def load_poses(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"No poses at {path}. Run record.py first.")
    with open(path) as f:
        return json.load(f)


def setup_arm(bus, speed: int):
    """SO100 position control preset."""
    bus.write("Torque_Enable", TorqueMode.DISABLED.value)
    bus.write("Mode", 0)
    bus.write("P_Coefficient", 16)
    bus.write("I_Coefficient", 0)
    bus.write("D_Coefficient", 32)
    bus.write("Lock", 0)
    bus.write("Maximum_Acceleration", speed)
    bus.write("Acceleration", speed)
    bus.write("Torque_Enable", TorqueMode.ENABLED.value)
    print(f"Arm ready: position mode, acceleration={speed}")


def move_to_pose(bus, pose: dict[str, float], pause: float):
    goal = np.array([pose[name] for name in bus.motor_names], dtype=np.float32)
    print(f"  Goal: {dict(zip(bus.motor_names, goal.tolist()))}")
    bus.write("Goal_Position", goal)
    time.sleep(pause)

    actual = bus.read("Present_Position")
    print(f"  Actual: {dict(zip(bus.motor_names, [round(float(v), 2) for v in actual]))}")


def main():
    parser = argparse.ArgumentParser(description="Move arm between saved poses")
    parser.add_argument("poses", nargs="*")
    parser.add_argument("--port", default=DEFAULT_PORT)
    parser.add_argument("--poses-file", type=Path, default=POSES_PATH)
    parser.add_argument("--speed", type=int, default=100)
    parser.add_argument("--pause", type=float, default=2.0)
    args = parser.parse_args()

    pose_sequence = args.poses or ["home", "look_at_user", "home"]
    all_poses = load_poses(args.poses_file)

    for name in pose_sequence:
        if name not in all_poses:
            available = ", ".join(all_poses.keys()) or "(none)"
            raise SystemExit(f"Pose '{name}' not found. Available: {available}")

    bus = make_bus(args.port)
    try:
        setup_arm(bus, args.speed)

        for i, name in enumerate(pose_sequence):
            print(f"\n[{i+1}/{len(pose_sequence)}] Moving to '{name}'...")
            move_to_pose(bus, all_poses[name], args.pause)

        print("\nDone.")
    finally:
        bus.write("Torque_Enable", TorqueMode.DISABLED.value)
        bus.disconnect()


if __name__ == "__main__":
    main()
