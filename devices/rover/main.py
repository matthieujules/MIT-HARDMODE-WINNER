from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from dotenv import load_dotenv

# Load .env BEFORE any imports that read env vars at module level (agent.py reads CEREBRAS_API_KEY)
# Try local .env first (Pi deployment), then project root (laptop dev)
_here = Path(__file__).resolve().parent
load_dotenv(_here / ".env")
load_dotenv(_here.parents[1] / ".env")

import yaml


DEFAULT_CONFIG = Path(__file__).resolve().with_name("config.yaml")
EXIT_WORDS = {"exit", "quit", "q"}

# Motion is imported LAZILY — not at startup.
# motion.py starts encoder polling threads on import which burn CPU.
# Only import when we actually need to execute motor commands.
_motion = None
_motion_loaded = False


def _get_motion():
    global _motion, _motion_loaded
    if not _motion_loaded:
        _motion_loaded = True
        try:
            import motion as m
            _motion = m
        except Exception:
            _motion = None
    return _motion


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rover device runtime — small mobile coaster."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"Path to the device config file. Default: {DEFAULT_CONFIG}",
    )
    parser.add_argument(
        "--connect",
        action="store_true",
        help="Run the full WebSocket runtime (register + connect to control plane).",
    )
    parser.add_argument(
        "--once",
        type=str,
        help="Process a single instruction and exit.",
    )
    return parser


def print_banner(config: dict, sim: bool) -> None:
    print("Rover runtime")
    print(f"Mode: {'simulation' if sim else 'live hardware'}")
    print(f"Config: {config['device_name']} ({config['device_id']})")
    if sim:
        print("Hardware: lgpio not available — running in sim mode")
    print("Commands: move <cm>, rotate <deg>, stop, excited, sad, ponder, deliver")
    print("Type 'quit' to exit.")


def process_instruction(instruction: str) -> None:
    """Process a CLI instruction in sim or live mode."""
    text = instruction.strip().lower()
    _motion = _get_motion()

    if _motion is None:
        print(f"  [SIM] Would execute: {text}")
        return

    if text == "stop":
        _motion.stop()
        print("  Stopped.")
    elif text.startswith("move"):
        parts = text.split()
        cm = float(parts[1]) if len(parts) > 1 else 10
        speed = int(parts[2]) if len(parts) > 2 else 40
        _motion.move(cm, speed)
        print(f"  Moved {cm}cm at speed {speed}")
    elif text.startswith("rotate") or text.startswith("turn"):
        parts = text.split()
        deg = float(parts[1]) if len(parts) > 1 else 90
        speed = int(parts[2]) if len(parts) > 2 else 40
        _motion.rotate(deg, speed)
        print(f"  Rotated {deg} degrees at speed {speed}")
    elif text in ("excited", "excitement", "spin"):
        _motion.excitement()
        print("  Excitement routine done.")
    elif text in ("sad",):
        _motion.act_sad()
        print("  Sad routine done.")
    elif text in ("ponder", "think"):
        _motion.say_no()
        print("  Ponder routine done.")
    elif text in ("deliver", "food", "pass"):
        _motion.pass_food()
        print("  Delivery routine done.")
    else:
        print(f"  Unknown: {text}")
        print("  Try: move <cm>, rotate <deg>, stop, excited, sad, ponder, deliver")


def main() -> int:
    args = build_parser().parse_args()
    config = load_config(args.config.resolve())

    # -- WebSocket runtime mode ------------------------------------------------
    if args.connect:
        from ws_client import run_ws_client

        # Don't import motion here — ws_client/agent lazy-load it when needed
        sim = _get_motion() is None

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(name)s %(levelname)s %(message)s",
        )
        print(f"Rover runtime: connecting to control plane (sim={sim})")
        try:
            asyncio.run(run_ws_client(config, simulate=sim))  # motion imported lazily by agent/ws_client
        except KeyboardInterrupt:
            print("\nRover runtime stopped.")
        finally:
            m = _get_motion()
            if m is not None:
                try:
                    m.cleanup()
                except Exception:
                    pass
        return 0

    # -- CLI simulator mode ----------------------------------------------------
    sim = _get_motion() is None
    try:
        print_banner(config, sim)

        if args.once:
            process_instruction(args.once)
            return 0

        while True:
            raw = input("\nRover> ").strip()
            if not raw:
                continue
            if raw.lower() in EXIT_WORDS:
                return 0
            process_instruction(raw)
    finally:
        m = _get_motion()
        if m is not None:
            try:
                m.cleanup()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
