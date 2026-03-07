"""Vision capture service for ClaudeHome.

Sidecar process: captures camera, tracks the user and rover for spatial updates,
and sends motion-gated frames to control plane for Claude Vision analysis.

Usage:
    python3 -m vision.vision_service [--camera 0] [--control-plane-url http://localhost:8000]
    python3 -m vision.vision_service --calibrate   # click 4 room corners
    python3 -m vision.vision_service --test        # send a single test frame and exit

Environment:
    CONTROL_PLANE_URL  -- default http://localhost:8000
    VISION_CAMERA      -- camera index (default 0)
"""

import argparse
import base64
import json
import logging
import math
import os
import sys
import threading
import time
from pathlib import Path

import cv2
import httpx
import numpy as np
import torch
from torchvision.models.detection import fasterrcnn_mobilenet_v3_large_320_fpn

try:
    from torchvision.models.detection import FasterRCNN_MobileNet_V3_Large_320_FPN_Weights
except ImportError:  # pragma: no cover - older torchvision fallback
    FasterRCNN_MobileNet_V3_Large_320_FPN_Weights = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s [vision] %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────

CONTROL_PLANE_URL = os.getenv("CONTROL_PLANE_URL", "http://localhost:8000")
CAMERA_INDEX = int(os.getenv("VISION_CAMERA", "0"))
CALIBRATION_PATH = Path("data/vision_calibration.json")
ROOM_CONFIG_PATH = Path("data/room.json")
FRAME_COOLDOWN = 8  # seconds between Claude Vision captures
MOTION_THRESHOLD = 25
MOTION_MIN_AREA = 500
JPEG_QUALITY = 80
PERSON_DETECT_INTERVAL = 0.25
SPATIAL_POST_INTERVAL = 0.25
SPATIAL_MIN_MOVE_CM = 5.0
ROVER_MIN_CONTOUR_AREA = 150.0
CALIBRATION_WINDOW = "ClaudeHome Calibration"

ROVER_COLOR_PRESETS = {
    "green": {
        "lower": np.array([35, 100, 100], dtype=np.uint8),
        "upper": np.array([85, 255, 255], dtype=np.uint8),
    },
    "orange": {
        "lower": np.array([5, 120, 120], dtype=np.uint8),
        "upper": np.array([25, 255, 255], dtype=np.uint8),
    },
    "blue": {
        "lower": np.array([90, 120, 80], dtype=np.uint8),
        "upper": np.array([130, 255, 255], dtype=np.uint8),
    },
}

CORNER_LABELS = ("TL", "TR", "BR", "BL")


# ── Calibration ──────────────────────────────────────────────────

def load_room_config(control_plane_url: str | None = None) -> dict:
    """Load room dimensions from disk, with control-plane fallback."""
    if ROOM_CONFIG_PATH.exists():
        try:
            return json.loads(ROOM_CONFIG_PATH.read_text())
        except Exception as e:
            logger.warning("Failed to read %s: %s", ROOM_CONFIG_PATH, e)

    if control_plane_url:
        try:
            resp = httpx.get(f"{control_plane_url}/room", timeout=5.0)
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.warning("Failed to fetch room config: %s", e)

    return {"width_cm": 500, "height_cm": 400}


def load_calibration() -> dict | None:
    """Load saved calibration from disk."""
    if not CALIBRATION_PATH.exists():
        return None

    try:
        data = json.loads(CALIBRATION_PATH.read_text())
        matrix = data.get("perspective_transform") or data.get("homography")
        if matrix is None:
            raise ValueError("missing perspective_transform")
        data["perspective_transform"] = np.array(matrix, dtype=np.float32)
        logger.info("Loaded calibration from %s", CALIBRATION_PATH)
        return data
    except Exception as e:
        logger.warning("Failed to load calibration: %s", e)
        return None


