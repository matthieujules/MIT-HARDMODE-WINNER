#!/usr/bin/env python3
"""Test hardware.py in isolation on the Pi.

Usage:
    python3 test_hardware.py                # run all tests
    python3 test_hardware.py --sim          # sim mode (no real hardware)
    python3 test_hardware.py --test pose    # run only pose test
    python3 test_hardware.py --test led     # run only LED test
    python3 test_hardware.py --test flash   # run only flash test
    python3 test_hardware.py --test pulse   # run only pulse test
    python3 test_hardware.py --test agent   # run agent tool execution test
"""

import argparse
import json
import sys
import time
from pathlib import Path

import yaml

# Ensure we can import from the lamp directory
sys.path.insert(0, str(Path(__file__).parent))

CONFIG_PATH = Path(__file__).parent / "config.yaml"


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def test_poses(hw):
    """Test moving to all available poses."""
    print("\n=== POSE TEST ===")
    poses = hw.get_pose_names()
    print(f"Available poses: {poses}")

    for name in poses:
        print(f"\nMoving to '{name}'...")
        result = hw.move_to_pose(name)
        print(f"  Result: {result}")
        print(f"  Joints: {json.dumps({k: round(v, 2) for k, v in hw.current_joints.items()})}")
        time.sleep(1.0)

    print("\n  Pose test complete.")


def test_led(hw):
    """Test LED color control."""
    print("\n=== LED TEST ===")

    colors = [
        ("Red", 255, 0, 0),
        ("Green", 0, 255, 0),
        ("Blue", 0, 0, 255),
        ("Warm white", 255, 180, 120),
    ]

    for name, r, g, b in colors:
        print(f"  Setting {name} ({r}, {g}, {b})...")
        hw.set_color(r, g, b)
        time.sleep(0.8)

    print("  Testing brightness...")
    hw.set_color(255, 255, 255)
    for b in [1.0, 0.5, 0.2, 0.05, 0.5, 1.0]:
        print(f"    Brightness {b}")
        hw.set_brightness(b)
        time.sleep(0.5)

    # Restore default
    hw.set_color(255, 180, 120)
    hw.set_brightness(1.0)
    print("  LED test complete.")


def test_flash(hw):
    """Test flash effect."""
    print("\n=== FLASH TEST ===")
    print("  Setting base color to warm white...")
    hw.set_color(255, 180, 120)
    time.sleep(0.5)

    print("  Flashing red (500ms)...")
    hw.flash(255, 0, 0, 500)
    print(f"  After flash, color: {hw.current_color}")
    time.sleep(0.5)

    print("  Flashing blue (300ms)...")
    hw.flash(0, 0, 255, 300)
    print(f"  After flash, color: {hw.current_color}")
    print("  Flash test complete.")


def test_pulse(hw):
    """Test pulse effect."""
    print("\n=== PULSE TEST ===")
    print("  Pulsing blue (3 cycles, 800ms period)...")
    hw.pulse(0, 100, 255, cycles=3, period_ms=800)
    print(f"  After pulse, color: {hw.current_color}")
    print("  Pulse test complete.")


def test_agent_tools(hw):
    """Test agent tool execution directly."""
    print("\n=== AGENT TOOL TEST ===")
    from agent import _execute_tool_call

    tests = [
        ("set_color", {"r": 0, "g": 255, "b": 100}),
        ("set_brightness", {"brightness": 0.7}),
        ("pose", {"name": "home"}),
        ("flash", {"r": 255, "g": 0, "b": 0, "duration_ms": 300}),
        ("pulse", {"r": 0, "g": 100, "b": 255, "cycles": 2, "period_ms": 600}),
        ("set_color", {"r": 255, "g": 180, "b": 120}),
        ("set_brightness", {"brightness": 1.0}),
        ("done", {"detail": "test complete"}),
    ]

    for name, args in tests:
        print(f"  {name}({args})...")
        result = _execute_tool_call(name, args, hw)
        print(f"    -> {result}")
        time.sleep(0.5)

    print("  Agent tool test complete.")


def main():
    parser = argparse.ArgumentParser(description="Test lamp hardware in isolation")
    parser.add_argument("--sim", action="store_true", help="Run in sim mode")
    parser.add_argument("--test", type=str, help="Run specific test: pose, led, flash, pulse, agent")
    args = parser.parse_args()

    config = load_config()
    simulate = args.sim
    print(f"Hardware test (simulate={simulate})")

    from hardware import LEMHardwareController
    hw = LEMHardwareController(config, simulate=simulate)

    print(f"Poses loaded: {hw.get_pose_names()}")
    print(f"Initial joints: {json.dumps({k: round(v, 2) for k, v in hw.current_joints.items()})}")
    print(f"Initial color: {hw.current_color}")

    try:
        if args.test:
            test_map = {
                "pose": test_poses,
                "led": test_led,
                "flash": test_flash,
                "pulse": test_pulse,
                "agent": test_agent_tools,
            }
            test_fn = test_map.get(args.test)
            if test_fn:
                test_fn(hw)
            else:
                print(f"Unknown test: {args.test}. Available: {', '.join(test_map.keys())}")
                return 1
        else:
            test_poses(hw)
            test_led(hw)
            test_flash(hw)
            test_pulse(hw)
            test_agent_tools(hw)

        print("\nAll tests passed.")
        return 0
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 1
    finally:
        hw.close()


if __name__ == "__main__":
    sys.exit(main())
