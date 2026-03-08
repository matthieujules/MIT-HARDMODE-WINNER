from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from dotenv import load_dotenv

# Load .env BEFORE any imports that read env vars at module level (agent.py reads CEREBRAS_API_KEY)
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

import yaml

from hardware import LEMHardwareController
from planner import InstructionPlanner


DEFAULT_CONFIG = Path(__file__).resolve().with_name("config.yaml")
EXIT_WORDS = {"exit", "quit", "q"}


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Simulate the Lamp/LEM arm runtime from typed host instructions."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"Path to the device config file. Default: {DEFAULT_CONFIG}",
    )
    parser.add_argument(
        "--once",
        type=str,
        help="Process a single instruction and exit.",
    )
    parser.add_argument(
        "--live-serial",
        action="store_true",
        help="Use real hardware instead of simulating.",
    )
    parser.add_argument(
        "--connect",
        action="store_true",
        help="Run the full WebSocket runtime (register + connect to control plane).",
    )
    return parser


def print_banner(config: dict, simulate: bool) -> None:
    serial_port = config.get("arm", {}).get("serial_port", "/dev/ttyACM0")
    led_pins = config.get("led", {}).get("pins", {})
    print("Lamp / LEM arm runtime")
    print(f"Mode: {'simulation' if simulate else 'live hardware'}")
    print(f"Config: {config['device_name']} ({config['device_id']})")
    print(f"SO-101 serial port: {serial_port}")
    if led_pins:
        print(
            "LED PWM pins: "
            f"R={led_pins.get('red', '?')} G={led_pins.get('green', '?')} B={led_pins.get('blue', '?')}"
        )
    print("Type pose names like 'home', 'look_at_user', or color names.")
    print("Type 'quit' to exit.")


def process_instruction(
    instruction: str,
    planner: InstructionPlanner,
    controller: LEMHardwareController,
) -> None:
    # Try pose detection
    pose_name = planner.detect_pose(instruction)
    if pose_name and pose_name in controller.poses:
        result = controller.move_to_pose(pose_name)
        print(f"  {result}")
        return

    # Try color detection
    color = planner.parse_color(instruction)
    if color:
        controller.set_color(color["r"], color["g"], color["b"])
        print(f"  Color set to R={color['r']} G={color['g']} B={color['b']}")
        return

    # Try brightness
    brightness = planner.parse_brightness(instruction)
    if brightness is not None:
        controller.set_brightness(brightness)
        print(f"  Brightness set to {brightness}")
        return

    # Try direct pose name
    if instruction.strip() in controller.poses:
        result = controller.move_to_pose(instruction.strip())
        print(f"  {result}")
        return

    print(f"  Unknown instruction: {instruction}")
    print(f"  Available poses: {', '.join(controller.get_pose_names())}")


def main() -> int:
    args = build_parser().parse_args()
    config = load_config(args.config.resolve())
    simulate = not args.live_serial

    # -- WebSocket runtime mode --------------------------------------------
    if args.connect:
        from ws_client import run_ws_client

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(name)s %(levelname)s %(message)s",
        )
        print(f"Lamp runtime: connecting to control plane (simulate={simulate})")
        try:
            asyncio.run(run_ws_client(config, simulate=simulate))
        except KeyboardInterrupt:
            print("\nLamp runtime stopped.")
        return 0

    # -- CLI simulator mode (original) -------------------------------------
    planner = InstructionPlanner(config)
    controller = LEMHardwareController(config, simulate=simulate)

    try:
        print_banner(config, simulate)

        if args.once:
            process_instruction(args.once, planner, controller)
            return 0

        while True:
            raw = input("\nInstruction> ").strip()
            if not raw:
                continue
            if raw.lower() in EXIT_WORDS:
                return 0
            process_instruction(raw, planner, controller)
    finally:
        controller.close()


if __name__ == "__main__":
    raise SystemExit(main())
