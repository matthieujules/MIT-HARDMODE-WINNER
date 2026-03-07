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
        help="Send the resulting payload to the configured serial port instead of simulating.",
    )
    parser.add_argument(
        "--connect",
        action="store_true",
        help="Run the full WebSocket runtime (register + connect to control plane).",
    )
    return parser


def print_banner(config: dict, simulate: bool) -> None:
    serial_port = config["hardware"]["arm"]["serial"]["port"]
    pins = config["hardware"]["lemp"]["pins"]
    print("Lamp / LEM arm simulator")
    print(f"Mode: {'simulation' if simulate else 'live serial'}")
    print(f"Config: {config['device_name']} ({config['device_id']})")
    print(f"SO-101 serial port: {serial_port}")
    print(
        "LEMP PWM pins: "
        f"R={pins['red']} G={pins['green']} B={pins['blue']}"
    )
    print(
        "Type joint commands like "
        "'base 120 shoulder 80 elbow 130 wrist 70 roll 110 blue'."
    )
    print("Type 'focus', 'relax', 'alert', or 'home' for presets.")
    print("Type 'quit' to exit.")


def process_instruction(
    instruction: str,
    planner: InstructionPlanner,
    controller: LEMHardwareController,
) -> None:
    plan = planner.plan(
        instruction=instruction,
        current_joints=controller.current_joints,
        current_color=controller.current_color,
    )

    print("\nPLAN")
    print(f"Instruction: {plan.raw_instruction}")
    if plan.preset:
        print(f"Preset: {plan.preset}")
    print(f"Joints: {format_joint_map(plan.joints)}")
    print(f"Color: {plan.color} @ brightness={plan.brightness}")
    if plan.light_frames:
        print(f"Light frames: {plan.light_frames}")
    print(f"Duration: {plan.duration_ms} ms")
    print(f"Notes: {', '.join(plan.notes)}")

    payload = controller.apply_plan(plan)
    print(f"Pose preview mm: {payload['pose_preview_mm']}\n")


def format_joint_map(joints: dict[str, float]) -> str:
    return ", ".join(f"{name}={round(value, 2)}" for name, value in joints.items())


def main() -> int:
    args = build_parser().parse_args()
    config = load_config(args.config.resolve())
    simulate = not args.live_serial

    # ── WebSocket runtime mode ────────────────────────────────────
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

    # ── CLI simulator mode (original) ─────────────────────────────
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
