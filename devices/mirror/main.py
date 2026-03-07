from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from .camera import MirrorCamera
    from .display import MirrorDisplay
    from .image_generation import MirrorImageGenerator
    from .planner import MirrorInstructionPlanner
except ImportError:
    from camera import MirrorCamera
    from display import MirrorDisplay
    from image_generation import MirrorImageGenerator
    from planner import MirrorInstructionPlanner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Mirror display runtime")
    parser.add_argument("instruction", nargs="?", help="visual instruction from the control plane")
    parser.add_argument("--stdin", action="store_true", help="read the instruction from stdin")
    parser.add_argument("--camera-device", default="/dev/video0", help="camera device path or index")
    parser.add_argument("--width", type=int, default=1080, help="LCD width in pixels")
    parser.add_argument("--height", type=int, default=1920, help="LCD height in pixels")
    parser.add_argument("--hold-seconds", type=int, default=0, help="auto-close the fullscreen window after N seconds")
    parser.add_argument("--skip-camera", action="store_true", help="use a placeholder frame instead of the Pi camera")
    parser.add_argument("--loop", action="store_true", help="keep reading instructions from stdin interactively")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    mirror_dir = Path(__file__).resolve().parent
    output_dir = mirror_dir / "output"

    camera = MirrorCamera(device=args.camera_device)
    planner = MirrorInstructionPlanner()
    generator = MirrorImageGenerator(output_dir=output_dir, display_size=(args.width, args.height))
    display = MirrorDisplay(output_dir=output_dir)

    if args.loop:
        return run_loop(args, camera, planner, generator, display)

    instruction = resolve_instruction(args)
    if not instruction:
        print("No instruction provided. Pass text directly, use --stdin, or use --loop.", file=sys.stderr)
        return 1

    process_instruction(
        instruction=instruction,
        skip_camera=args.skip_camera,
        hold_seconds=args.hold_seconds,
        camera=camera,
        planner=planner,
        generator=generator,
        display=display,
    )
    return 0


def run_loop(args, camera, planner, generator, display) -> int:
    print("Mirror runtime ready. Type an instruction and press Enter. Ctrl-D exits.")
    while True:
        try:
            instruction = input("mirror> ").strip()
        except EOFError:
            print()
            return 0

        if not instruction:
            continue

        process_instruction(
            instruction=instruction,
            skip_camera=args.skip_camera,
            hold_seconds=args.hold_seconds,
            camera=camera,
            planner=planner,
            generator=generator,
            display=display,
        )


def resolve_instruction(args: argparse.Namespace) -> str:
    if args.stdin:
        return sys.stdin.read().strip()
    return (args.instruction or "").strip()


def process_instruction(
    instruction: str,
    skip_camera: bool,
    hold_seconds: int,
    camera: MirrorCamera,
    planner: MirrorInstructionPlanner,
    generator: MirrorImageGenerator,
    display: MirrorDisplay,
) -> None:
    frame = camera.placeholder_frame("camera skipped by operator") if skip_camera else camera.capture()
    plan = planner.plan(instruction)
    result = generator.generate(plan, frame)
    screen_path = display.show(result.image, hold_seconds=hold_seconds)

    print(f"instruction: {instruction}")
    print(f"frame_source: {frame.source}")
    print(f"plan_mode: {plan.display_mode}")
    print(f"image_source: {result.source}")
    if result.api_error:
        print(f"api_error: {result.api_error}")
    print(f"saved_image: {result.saved_path}")
    print(f"display_output: {screen_path}")


if __name__ == "__main__":
    raise SystemExit(main())