def save_calibration(data: dict) -> None:
    """Save calibration to disk."""
    CALIBRATION_PATH.parent.mkdir(parents=True, exist_ok=True)
    save_data = dict(data)
    matrix = save_data.get("perspective_transform")
    if isinstance(matrix, np.ndarray):
        save_data["perspective_transform"] = matrix.tolist()
    CALIBRATION_PATH.write_text(json.dumps(save_data, indent=2))
    logger.info("Saved calibration to %s", CALIBRATION_PATH)


def build_perspective_transform(clicked_points: list[tuple[int, int]], room_config: dict) -> dict:
    """Build a perspective transform from clicked room corners."""
    w = float(room_config.get("width_cm", 500))
    h = float(room_config.get("height_cm", 400))

    src_pts = np.array(clicked_points, dtype=np.float32)
    dst_pts = np.array([
        [0, 0],
        [w, 0],
        [w, h],
        [0, h],
    ], dtype=np.float32)

    perspective_transform = cv2.getPerspectiveTransform(src_pts, dst_pts)
    return {
        "perspective_transform": perspective_transform,
        "room_width_cm": w,
        "room_height_cm": h,
        "clicked_points": [list(pt) for pt in clicked_points],
        "timestamp": time.time(),
    }


def pixel_to_room(px_x: float, px_y: float, perspective_transform: np.ndarray) -> tuple[float, float]:
    """Transform pixel coordinates to room cm coordinates using a perspective transform."""
    pt = np.array([[[px_x, px_y]]], dtype=np.float32)
    transformed = cv2.perspectiveTransform(pt, perspective_transform)
    x_cm = float(transformed[0][0][0])
    y_cm = float(transformed[0][0][1])
    return x_cm, y_cm


def draw_calibration_frame(frame, clicked_points: list[tuple[int, int]], room_config: dict) -> np.ndarray:
    """Render calibration instructions and clicked corner markers."""
    overlay = frame.copy()
    for idx, (x, y) in enumerate(clicked_points):
        cv2.circle(overlay, (x, y), 8, (0, 255, 255), -1)
        cv2.putText(
            overlay,
            CORNER_LABELS[idx],
            (x + 10, y - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 255, 255),
            2,
        )

    if len(clicked_points) > 1:
        pts = np.array(clicked_points, dtype=np.int32).reshape((-1, 1, 2))
        cv2.polylines(overlay, [pts], False, (0, 180, 255), 2)

    room_label = f"{int(room_config.get('width_cm', 500))}x{int(room_config.get('height_cm', 400))}cm"
    next_corner = CORNER_LABELS[min(len(clicked_points), len(CORNER_LABELS) - 1)]
    instructions = [
        f"Click room corners in order: TL, TR, BR, BL ({room_label})",
        f"Next: {next_corner}   R: reset   Q/ESC: cancel",
    ]

    for idx, text in enumerate(instructions):
        cv2.putText(
            overlay,
            text,
            (16, 28 + idx * 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 255, 255),
            2,
        )

    return overlay


# ── Motion detection ─────────────────────────────────────────────

