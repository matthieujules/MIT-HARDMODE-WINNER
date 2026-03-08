from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import yaml

from hardware import LEMHardwareController
from lamp_robot import LerobotLampPlayer


DEFAULT_CONFIG = Path(__file__).resolve().with_name("config.yaml")
DEFAULT_POSES = Path(__file__).resolve().with_name("poses.json")
DEFAULT_NAMES = ("home", "look_at_user")


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_pose_store(path: Path, config: dict) -> dict:
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    joint_names = list(config["hardware"]["arm"]["joints"].keys())
    return {
        "version": 1,
        "joint_names": joint_names,
        "poses": {},
    }


def save_pose_store(path: Path, pose_store: dict) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(pose_store, handle, indent=2, sort_keys=True)
        handle.write("\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read, save, and replay Lamp/LEM arm poses."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"Path to Lamp config. Default: {DEFAULT_CONFIG}",
    )
    parser.add_argument(
        "--poses",
        type=Path,
        default=DEFAULT_POSES,
        help=f"Path to pose JSON file. Default: {DEFAULT_POSES}",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Use the controller's in-memory pose instead of live serial readback.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("print", help="Print current joint locations.")
    subparsers.add_parser("debug_print", help="Print normalized joint angles and raw bus positions.")

    save_parser = subparsers.add_parser("save", help="Save current joint locations as a named pose.")
    save_parser.add_argument("name", choices=DEFAULT_NAMES, help="Pose name to save.")

    capture_parser = subparsers.add_parser(
        "capture",
        help="Disable torque, let you hand-place the arm, then save a named pose.",
    )
    capture_parser.add_argument("name", choices=DEFAULT_NAMES, help="Pose name to save.")

    subparsers.add_parser("list", help="List saved poses.")
    subparsers.add_parser("torque_on", help="Enable servo torque.")
    subparsers.add_parser("torque_off", help="Disable servo torque for hand-guiding.")

    play_parser = subparsers.add_parser("play", help="Replay a saved pose.")
    play_parser.add_argument("name", choices=DEFAULT_NAMES, help="Pose name to replay.")
    play_parser.add_argument("--duration-ms", type=int, default=4000, help="Move duration in milliseconds.")
    play_parser.add_argument(
        "--raw",
        action="store_true",
        help="Replay saved raw bus positions instead of normalized joint angles.",
    )

    interp_parser = subparsers.add_parser(
        "interpolate",
        help="Replay an interpolated path between home and look_at_user.",
    )
    interp_parser.add_argument("--steps", type=int, default=20, help="Number of interpolation frames.")
    interp_parser.add_argument("--segment-ms", type=int, default=180, help="Per-frame duration in milliseconds.")
    interp_parser.add_argument(
        "--loop-count",
        type=int,
        default=1,
        help="How many times to run home -> look_at_user -> home.",
    )
    interp_parser.add_argument(
        "--raw",
        action="store_true",
        help="Replay saved raw bus positions instead of normalized joint angles.",
    )

    return parser


def read_current_pose(controller: LEMHardwareController) -> dict[str, float]:
    joints = controller.read_current_joints()
    print("Current joints:")
    for joint_name, angle in joints.items():
        print(f"  {joint_name}: {round(angle, 3)}")
    return joints


def debug_current_pose(controller: LEMHardwareController) -> None:
    joints = controller.read_current_joints()
    raw_positions = controller.read_current_bus_positions()
    print("Current joints:")
    for joint_name, angle in joints.items():
        print(f"  {joint_name}: {round(angle, 3)}")
    print("Raw bus positions:")
    for joint_name, position in raw_positions.items():
        print(f"  {joint_name}: {round(position, 3)}")


def save_named_pose(
    pose_name: str,
    controller: LEMHardwareController,
    pose_store: dict,
    poses_path: Path,
) -> None:
    joints = read_current_pose(controller)
    raw_positions = controller.read_current_bus_positions()
    pose_store.setdefault("poses", {})[pose_name] = {
        "saved_at_epoch_s": round(time.time(), 3),
        "joints": joints,
        "raw_positions": raw_positions,
    }
    save_pose_store(poses_path, pose_store)
    print(f"Saved pose '{pose_name}' to {poses_path}")


def capture_named_pose(
    pose_name: str,
    controller: LEMHardwareController,
    pose_store: dict,
    poses_path: Path,
) -> None:
    print("Disabling torque so you can place the arm by hand.")
    controller.disable_torque()
    try:
        input(f"Move the arm to '{pose_name}', then press Enter to capture. ")
        save_named_pose(pose_name, controller, pose_store, poses_path)
    finally:
        print("Re-enabling torque.")
        controller.enable_torque()


def print_saved_poses(pose_store: dict) -> None:
    poses = pose_store.get("poses", {})
    if not poses:
        print("No saved poses yet.")
        return

    for pose_name, pose_data in poses.items():
        print(f"{pose_name}:")
        for joint_name, angle in pose_data.get("joints", {}).items():
            print(f"  {joint_name}: {round(float(angle), 3)}")


def require_pose(pose_store: dict, pose_name: str) -> dict[str, float]:
    pose = pose_store.get("poses", {}).get(pose_name)
    if pose is None:
        raise SystemExit(f"Pose '{pose_name}' is not saved yet.")
    return {
        str(joint_name): float(angle)
        for joint_name, angle in pose.get("joints", {}).items()
    }


def require_raw_positions(pose_store: dict, pose_name: str) -> dict[str, float] | None:
    pose = pose_store.get("poses", {}).get(pose_name)
    if pose is None:
        raise SystemExit(f"Pose '{pose_name}' is not saved yet.")
    raw_positions = pose.get("raw_positions")
    if not raw_positions:
        return None
    return {
        str(joint_name): float(position)
        for joint_name, position in raw_positions.items()
    }


def build_play_payload(config: dict, joints: dict[str, float], duration_ms: int) -> dict:
    joint_config = config["hardware"]["arm"]["joints"]
    return {
        "mode": "joints",
        "duration_ms": int(duration_ms),
        "joints": [
            {
                "name": joint_name,
                "servo_id": int(joint_config[joint_name]["servo_id"]),
                "angle_deg": round(float(joints[joint_name]), 3),
            }
            for joint_name in joint_config
        ],
    }


def build_raw_play_payload(positions: dict[str, float], duration_ms: int) -> dict:
    return {
        "mode": "raw_positions",
        "duration_ms": int(duration_ms),
        "raw_positions": {
            joint_name: round(float(position), 3)
            for joint_name, position in positions.items()
        },
    }


def interpolate_poses(
    config: dict,
    start_joints: dict[str, float],
    end_joints: dict[str, float],
    steps: int,
) -> list[dict[str, float]]:
    joint_names = list(config["hardware"]["arm"]["joints"].keys())
    if steps < 2:
        return [{joint_name: float(end_joints[joint_name]) for joint_name in joint_names}]

    frames: list[dict[str, float]] = []
    for index in range(steps):
        alpha = index / (steps - 1)
        frame = {
            joint_name: round(
                float(start_joints[joint_name])
                + (float(end_joints[joint_name]) - float(start_joints[joint_name])) * alpha,
                3,
            )
            for joint_name in joint_names
        }
        frames.append(frame)
    return frames


def play_pose(
    pose_name: str,
    player: LerobotLampPlayer,
    pose_store: dict,
    config: dict,
    duration_ms: int,
    use_raw: bool = False,
) -> None:
    joints = require_pose(pose_store, pose_name)
    raw_positions = require_raw_positions(pose_store, pose_name)
    if use_raw:
        if raw_positions is None:
            raise SystemExit(
                f"Pose '{pose_name}' does not include raw_positions. Save or capture it again before using --raw."
            )
        player.play_raw_pose(positions=raw_positions, duration_ms=duration_ms)
        payload = build_raw_play_payload(raw_positions, duration_ms)
    else:
        player.play_pose(joints=joints, duration_ms=duration_ms)
        payload = build_play_payload(config, joints, duration_ms)
    print(f"Played pose '{pose_name}'")
    print(json.dumps(payload, indent=2, sort_keys=True))


def play_interpolation(
    player: LerobotLampPlayer,
    pose_store: dict,
    config: dict,
    steps: int,
    segment_ms: int,
    loop_count: int,
    use_raw: bool = False,
) -> None:
    home = require_pose(pose_store, "home")
    look_at_user = require_pose(pose_store, "look_at_user")
    home_raw = require_raw_positions(pose_store, "home")
    look_at_user_raw = require_raw_positions(pose_store, "look_at_user")

    for loop_index in range(loop_count):
        print(f"Interpolation loop {loop_index + 1}/{loop_count}")
        if use_raw:
            if home_raw is None or look_at_user_raw is None:
                raise SystemExit(
                    "Saved poses do not include raw_positions. Save or capture both poses again before using --raw."
                )
            forward = interpolate_poses(config, home_raw, look_at_user_raw, steps)
            backward = interpolate_poses(config, look_at_user_raw, home_raw, steps)

            for frame in forward:
                player.play_raw_pose(positions=frame, duration_ms=segment_ms)
            for frame in backward[1:]:
                player.play_raw_pose(positions=frame, duration_ms=segment_ms)
            continue

        forward = interpolate_poses(config, home, look_at_user, steps)
        backward = interpolate_poses(config, look_at_user, home, steps)

        for frame in forward:
            player.play_pose(joints=frame, duration_ms=segment_ms)
        for frame in backward[1:]:
            player.play_pose(joints=frame, duration_ms=segment_ms)


def main() -> int:
    args = build_parser().parse_args()
    config = load_config(args.config.resolve())
    pose_store = load_pose_store(args.poses.resolve(), config)
    if args.command == "list":
        print_saved_poses(pose_store)
        return 0

    if args.command in {"play", "interpolate"}:
        player = LerobotLampPlayer(config)
        try:
            player.connect()
            if args.command == "play":
                play_pose(args.name, player, pose_store, config, args.duration_ms, use_raw=args.raw)
                return 0

            play_interpolation(
                player=player,
                pose_store=pose_store,
                config=config,
                steps=args.steps,
                segment_ms=args.segment_ms,
                loop_count=args.loop_count,
                use_raw=args.raw,
            )
            return 0
        finally:
            player.disconnect()

    controller = LEMHardwareController(
        config,
        simulate=args.simulate,
        enable_light=False,
    )
    try:
        if args.command == "print":
            read_current_pose(controller)
            return 0

        if args.command == "debug_print":
            debug_current_pose(controller)
            return 0

        if args.command == "save":
            save_named_pose(args.name, controller, pose_store, args.poses.resolve())
            return 0

        if args.command == "capture":
            capture_named_pose(args.name, controller, pose_store, args.poses.resolve())
            return 0

        if args.command == "torque_on":
            controller.enable_torque()
            print("Torque enabled.")
            return 0

        if args.command == "torque_off":
            controller.disable_torque()
            print("Torque disabled.")
            return 0

        raise SystemExit(f"Unsupported command: {args.command}")
    finally:
        controller.close()


if __name__ == "__main__":
    raise SystemExit(main())
