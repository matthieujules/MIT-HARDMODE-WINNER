"""
Import shim for lerobot 0.4.4 (on the Lamp Pi).
"""

from lerobot.motors.feetech import FeetechMotorsBus, TorqueMode
from lerobot.motors.motors_bus import Motor, MotorNormMode

DEFAULT_PORT = "/dev/ttyACM0"

# SO-100 follower motor map (5 joints, no gripper)
MOTOR_NAMES = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll"]
MOTORS = {
    "shoulder_pan": Motor(1, "sts3215", MotorNormMode.RANGE_M100_100),
    "shoulder_lift": Motor(2, "sts3215", MotorNormMode.RANGE_M100_100),
    "elbow_flex": Motor(3, "sts3215", MotorNormMode.RANGE_M100_100),
    "wrist_flex": Motor(4, "sts3215", MotorNormMode.RANGE_M100_100),
    "wrist_roll": Motor(5, "sts3215", MotorNormMode.RANGE_M100_100),
}


def make_bus(port: str = DEFAULT_PORT) -> FeetechMotorsBus:
    bus = FeetechMotorsBus(port=port, motors=MOTORS)
    bus.connect()
    return bus