def check_motion(prev_gray, curr_gray, threshold=MOTION_THRESHOLD, min_area=MOTION_MIN_AREA) -> bool:
    """Frame differencing to detect significant motion."""
    diff = cv2.absdiff(prev_gray, curr_gray)
    _, thresh = cv2.threshold(diff, threshold, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    total_area = sum(cv2.contourArea(c) for c in contours)
    return total_area > min_area


# ── Frame sending ────────────────────────────────────────────────

def encode_frame(frame) -> str:
    """Encode a BGR frame as base64 JPEG."""
    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    return base64.b64encode(buf).decode("ascii")


def send_frame_event(image_b64: str, control_plane_url: str) -> None:
    """POST a frame event to the control plane (non-blocking).

    The control plane acks immediately and processes vision in the background,
    so this should be fast, but we still fire-and-forget to be safe.
    """

    def _post():
        try:
            resp = httpx.post(
                f"{control_plane_url}/events",
                json={
                    "device_id": "global_camera",
                    "kind": "frame",
                    "payload": {
                        "image_b64": image_b64,
                        "resolution": [640, 480],
                        "trigger": "motion",
                    },
                },
                timeout=30.0,
            )
            logger.info("Frame sent to control plane: %s", resp.status_code)
        except Exception as e:
            logger.error("Failed to send frame: %s", e)

    threading.Thread(target=_post, daemon=True).start()


# Non-blocking spatial update: fire-and-forget in a thread so the camera loop isn't blocked
def send_spatial_observe(
    device_id: str,
    x_cm: float,
    y_cm: float,
    control_plane_url: str,
    confidence: float = 0.9,
    theta_deg: float | None = None,
    source: str = "camera_markerless",
) -> None:
    """POST a spatial observation to the control plane (non-blocking)."""

    def _post():
        payload = {
            "device_id": device_id,
            "x_cm": round(x_cm, 1),
            "y_cm": round(y_cm, 1),
            "confidence": round(float(confidence), 3),
            "source": source,
        }
        if theta_deg is not None:
            payload["theta_deg"] = round(theta_deg, 1)
        try:
            httpx.post(
                f"{control_plane_url}/spatial/observe",
                json=payload,
                timeout=2.0,
            )
        except Exception as e:
            logger.debug("Spatial observe failed for %s: %s", device_id, e)

    threading.Thread(target=_post, daemon=True).start()


def maybe_send_spatial_observe(
    device_id: str,
    x_cm: float,
    y_cm: float,
    control_plane_url: str,
    tracker_state: dict[str, dict],
    room_config: dict,
    confidence: float = 0.9,
    theta_deg: float | None = None,
) -> bool:
    """Rate-limit spatial updates and drop tiny movements."""
    w = float(room_config.get("width_cm", 500))
    h = float(room_config.get("height_cm", 400))
    x_cm = max(0.0, min(x_cm, w))
    y_cm = max(0.0, min(y_cm, h))

    now = time.time()
    last = tracker_state.get(device_id)
    if last is not None:
        moved_cm = math.hypot(x_cm - last["x_cm"], y_cm - last["y_cm"])
        if moved_cm < SPATIAL_MIN_MOVE_CM:
            return False
        if (now - last["timestamp"]) < SPATIAL_POST_INTERVAL:
            return False

    send_spatial_observe(
        device_id,
        x_cm,
        y_cm,
        control_plane_url,
        confidence=confidence,
        theta_deg=theta_deg,
    )
    tracker_state[device_id] = {"timestamp": now, "x_cm": x_cm, "y_cm": y_cm}
    return True


# ── Tracker setup ────────────────────────────────────────────────

def load_person_detector():
    """Load the FasterRCNN person detector once at startup."""
    weights = "DEFAULT"
    if FasterRCNN_MobileNet_V3_Large_320_FPN_Weights is not None:
        weights = FasterRCNN_MobileNet_V3_Large_320_FPN_Weights.DEFAULT

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = fasterrcnn_mobilenet_v3_large_320_fpn(weights=weights)
    model.eval()
    model.to(device)
    logger.info("Loaded FasterRCNN person detector on %s", device)
    return model, device


def detect_person(frame, model, device) -> tuple[float, float, float] | None:
    """Run person detection and return the bottom-center point of the best box."""
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    rgb = np.ascontiguousarray(rgb)
    image_tensor = torch.from_numpy(rgb).permute(2, 0, 1).float().div(255.0).to(device)

    with torch.inference_mode():
        output = model([image_tensor])[0]

    labels = output["labels"].detach().cpu().numpy()
    scores = output["scores"].detach().cpu().numpy()
    boxes = output["boxes"].detach().cpu().numpy()

    person_indices = np.where(labels == 1)[0]
    if person_indices.size == 0:
        return None

    best_idx = person_indices[np.argmax(scores[person_indices])]
    x1, y1, x2, y2 = boxes[best_idx]
    x_px = float((x1 + x2) / 2.0)
    y_px = float(y2)
    confidence = float(scores[best_idx])
    return x_px, y_px, confidence


def detect_rover(frame, color_name: str) -> tuple[float, float, float | None, float] | None:
    """Track neon tape on the rover with HSV thresholding."""
    color_range = ROVER_COLOR_PRESETS[color_name]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, color_range["lower"], color_range["upper"])

    kernel = np.ones((5, 5), dtype=np.uint8)
    mask = cv2.erode(mask, kernel, iterations=1)
    mask = cv2.dilate(mask, kernel, iterations=2)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    contour = max(contours, key=cv2.contourArea)
    area = float(cv2.contourArea(contour))
    if area < ROVER_MIN_CONTOUR_AREA:
        return None

    moments = cv2.moments(contour)
    if moments["m00"] == 0:
        return None

    x_px = float(moments["m10"] / moments["m00"])
    y_px = float(moments["m01"] / moments["m00"])

    theta_deg = None
    rect = cv2.minAreaRect(contour)
    (_, _), (w, h), angle = rect
    if w > 0 and h > 0:
        theta_deg = float(angle + 90 if w < h else angle)
        theta_deg = (theta_deg + 360.0) % 360.0

    return x_px, y_px, theta_deg, area


