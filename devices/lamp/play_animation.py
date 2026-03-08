#!/usr/bin/env python3
"""
Play back a recorded animation using the lerobot Robot API.
Holds position at the end until Ctrl+C.

Usage:
    python play_animation.py wave                # play once, hold at end
    python play_animation.py wave --loop         # loop forever
    python play_animation.py wave --loop 3       # loop 3 times
    python play_animation.py wave --speed 0.5    # half speed
    python play_animation.py wave --speed 2.0    # double speed
    python play_animation.py --list              # show available animations
"""

import argparse
import json
import time
from pathlib import Path

from lerobot.robots import so_follower, make_robot_from_config

DEFAULT_PORT = "/dev/ttyACM1"
POSES_PATH = Path(__file__).parent / "poses.json"


def load_poses(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"No poses at {path}. Run record.py first.")
    with open(path) as f:
        return json.load(f)


def get_animations(poses: dict) -> dict:
    return {k: v for k, v in poses.items() if isinstance(v, dict) and v.get("type") == "animation"}


def play_once(robot, animation: dict, speed: float):
    fps = animation["fps"]
    frames = animation["frames"]
    interval = (1.0 / fps) / speed

    print(f"  Playing {len(frames)} frames @ {fps}fps (speed {speed}x)")

    for i, frame in enumerate(frames):
        frame_start = time.perf_counter()
        robot.send_action(frame)
        elapsed = time.perf_counter() - frame_start
        sleep_time = interval - elapsed
        if sleep_time > 0:
            time.sleep(sleep_time)

        if (i + 1) % fps == 0:
            print(f"  Frame {i+1}/{len(frames)}")


def main():
    parser = argparse.ArgumentParser(description="Play back recorded animations")
    parser.add_argument("name", nargs="?", help="Animation name to play")
    parser.add_argument("--port", default=DEFAULT_PORT)
    parser.add_argument("--poses-file", type=Path, default=POSES_PATH)
    parser.add_argument("--loop", nargs="?", const=-1, type=int, default=1,
                        help="Loop playback. No value = forever, or specify count.")
    parser.add_argument("--speed", type=float, default=1.0, help="Playback speed multiplier (default: 1.0)")
    parser.add_argument("--list", action="store_true", help="Show available animations")
    args = parser.parse_args()

    all_poses = load_poses(args.poses_file)
    animations = get_animations(all_poses)

    if args.list:
        if not animations:
            print("No animations recorded yet. Use: python record.py --animate NAME")
            return
        for name, data in animations.items():
            n_frames = len(data.get("frames", []))
            fps = data.get("fps", 30)
            duration = n_frames / fps if fps else 0
            print(f"  {name}: {n_frames} frames @ {fps}fps ({duration:.1f}s)")
        return

    if not args.name:
        available = ", ".join(animations.keys()) if animations else "(none)"
        parser.error(f"Provide an animation name. Available: {available}")

    if args.name not in animations:
        available = ", ".join(animations.keys()) if animations else "(none)"
        raise SystemExit(f"'{args.name}' is not an animation. Available: {available}")

    animation = animations[args.name]

    config = so_follower.SO100FollowerConfig(port=args.port)
    robot = make_robot_from_config(config)
    robot.connect()

    try:
        loop_count = args.loop
        iteration = 0

        while loop_count == -1 or iteration < loop_count:
            iteration += 1
            label = f"loop {iteration}" if loop_count != 1 else "playing"
            print(f"\n[{label}] '{args.name}'")
            play_once(robot, animation, args.speed)

        print(f"\nDone. Holding at final frame. Ctrl+C to release.")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nReleased.")
    finally:
        robot.disconnect()


if __name__ == "__main__":
    main()
