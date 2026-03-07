#!/usr/bin/env python3
"""
Record arm poses by hand-guiding. Uses raw servo positions (no calibration needed).

Usage:
    python record.py home
    python record.py look_at_user
    python record.py --list
    python record.py --print
"""

import argparse
import json
from pathlib import Path

from compat import TorqueMode, make_bus, DEFAULT_PORT

POSES_PATH = Path(__file__).parent / "poses.json"


def load_poses(path: Path) -> dict:
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def save_poses(path: Path, poses: dict):
    with open(path, "w") as f:
        json.dump(poses, f, indent=2)
        f.write("\n")


def read_positions(bus) -> dict[str, float]:
    values = bus.read("Present_Position")
    return {name: round(float(v), 2) for name, v in zip(bus.motor_names, values)}


def print_positions(bus):
    pos = read_positions(bus)
    print("Current joint positions (raw):")
    for name, val in pos.items():
        print(f"  {name}: {val}")
    return pos


def main():
    parser = argparse.ArgumentParser(description="Record arm poses by hand-guiding")
    parser.add_argument("pose_name", nargs="?")
    parser.add_argument("--port", default=DEFAULT_PORT)
    parser.add_argument("--poses", type=Path, default=POSES_PATH)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--print", dest="print_pos", action="store_true")
    args = parser.parse_args()

    if args.list:
        poses = load_poses(args.poses)
        if not poses:
            print("No poses saved yet.")
        for name, joints in poses.items():
            print(f"\n{name}:")
            for joint, val in joints.items():
                print(f"  {joint}: {val}")
        return

    bus = make_bus(args.port)
    try:
        if args.print_pos:
            print_positions(bus)
            return

        if not args.pose_name:
            parser.error("Provide a pose name (e.g. 'home') or use --list / --print")

        print(f"Torque OFF — move the arm to '{args.pose_name}' by hand.")
        bus.write("Torque_Enable", TorqueMode.DISABLED.value)
        input(f"Press Enter when the arm is in the '{args.pose_name}' position... ")

        pos = print_positions(bus)
        poses = load_poses(args.poses)
        poses[args.pose_name] = pos
        save_poses(args.poses, poses)
        print(f"\nSaved '{args.pose_name}' to {args.poses}")
    finally:
        bus.write("Torque_Enable", TorqueMode.DISABLED.value)
        bus.disconnect()


if __name__ == "__main__":
    main()
