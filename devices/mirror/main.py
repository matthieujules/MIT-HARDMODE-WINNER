from __future__ import annotations

import argparse
import asyncio
import logging
import os
import threading
from pathlib import Path

from dotenv import load_dotenv

# Load .env before importing modules that read env vars at import time.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

import yaml

try:
    from .camera import MirrorCamera
    from .display import MirrorDisplay
    from .image_generation import MirrorImageGenerator
    from .planner import MirrorInstructionPlanner
    from .ws_client import run_ws_client
except ImportError:
    from camera import MirrorCamera
    from display import MirrorDisplay
    from image_generation import MirrorImageGenerator
    from planner import MirrorInstructionPlanner
    from ws_client import run_ws_client


DEFAULT_CONFIG = Path(__file__).resolve().with_name("config.yaml")
EXIT_WORDS = {"exit", "quit", "q"}

logger = logging.getLogger(__name__)


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Mirror display device runtime.")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"Path to the device config file. Default: {DEFAULT_CONFIG}",
    )
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        "--once",
        type=str,
        help="Process a single instruction and exit.",
    )
    mode_group.add_argument(
        "--loop",
        action="store_true",
        help="Interactive mode: type instructions at a prompt while the display stays live.",
    )
    mode_group.add_argument(
        "--connect",
        action="store_true",
        help="Run the WebSocket runtime against the control plane.",
    )
    parser.add_argument(
        "--skip-camera",
        action="store_true",
        help="Use placeholder frames instead of a live camera feed.",
    )
    return parser


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )


def build_runtime(
    config: dict,
    *,
    headless: bool,
) -> tuple[MirrorCamera, MirrorInstructionPlanner, MirrorImageGenerator, MirrorDisplay]:
    mirror_dir = Path(__file__).resolve().parent
    output_dir = mirror_dir / "output"

    camera_cfg = config.get("hardware", {}).get("camera", {})
    display_cfg = config.get("hardware", {}).get("display", {})
    camera_res = tuple(camera_cfg.get("resolution", [640, 480]))
    display_res = tuple(display_cfg.get("resolution", [1080, 1920]))

    previous_headless = os.environ.get("MIRROR_HEADLESS")
    if headless:
        os.environ["MIRROR_HEADLESS"] = "1"

    try:
        display = MirrorDisplay(output_dir=output_dir, display_size=display_res, fps=30)
    finally:
        if headless:
            if previous_headless is None:
                os.environ.pop("MIRROR_HEADLESS", None)
            else:
                os.environ["MIRROR_HEADLESS"] = previous_headless

    camera = MirrorCamera(
        device=camera_cfg.get("device", "/dev/video0"),
        capture_size=camera_res,
        on_frame_callback=display.set_camera_frame,
    )
    planner = MirrorInstructionPlanner()
    generator = MirrorImageGenerator(output_dir=output_dir, display_size=display_res)
    return camera, planner, generator, display


def process_instruction(
    instruction: str,
    camera: MirrorCamera,
    planner: MirrorInstructionPlanner,
    generator: MirrorImageGenerator,
    display: MirrorDisplay,
    skip_camera: bool,
) -> dict[str, str]:
    frame = camera.placeholder_frame("camera skipped by operator") if skip_camera else camera.get_frame()
    plan = planner.plan(instruction)
    result = generator.generate(plan, frame)
    screen_path = display.show_generated(result.image, ttl_s=20)

    response = {
        "instruction": instruction,
        "frame_source": frame.source,
        "plan_mode": plan.display_mode,
        "image_source": result.source,
        "saved_image": str(result.saved_path),
        "display_output": str(screen_path),
    }
    if result.api_error:
        response["api_error"] = result.api_error
    return response


def print_instruction_result(result: dict[str, str]) -> None:
    print(f"instruction: {result['instruction']}")
    print(f"frame_source: {result['frame_source']}")
    print(f"plan_mode: {result['plan_mode']}")
    print(f"image_source: {result['image_source']}")
    if "api_error" in result:
        print(f"api_error: {result['api_error']}")
    print(f"saved_image: {result['saved_image']}")
    print(f"display_output: {result['display_output']}")


def run_loop_input_thread(
    camera: MirrorCamera,
    planner: MirrorInstructionPlanner,
    generator: MirrorImageGenerator,
    display: MirrorDisplay,
    stop_event: threading.Event,
    skip_camera: bool,
) -> threading.Thread:
    def _worker() -> None:
        print("Mirror runtime ready. Type an instruction and press Enter.")
        print("Type 'quit' to exit.")
        while not stop_event.is_set():
            try:
                raw = input("\nmirror> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                stop_event.set()
                display.stop()
                return

            if not raw:
                continue
            if raw.lower() in EXIT_WORDS:
                stop_event.set()
                display.stop()
                return

            try:
                result = process_instruction(raw, camera, planner, generator, display, skip_camera)
            except Exception as exc:
                logger.exception("Interactive instruction failed: %s", exc)
                print(f"error: {exc}")
                continue

            print_instruction_result(result)

    thread = threading.Thread(target=_worker, name="mirror-cli", daemon=True)
    thread.start()
    return thread


def run_ws_thread(
    config: dict,
    camera: MirrorCamera,
    planner: MirrorInstructionPlanner,
    generator: MirrorImageGenerator,
    display: MirrorDisplay,
    stop_event: threading.Event,
    skip_camera: bool,
) -> threading.Thread:
    def _worker() -> None:
        try:
            asyncio.run(
                run_ws_client(
                    config,
                    camera,
                    planner,
                    generator,
                    display,
                    skip_camera=skip_camera,
                    stop_event=stop_event,
                )
            )
        except Exception:
            logger.exception("WebSocket thread crashed")
            stop_event.set()
            display.stop()

    thread = threading.Thread(target=_worker, name="mirror-ws", daemon=True)
    thread.start()
    return thread


def main() -> int:
    configure_logging()
    args = build_parser().parse_args()
    config = load_config(args.config.resolve())

    camera, planner, generator, display = build_runtime(
        config,
        headless=bool(args.once),
    )

    if args.skip_camera:
        display.set_camera_frame(camera.placeholder_frame("camera skipped by operator"))
    else:
        camera.start()

    try:
        if args.once:
            result = process_instruction(args.once, camera, planner, generator, display, args.skip_camera)
            print_instruction_result(result)
            return 0

        stop_event = threading.Event()
        background_thread: threading.Thread | None = None

        if args.connect:
            background_thread = run_ws_thread(config, camera, planner, generator, display, stop_event, args.skip_camera)
        elif args.loop:
            background_thread = run_loop_input_thread(camera, planner, generator, display, stop_event, args.skip_camera)

        try:
            display.run()
        except KeyboardInterrupt:
            logger.info("Mirror runtime interrupted")
        finally:
            stop_event.set()
            display.stop()
            if args.connect and background_thread is not None:
                background_thread.join(timeout=5.0)
                if background_thread.is_alive():
                    logger.warning("WebSocket thread did not exit within timeout")
        return 0
    finally:
        camera.stop()


if __name__ == "__main__":
    raise SystemExit(main())
