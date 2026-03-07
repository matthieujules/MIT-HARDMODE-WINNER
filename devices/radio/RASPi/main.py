from __future__ import annotations

import argparse
import json
import sys

from runtime import RadioRuntime


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Raspberry Pi runtime for the Radio device")
    parser.add_argument("command", nargs="?", help="radio instruction from the control plane")
    parser.add_argument("--stdin", action="store_true", help="read the command from stdin")
    parser.add_argument("--loop", action="store_true", help="interactive CLI loop for local testing")
    return parser


def resolve_command(args: argparse.Namespace) -> str:
    if args.stdin:
        return sys.stdin.read().strip()
    return (args.command or "").strip()


def main() -> int:
    args = build_parser().parse_args()
    runtime = RadioRuntime.from_repo_defaults()
    try:
        if args.loop:
            return run_loop(runtime)

        command = resolve_command(args)
        if not command:
            print("No command provided. Pass text directly, use --stdin, or use --loop.", file=sys.stderr)
            return 1

        result = runtime.handle_command(command)
        print(json.dumps(result, indent=2))
        return 0
    finally:
        runtime.close()


def run_loop(runtime: RadioRuntime) -> int:
    print("Radio RASPi runtime ready. Type a command and press Enter. Ctrl-D exits.")
    while True:
        try:
            command = input("radio> ").strip()
        except EOFError:
            print()
            return 0

        if not command:
            continue
        result = runtime.handle_command(command)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
