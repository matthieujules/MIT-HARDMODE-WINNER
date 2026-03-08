#!/usr/bin/env python3
"""
Record arm poses and animations by hand-guiding using the lerobot Robot API.

Usage:
    # Static poses
    python record.py home
    python record.py look_at_user
    python record.py any_name_you_want

    # Animations (record movement over time)
    python record.py --animate wave
    python record.py --animate nod --fps 60

    # Manage
    python record.py --list
    python record.py --print
    python record.py --delete wave
"""

import argparse
import json
import threading
import time
from pathlib import Path

from lerobot.robots import so_follower, make_robot_from_config

DEFAULT_PORT = "/dev/ttyACM1"
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


def get_positions(robot) -> dict[str, float]:
    obs = robot.get_observation()
    return {k: round(float(v), 2) for k, v in obs.items()}


def print_positions(robot) -> dict[str, float]:
    pos = get_positions(robot)
    print("Current joint positions:")
    for name, val in pos.items():
        print(f"  {name}: {val}")
    return pos


def record_animation(robot, name: str, fps: int, poses_path: Path):
    """Record an animation by sampling joint positions while the user moves the arm."""
    print(f"Torque OFF — get ready to move the arm for '{name}'.")
    robot.bus.disable_torque()
    input("Press Enter to START recording... ")

    frames = []
    stop_event = threading.Event()
    interval = 1.0 / fps

    def wait_for_stop():
        input()
        stop_event.set()

    print(f"RECORDING at {fps}fps — move the arm now. Press Enter to STOP.")
    stopper = threading.Thread(target=wait_for_stop, daemon=True)
    stopper.start()

    start_time = time.perf_counter()
    while not stop_event.is_set():
        frame_start = time.perf_counter()
        obs = robot.get_observation()
        frame = {k: round(float(v), 2) for k, v in obs.items()}
        frames.append(frame)
        elapsed = time.perf_counter() - frame_start
        sleep_time = interval - elapsed
        if sleep_time > 0:
            time.sleep(sleep_time)

    total_time = time.perf_counter() - start_time
    print(f"\nRecorded {len(frames)} frames in {total_time:.1f}s (effective {len(frames)/total_time:.1f}fps)")

    animation = {
        "type": "animation",
        "fps": fps,
        "frames": frames,
    }

    poses = load_poses(poses_path)
    poses[name] = animation
    save_poses(poses_path, poses)
    print(f"Saved animation '{name}' to {poses_path}")


def main():
    parser = argparse.ArgumentParser(description="Record arm poses by hand-guiding")
    parser.add_argument("pose_name", nargs="?")
    parser.add_argument("--port", default=DEFAULT_PORT)
    parser.add_argument("--poses", type=Path, default=POSES_PATH)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--print", dest="print_pos", action="store_true")
    parser.add_argument("--delete", type=str, help="Delete a saved pose by name")
    parser.add_argument("--animate", type=str, metavar="NAME", help="Record an animation by name")
    parser.add_argument("--fps", type=int, default=30, help="Sampling rate for animation recording (default: 30)")
    args = parser.parse_args()

    if args.delete:
        poses = load_poses(args.poses)
        if args.delete in poses:
            del poses[args.delete]
            save_poses(args.poses, poses)
            print(f"Deleted '{args.delete}'")
        else:
            print(f"Pose '{args.delete}' not found.")
        return

    if args.list:
        poses = load_poses(args.poses)
        if not poses:
            print("No poses saved yet.")
        for name, data in poses.items():
            if isinstance(data, dict) and data.get("type") == "animation":
                n_frames = len(data.get("frames", []))
                fps = data.get("fps", 30)
                duration = n_frames / fps if fps else 0
                print(f"\n{name}: [animation] {n_frames} frames @ {fps}fps ({duration:.1f}s)")
            else:
                print(f"\n{name}: [pose]")
                for joint, val in data.items():
                    print(f"  {joint}: {val}")
        return

    config = so_follower.SO100FollowerConfig(port=args.port)
    robot = make_robot_from_config(config)
    robot.connect()

    try:
        if args.print_pos:
            print_positions(robot)
            return

        # Animation recording mode
        if args.animate:
            record_animation(robot, args.animate, args.fps, args.poses)
            return

        if not args.pose_name:
            parser.error("Provide a pose name, --animate NAME, or --list / --print")

        print(f"Torque OFF — move the arm to '{args.pose_name}' by hand.")
        robot.bus.disable_torque()
        input(f"Press Enter when the arm is in the '{args.pose_name}' position... ")

        pos = print_positions(robot)
        poses = load_poses(args.poses)
        poses[args.pose_name] = pos
        save_poses(args.poses, poses)
        print(f"\nSaved '{args.pose_name}' to {args.poses}")
    finally:
        robot.disconnect()


if __name__ == "__main__":
    main()