# ── Main loop ────────────────────────────────────────────────────

def run_test_mode(control_plane_url: str) -> None:
    """Send a single test frame (solid gray image) and exit."""
    logger.info("Test mode: generating a synthetic test frame")
    # Create a simple 640x480 gray test image
    test_frame = np.full((480, 640, 3), 128, dtype=np.uint8)
    # Add some text
    cv2.putText(
        test_frame,
        "ClaudeHome Test Frame",
        (100, 240),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.0,
        (255, 255, 255),
        2,
    )

    image_b64 = encode_frame(test_frame)
    logger.info("Test frame encoded: %d bytes base64", len(image_b64))

    success = send_frame_event(image_b64, control_plane_url)
    if success:
        logger.info("Test frame sent successfully")
    else:
        logger.error("Failed to send test frame")
    sys.exit(0 if success else 1)


def run_calibration_mode(control_plane_url: str, camera_index: int = 0) -> None:
    """Show camera feed, collect 4 corner clicks, save calibration, and exit."""
    logger.info("Calibration mode: click corners in order TL, TR, BR, BL")
    cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        logger.error("Cannot open camera %d", camera_index)
        sys.exit(1)

    room_config = load_room_config(control_plane_url)
    state = {
        "points": [],
        "live_frame": None,
        "frozen_frame": None,
    }

    def on_mouse(event, x, y, _flags, _param):
        if event != cv2.EVENT_LBUTTONDOWN:
            return
        if len(state["points"]) >= 4:
            return
        if state["frozen_frame"] is None and state["live_frame"] is not None:
            state["frozen_frame"] = state["live_frame"].copy()
        state["points"].append((int(x), int(y)))

    cv2.namedWindow(CALIBRATION_WINDOW)
    cv2.setMouseCallback(CALIBRATION_WINDOW, on_mouse)

    try:
        while True:
            if state["frozen_frame"] is None:
                ret, frame = cap.read()
                if not ret:
                    logger.warning("Failed to read frame during calibration")
                    time.sleep(0.1)
                    continue
                state["live_frame"] = frame.copy()
            display_frame = state["frozen_frame"] if state["frozen_frame"] is not None else state["live_frame"]
            if display_frame is None:
                continue

            cv2.imshow(CALIBRATION_WINDOW, draw_calibration_frame(display_frame, state["points"], room_config))
            key = cv2.waitKey(20) & 0xFF

            if key in (27, ord("q")):
                logger.info("Calibration cancelled")
                sys.exit(1)

            if key == ord("r"):
                logger.info("Calibration points reset")
                state["points"].clear()
                state["frozen_frame"] = None

            if len(state["points"]) == 4:
                result = build_perspective_transform(state["points"], room_config)
                save_calibration(result)
                logger.info("Calibration saved successfully")
                sys.exit(0)
    finally:
        cap.release()
        cv2.destroyWindow(CALIBRATION_WINDOW)


