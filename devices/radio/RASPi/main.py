from __future__ import annotations

import argparse
import json
import sys
import threading
from pathlib import Path

# -- dotenv MUST load before importing agent (reads CEREBRAS_API_KEY at import time) --
from dotenv import load_dotenv

_here = Path(__file__).resolve().parent
_radio_dir = _here.parent  # devices/radio/ (repo) or same dir (flat deploy)

load_dotenv(_here / ".env")                # Pi flat deploy (files in ~/Radio/)
load_dotenv(_radio_dir / ".env")           # Repo: devices/radio/.env
load_dotenv(_here.parents[2] / ".env")     # Repo: project root .env

# Ensure both directories are importable (repo: RASPi/ + radio/, flat: same dir)
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))
if str(_radio_dir) not in sys.path:
    sys.path.insert(0, str(_radio_dir))

from runtime import RadioRuntime


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Raspberry Pi runtime for the Radio device")
    parser.add_argument("command", nargs="?", help="radio instruction from the control plane")
    parser.add_argument("--stdin", action="store_true", help="read the command from stdin")
    parser.add_argument("--loop", action="store_true", help="interactive CLI loop for local testing")
    parser.add_argument("--connect", action="store_true", help="run the full WebSocket runtime (register + connect to control plane)")
    parser.add_argument("--dial-test", action="store_true", help="run a safe clockwise/counterclockwise dial test")
    parser.add_argument("--dial-seconds", type=float, default=0.2, help="seconds per dial movement in --dial-test")
    parser.add_argument("--dial-repeats", type=int, default=1, help="how many clockwise/counterclockwise test cycles")
    return parser


def resolve_command(args: argparse.Namespace) -> str:
    if args.stdin:
        return sys.stdin.read().strip()
    return (args.command or "").strip()


def main() -> int:
    import logging

    args = build_parser().parse_args()

    # -- WebSocket runtime mode ------------------------------------------------
    if args.connect:
        import asyncio
        from ws_client import load_config, run_ws_client

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(name)s %(levelname)s %(message)s",
        )

        config_path = Path(__file__).resolve().with_name("config.yaml")
        if not config_path.exists():
            # Flat deploy: config.yaml alongside main.py
            config_path = _here / "config.yaml"
        if not config_path.exists():
            # Repo: devices/radio/config.yaml
            config_path = _radio_dir / "config.yaml"

        config = load_config(config_path)
        print(f"Radio runtime: connecting to control plane")
        try:
            asyncio.run(run_ws_client(config))
        except KeyboardInterrupt:
            print("\nRadio runtime stopped.")
        return 0

    # -- Original modes --------------------------------------------------------
    runtime = RadioRuntime.from_repo_defaults()
    try:
        if args.dial_test:
            return run_dial_test(runtime, max(0.05, args.dial_seconds), max(1, args.dial_repeats))

        if args.loop:
            return run_loop(runtime)

        command = resolve_command(args)
        if not command:
            print("No command provided. Pass text directly, use --stdin, --loop, or --connect.", file=sys.stderr)
            return 1

        result = runtime.handle_command(command)
        print(json.dumps(result, indent=2))
        return 0
    finally:
        runtime.close()


def run_loop(runtime: RadioRuntime) -> int:
    print("Radio RASPi runtime ready. Type commands anytime; new command interrupts current playback. Ctrl-D exits.")

    latest_command: str | None = None
    command_lock = threading.Lock()
    command_event = threading.Event()
    shutdown_event = threading.Event()

    def worker() -> None:
        nonlocal latest_command
        while not shutdown_event.is_set():
            command_event.wait(timeout=0.1)
            if shutdown_event.is_set():
                return
            with command_lock:
                command = latest_command
                latest_command = None
                command_event.clear()
            if not command:
                continue
            result = runtime.handle_command(command)
            print(json.dumps(result, indent=2))

    worker_thread = threading.Thread(target=worker, daemon=True)
    worker_thread.start()

    while True:
        try:
            command = input("radio> ").strip()
        except EOFError:
            print()
            shutdown_event.set()
            runtime.interrupt_playback()
            command_event.set()
            worker_thread.join(timeout=2.0)
            return 0

        if not command:
            continue

        with command_lock:
            latest_command = command
            command_event.set()
        runtime.interrupt_playback()


def run_dial_test(runtime: RadioRuntime, seconds: float, repeats: int) -> int:
    print(f"Running dial test: repeats={repeats}, seconds={seconds:.2f}")
    for _ in range(repeats):
        runtime.dial.nudge_clockwise(duration_seconds=seconds)
        runtime.dial.nudge_counterclockwise(duration_seconds=seconds)
    print(json.dumps({"dial_history": [event.__dict__ for event in runtime.dial.history()]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
