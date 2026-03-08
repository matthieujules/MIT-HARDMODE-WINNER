#!/usr/bin/env python3
"""
Calibrate the SO-100 follower arm using lerobot's built-in robot calibration.

Usage:
    python calibrate.py                          # calibrate using so100_follower
    python calibrate.py --port /dev/ttyACM1      # override serial port
"""

import argparse
from lerobot.robots import so_follower, make_robot_from_config

DEFAULT_PORT = "/dev/ttyACM0"


def main():
    parser = argparse.ArgumentParser(description="Calibrate the SO-100 arm")
    parser.add_argument("--port", default=DEFAULT_PORT)
    args = parser.parse_args()

    config = so_follower.SO100FollowerConfig(port=args.port)
    robot = make_robot_from_config(config)
    robot.connect(calibrate=False)

    try:
        print("Starting calibration...")
        robot.calibrate()
        print("Calibration complete.")
    finally:
        robot.disconnect()


if __name__ == "__main__":
    main()
