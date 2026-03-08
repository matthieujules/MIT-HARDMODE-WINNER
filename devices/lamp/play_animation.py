#!/usr/bin/env python3
"""
Play back recorded animations using the lerobot Robot API.
Smoothly interpolates to the start frame before playing.
Holds position at the end until Ctrl+C.

Usage:
    python play_animation.py wave                    # play once, hold at end
    python play_animation.py wave --loop             # loop forever
    python play_animation.py wave --loop 3           # loop 3 times
    python play_animation.py wave --speed 0.5        # half speed
    python play_animation.py wave nod                # play wave then nod (interpolated between)
    python play_animation.py wave --start-pose home  # interpolate from home first
    python play_animation.py --list                  # show available animations
"""

import argparse
import json
import time
from pathlib import Path

from lerobot.robots import so_follower, make_robot_from_config
from motion import interpolate_to, get_current_positions, max_joint_delta

DEFAULT_PORT = "/dev/ttyACM1"
POSES_PATH = Path(__file__).parent / "poses.json"


def load_poses(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"No poses at {path}. Run record.py first.")
    with open(path) as f:
        return json.load(f)


def get_animations(poses: dict) -> dict:
    return {k: v for k, v in poses.items() if isinstance(v, dict) and v.get("type") == "animation"}


def play_frames(robot, frames: list[dict], fps: int, speed: float):
    """Play a sequence of frames at the given fps and speed. No interpolation — frames are already dense."""
    interval = (1.0 / fps) / speed

    for i, frame in enumerate(frames):
        frame_start = time.perf_counter()
        robot.send_action(frame)
        elapsed = time.perf_counter() - frame_start
        sleep_time = interval - elapsed
        if sleep_time > 0:
            time.sleep(sleep_time)

        if (i + 1) % fps == 0:
            print(f"    Frame {i+1}/{len(frames)}")


def main():
    parser = argparse.ArgumentParser(description="Play back recorded animations (with smooth transitions)")
    parser.add_argument("names", nargs="*", help="Animation name(s) to play in sequence")
    parser.add_argument("--port", default=DEFAULT_PORT)
    parser.add_argument("--poses-file", type=Path, default=POSES_PATH)
    parser.add_argument("--loop", nargs="?", const=-1, type=int, default=1,
                        help="Loop playback. No value = forever, or specify count.")
    parser.add_argument("--speed", type=float, default=1.0, help="Playback speed multiplier")
    parser.add_argument("--start-pose", type=str, default=None,
                        help="Move to this pose before playing (interpolated)")
    parser.add_argument("--transition", type=float, default=None,
                        help="Fixed transition duration in seconds. Default: auto-scale.")
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

    if not args.names:
        available = ", ".join(animations.keys()) if animations else "(none)"
        parser.error(f"Provide animation name(s). Available: {available}")

    # Validate all names exist as animations
    for name in args.names:
        if name not in animations:
            available = ", ".join(animations.keys()) if animations else "(none)"
            raise SystemExit(f"'{name}' is not an animation. Available: {available}")

    # Build the playlist
    playlist = [animations[name] for name in args.names]

    config = so_follower.SO100FollowerConfig(port=args.port)
    robot = make_robot_from_config(config)
    robot.connect()

    try:
        # Optional: move to a starting pose first
        if args.start_pose:
            if args.start_pose not in all_poses:
                raise SystemExit(f"Start pose '{args.start_pose}' not found.")
            pose_data = all_poses[args.start_pose]
            target = pose_data if not pose_data.get("type") == "animation" else pose_data["frames"][0]
            print(f"Moving to start pose '{args.start_pose}'...")
            interpolate_to(robot, target, duration_s=args.transition)
            print("  Ready.")

        loop_count = args.loop
        iteration = 0

        while loop_count == -1 or iteration < loop_count:
            iteration += 1

            for idx, (name, anim) in enumerate(zip(args.names, playlist)):
                fps = anim["fps"]
                frames = anim["frames"]

                if not frames:
                    print(f"  Skipping '{name}' — no frames.")
                    continue

                # Interpolate from current position to first frame of this animation
                first_frame = frames[0]
                current = get_current_positions(robot)
                delta = max_joint_delta(current, first_frame)

                if delta > 2.0:  # Only interpolate if we're more than 2° away
                    label = f"loop {iteration}" if loop_count != 1 else "playing"
                    print(f"\n  [{label}] Transitioning to start of '{name}' (delta: {delta:.1f}°)")
                    interpolate_to(robot, first_frame, duration_s=args.transition)

                label = f"loop {iteration}" if loop_count != 1 else "playing"
                print(f"  [{label}] '{name}': {len(frames)} frames @ {fps}fps (speed {args.speed}x)")
                play_frames(robot, frames, fps, args.speed)

        print(f"\nDone. Holding at final frame. Ctrl+C to release.")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nReleased.")
    finally:
        robot.disconnect()


if __name__ == "__main__":
    main()
