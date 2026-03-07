from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from light_controller import LEMPLightController


DEFAULT_CONFIG = Path(__file__).resolve().with_name("config.yaml")


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def parse_frames(raw: str) -> list[tuple[int, int, int, int]]:
    frames: list[tuple[int, int, int, int]] = []
    for chunk in raw.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = [part.strip() for part in chunk.split(",")]
        if len(parts) != 4:
            raise ValueError(
                "Animation frames must use 'r,g,b,t_ms;r,g,b,t_ms;...'"
            )
        r, g, b, t_ms = (int(value) for value in parts)
        frames.append((r, g, b, t_ms))
    return frames


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Test Lamp/LEMP RGB PWM output without involving the arm controller."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"Path to Lamp config. Default: {DEFAULT_CONFIG}",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Drive real GPIO PWM instead of simulation output.",
    )
    parser.add_argument(
        "--brightness",
        type=float,
        default=1.0,
        help="Brightness scale from 0.0 to 1.0.",
    )
    parser.add_argument(
        "--loop-count",
        type=int,
        default=1,
        help="Loop count for animation playback.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    rgb_parser = subparsers.add_parser("rgb", help="Set a single RGB value.")
    rgb_parser.add_argument("r", type=int)
    rgb_parser.add_argument("g", type=int)
    rgb_parser.add_argument("b", type=int)

    anim_parser = subparsers.add_parser("animate", help="Play an RGB frame sequence.")
    anim_parser.add_argument(
        "frames",
        type=str,
        help="Frame string in the format 'r,g,b,t_ms;r,g,b,t_ms;...'",
    )

    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_config(args.config.resolve())
    controller = LEMPLightController(config["hardware"]["lemp"], simulate=not args.live)
    try:
        if args.command == "rgb":
            controller.set_rgb(
                {"r": args.r, "g": args.g, "b": args.b},
                brightness=args.brightness,
            )
            return 0

        if args.command == "animate":
            controller.play_frames(
                parse_frames(args.frames),
                brightness=args.brightness,
                loop_count=args.loop_count,
            )
            return 0

        raise SystemExit(f"Unsupported command: {args.command}")
    finally:
        controller.close()


if __name__ == "__main__":
    raise SystemExit(main())
