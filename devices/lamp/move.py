#!/usr/bin/env python3
"""
Move the arm between saved poses using the lerobot Robot API.
All transitions are smoothly interpolated to avoid collisions.
Holds position at the end until you Ctrl+C.

Usage:
    python move.py home                          # move to home, hold
    python move.py home look_at_user             # home then look_at_user, hold
    python move.py thinking home look_at_user    # any sequence of saved poses
    python move.py --duration 2.0 home           # fixed 2s transition
    python move.py --list                        # show available poses
"""

import argparse
import json
import time
from pathlib import Path

from lerobot.robots import so_follower, make_robot_from_config
from motion import interpolate_to, get_current_positions, max_joint_delta

DEFAULT_PORT = "/dev/ttyACM0"
POSES_PATH = Path(__file__).parent / "poses.json"


def load_poses(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"No poses at {path}. Run record.py first.")
    with open(path) as f:
        return json.load(f)


def get_static_target(pose_data: dict) -> dict[str, float] | None:
    """Extract the target joint dict from a pose entry.
    Returns None if it's an animation (those go through play_animation.py)."""
    if isinstance(pose_data, dict) and pose_data.get("type") == "animation":
        # For animations, use the first frame as the target
        frames = pose_data.get("frames", [])
        return frames[0] if frames else None
    if isinstance(pose_data, dict):
        return pose_data
    return None


def main():
    parser = argparse.ArgumentParser(description="Move arm between saved poses (interpolated)")
    parser.add_argument("poses", nargs="*", help="Pose names to visit in order")
    parser.add_argument("--port", default=DEFAULT_PORT)
    parser.add_argument("--poses-file", type=Path, default=POSES_PATH)
    parser.add_argument("--duration", type=float, default=None,
                        help="Fixed transition duration in seconds. Default: auto-scale by distance.")
    parser.add_argument("--list", action="store_true", help="Show available poses")
    args = parser.parse_args()

    all_poses = load_poses(args.poses_file)

    if args.list:
        for name, data in all_poses.items():
            if isinstance(data, dict) and data.get("type") == "animation":
                n = len(data.get("frames", []))
                fps = data.get("fps", 30)
                print(f"  {name}: [animation] {n} frames @ {fps}fps ({n/fps:.1f}s)")
            else:
                print(f"  {name}: [pose]")
        return

    if not args.poses:
        names = ", ".join(all_poses.keys())
        parser.error(f"Provide pose names to move to. Available: {names}")

    for name in args.poses:
        if name not in all_poses:
            available = ", ".join(all_poses.keys())
            raise SystemExit(f"Pose '{name}' not found. Available: {available}")

    config = so_follower.SO100FollowerConfig(port=args.port)
    robot = make_robot_from_config(config)
    robot.connect()

    try:
        for i, name in enumerate(args.poses):
            target = get_static_target(all_poses[name])
            if target is None:
                print(f"  Skipping '{name}' — no valid target frames.")
                continue

            current = get_current_positions(robot)
            delta = max_joint_delta(current, target)
            print(f"\n[{i+1}/{len(args.poses)}] Moving to '{name}' (max delta: {delta:.1f}°)")

            interpolate_to(robot, target, duration_s=args.duration)

            obs = robot.get_observation()
            print(f"  Arrived: {{{', '.join(f'{k}: {float(v):.1f}' for k, v in obs.items())}}}")

        print(f"\nHolding at '{args.poses[-1]}'. Ctrl+C to release.")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nReleased.")
    finally:
        robot.disconnect()


if __name__ == "__main__":
    main()