def main() -> None:
    parser = argparse.ArgumentParser(description="ClaudeHome Vision Service")
    parser.add_argument("--camera", type=int, default=CAMERA_INDEX, help="Camera index")
    parser.add_argument("--control-plane-url", default=CONTROL_PLANE_URL, help="Control plane URL")
    parser.add_argument("--calibrate", action="store_true", help="Run calibration and exit")
    parser.add_argument("--test", action="store_true", help="Send a test frame and exit")
    parser.add_argument(
        "--rover-color",
        choices=sorted(ROVER_COLOR_PRESETS.keys()),
        default="green",
        help="HSV preset for rover tape tracking",
    )
    args = parser.parse_args()

    control_plane_url = args.control_plane_url

    if args.test:
        run_test_mode(control_plane_url)
        return

    if args.calibrate:
        run_calibration_mode(control_plane_url, args.camera)
        return

    # ── Normal operation ──────────────────────────────────────────

    cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        logger.error("Cannot open camera %d -- exiting gracefully", args.camera)
        sys.exit(1)

    logger.info("Camera %d opened, starting vision loop", args.camera)

    room_config = load_room_config(control_plane_url)
    person_detector, detector_device = load_person_detector()

    # Load calibration
    calibration = load_calibration()
    if calibration is None:
        logger.warning("No calibration file found -- spatial tracking disabled")
        logger.warning("Run with --calibrate to set up room corner mapping")

    # State for motion detection and frame cooldown
    prev_gray = None
    last_frame_sent = 0.0
    last_person_detection = 0.0
    last_spatial_updates: dict[str, dict] = {}

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                logger.warning("Failed to read frame, retrying...")
                time.sleep(0.1)
                continue

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            # ── Fast path: markerless spatial tracking ───────────
            if calibration is not None:
                perspective_transform = calibration["perspective_transform"]

                rover_detection = detect_rover(frame, args.rover_color)
                if rover_detection is not None:
                    rover_px_x, rover_px_y, rover_theta_deg, _ = rover_detection
                    rover_x_cm, rover_y_cm = pixel_to_room(rover_px_x, rover_px_y, perspective_transform)
                    maybe_send_spatial_observe(
                        "rover",
                        rover_x_cm,
                        rover_y_cm,
                        control_plane_url,
                        last_spatial_updates,
                        room_config,
                        confidence=0.9,
                        theta_deg=rover_theta_deg,
                    )

                now = time.time()
                if (now - last_person_detection) >= PERSON_DETECT_INTERVAL:
                    last_person_detection = now
                    person_detection = detect_person(frame, person_detector, detector_device)
                    if person_detection is not None:
                        person_px_x, person_px_y, person_confidence = person_detection
                        person_x_cm, person_y_cm = pixel_to_room(
                            person_px_x,
                            person_px_y,
                            perspective_transform,
                        )
                        maybe_send_spatial_observe(
                            "user",
                            person_x_cm,
                            person_y_cm,
                            control_plane_url,
                            last_spatial_updates,
                            room_config,
                            confidence=person_confidence,
                        )

            # ── Slow path: motion-gated frame for Claude Vision ───
            if prev_gray is not None:
                motion = check_motion(prev_gray, gray)
                now = time.time()
                if motion and (now - last_frame_sent) > FRAME_COOLDOWN:
                    logger.info("Motion detected, sending frame for vision analysis")
                    image_b64 = encode_frame(frame)
                    send_frame_event(image_b64, control_plane_url)
                    last_frame_sent = now

            prev_gray = gray
            time.sleep(0.033)  # ~30fps cap

    except KeyboardInterrupt:
        logger.info("Shutting down vision service")
    finally:
        cap.release()
        logger.info("Camera released")


if __name__ == "__main__":
    main()
