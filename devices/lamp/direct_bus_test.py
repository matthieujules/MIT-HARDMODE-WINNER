from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import yaml


def load_lerobot_classes():
    try:
        from lerobot.motors import Motor, MotorCalibration, MotorNormMode
        from lerobot.motors.feetech import FeetechMotorsBus
        return FeetechMotorsBus, Motor, MotorCalibration, MotorNormMode
    except Exception:
        from lerobot.common.robot_devices.motors.feetech import FeetechMotorsBus
        from lerobot.common.robot_devices.motors.utils import Motor, MotorCalibration, MotorNormMode
        return FeetechMotorsBus, Motor, MotorCalibration, MotorNormMode


DEFAULT_CONFIG = Path(__file__).resolve().with_name("config.yaml")


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_calibration(config: dict) -> dict:
    calibration_path = Path(
        config["hardware"]["arm"]["lerobot"]["calibration_path"]
    ).expanduser()
    with calibration_path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)

    remapped = {}
    for joint_name, joint in config["hardware"]["arm"]["joints"].items():
        calibration_name = joint.get("calibration_name", joint_name)
        remapped[joint_name] = raw[calibration_name]
    return remapped


def build_bus(config: dict):
    FeetechMotorsBus, Motor, MotorCalibration, MotorNormMode = load_lerobot_classes()
    arm_config = config["hardware"]["arm"]
    motors = {
        joint_name: Motor(
            int(joint["servo_id"]),
            arm_config.get("motor_model", "sts3215"),
            getattr(MotorNormMode, arm_config.get("norm_mode", "RANGE_M100_100")),
        )
        for joint_name, joint in arm_config["joints"].items()
    }
    calibration = {
        joint_name: MotorCalibration(**values)
        for joint_name, values in load_calibration(config).items()
    }
    return FeetechMotorsBus(
        port=arm_config["serial"]["port"],
        motors=motors,
        calibration=calibration,
    )


def configure_bus(bus) -> None:
    motor_names = list(bus.motors.keys())
    bus.write("Torque_Enable", motor_names, 0)
    try:
        bus.write("Mode", motor_names, 0)
    except Exception:
        pass
    for register, value in (
        ("P_Coefficient", 4),
        ("I_Coefficient", 0),
        ("D_Coefficient", 32),
        ("Lock", 0),
        ("Maximum_Acceleration", 50),
        ("Acceleration", 50),
    ):
        try:
            bus.write(register, motor_names, value)
        except Exception:
            pass
    bus.write("Torque_Enable", motor_names, 1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Direct Feetech bus movement test for Lamp.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--sleep-s", type=float, default=2.0)
    parser.add_argument("--base-yaw", type=float, default=59.695)
    parser.add_argument("--shoulder-pitch", type=float, default=111.564)
    parser.add_argument("--elbow", type=float, default=48.004)
    parser.add_argument("--wrist-pitch", type=float, default=30.026)
    parser.add_argument("--wrist-roll", type=float, default=180.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_config(args.config.resolve())
    bus = build_bus(config)

    target = {
        "base_yaw": args.base_yaw,
        "shoulder_pitch": args.shoulder_pitch,
        "elbow": args.elbow,
        "wrist_pitch": args.wrist_pitch,
        "wrist_roll": args.wrist_roll,
    }

    bus.connect()
    try:
        configure_bus(bus)
        print("before:", bus.sync_read("Present_Position"))
        print("target:", target)
        bus.sync_write("Goal_Position", target)
        time.sleep(args.sleep_s)
        print("after:", bus.sync_read("Present_Position"))
    finally:
        bus.disconnect()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
