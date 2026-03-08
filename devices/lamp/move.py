#!/usr/bin/env python3
"""
Move the arm between saved poses using the lerobot Robot API.
Holds position at the end until you Ctrl+C or type the next command.

Usage:
    python move.py home                          # move to home, hold
    python move.py home look_at_user             # home then look_at_user, hold
    python move.py thinking home look_at_user    # any sequence of saved poses
    python move.py --pause 3.0 home look_at_user # slower transitions
    python move.py --list                        # show available poses
"""

import argparse
import json
import time
from pathlib import Path

from lerobot.robots import so_follower, make_robot_from_config

DEFAULT_PORT = "/dev/ttyACM0"
POSES_PATH = Path(__file__).parent / "poses.json"


def load_poses(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"No poses at {path}. Run record.py first.")
    with open(path) as f:
        return json.load(f)


def move_to_pose(robot, pose: dict[str, float], pause: float):
    print(f"  Goal: { {k: round(v, 2) for k, v in pose.items()} }")
    robot.send_action(pose)
    time.sleep(pause)

    obs = robot.get_observation()
    print(f"  Actual: { {k: round(float(v), 2) for k, v in obs.items()} }")


def main():
    parser = argparse.ArgumentParser(description="Move arm between saved poses")
    parser.add_argument("poses", nargs="*", help="Pose names to visit in order")
    parser.add_argument("--port", default=DEFAULT_PORT)
    parser.add_argument("--poses-file", type=Path, default=POSES_PATH)
    parser.add_argument("--pause", type=float, default=2.0)
    parser.add_argument("--list", action="store_true", help="Show available poses")
    args = parser.parse_args()

    all_poses = load_poses(args.poses_file)

    if args.list:
        print("Available poses:", ", ".join(all_poses.keys()))
        return

    if not args.poses:
        parser.error(f"Provide pose names to move to. Available: {', '.join(all_poses.keys())}")

    for name in args.poses:
        if name not in all_poses:
            available = ", ".join(all_poses.keys())
            raise SystemExit(f"Pose '{name}' not found. Available: {available}")

    config = so_follower.SO100FollowerConfig(port=args.port)
    robot = make_robot_from_config(config)
    robot.connect()

    try:
        for i, name in enumerate(args.poses):
            print(f"\n[{i+1}/{len(args.poses)}] Moving to '{name}'...")
            move_to_pose(robot, all_poses[name], args.pause)

        print(f"\nHolding at '{args.poses[-1]}'. Ctrl+C to release.")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nReleased.")
    finally:
        robot.disconnect()


if __name__ == "__main__":
    main()
