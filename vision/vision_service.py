"""Vision capture service for ClaudeHome.

Sidecar process: captures camera, detects ArUco markers for spatial tracking,
and sends motion-gated frames to control plane for Claude Vision analysis.

Usage:
    python3 -m vision.vision_service [--camera 0] [--control-plane-url http://localhost:8000]
    python3 -m vision.vision_service --calibrate   # re-run corner marker calibration
    python3 -m vision.vision_service --test         # send a single test frame and exit

Environment:
    CONTROL_PLANE_URL  -- default http://localhost:8000
    VISION_CAMERA      -- camera index (default 0)
    ARUCO_ROVER_ID     -- ArUco marker ID for rover (default 42)
"""

import argparse
import base64
import json
import logging
import os
import sys
import time
from pathlib import Path

import cv2
import httpx
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [vision] %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────

CONTROL_PLANE_URL = os.getenv("CONTROL_PLANE_URL", "http://localhost:8000")
CAMERA_INDEX = int(os.getenv("VISION_CAMERA", "0"))
ARUCO_ROVER_ID = int(os.getenv("ARUCO_ROVER_ID", "42"))
CALIBRATION_PATH = Path("data/vision_calibration.json")
FRAME_COOLDOWN = 8  # seconds between Claude Vision captures
MOTION_THRESHOLD = 25
MOTION_MIN_AREA = 500
JPEG_QUALITY = 80

# Corner marker IDs for room calibration (top-left, top-right, bottom-right, bottom-left)
CORNER_MARKER_IDS = [0, 1, 2, 3]


# ── ArUco setup ──────────────────────────────────────────────────

def create_aruco_detector():
    """Create ArUco detector with DICT_4X4_50 dictionary."""
    aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    detector_params = cv2.aruco.DetectorParameters()
    detector = cv2.aruco.ArucoDetector(aruco_dict, detector_params)
    return detector


# ── Calibration ──────────────────────────────────────────────────

def load_calibration() -> dict | None:
    """Load saved calibration from disk."""
    if CALIBRATION_PATH.exists():
        try:
            data = json.loads(CALIBRATION_PATH.read_text())
            # Reconstruct homography matrix from stored list
            if "homography" in data:
                data["homography"] = np.array(data["homography"], dtype=np.float64)
            logger.info("Loaded calibration from %s", CALIBRATION_PATH)
            return data
        except Exception as e:
            logger.warning("Failed to load calibration: %s", e)
    return None


def save_calibration(data: dict) -> None:
    """Save calibration to disk."""
    CALIBRATION_PATH.parent.mkdir(parents=True, exist_ok=True)
    save_data = dict(data)
    if "homography" in save_data and isinstance(save_data["homography"], np.ndarray):
        save_data["homography"] = save_data["homography"].tolist()
    CALIBRATION_PATH.write_text(json.dumps(save_data, indent=2))
    logger.info("Saved calibration to %s", CALIBRATION_PATH)


def calibrate_from_frame(frame, detector, room_config: dict) -> dict | None:
    """Detect 4 corner ArUco markers and compute homography.

    Expects markers with IDs 0-3 at the four room corners.
    room_config provides width_cm and height_cm for the real-world mapping.
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    corners, ids, _ = detector.detectMarkers(gray)

    if ids is None:
        logger.warning("No ArUco markers detected during calibration")
        return None

    ids_flat = ids.flatten().tolist()
    found = {}
    for i, marker_id in enumerate(ids_flat):
        if marker_id in CORNER_MARKER_IDS:
            # Use center of marker as reference point
            center = corners[i][0].mean(axis=0)
            found[marker_id] = center

    if len(found) < 4:
        missing = [m for m in CORNER_MARKER_IDS if m not in found]
        logger.warning("Calibration incomplete: missing corner markers %s (found %s)", missing, list(found.keys()))
        return None

    w = room_config.get("width_cm", 500)
    h = room_config.get("height_cm", 400)

    # Pixel coords of detected corners (order: TL, TR, BR, BL)
    src_pts = np.array([found[m] for m in CORNER_MARKER_IDS], dtype=np.float32)
    # Real-world coords in cm
    dst_pts = np.array([
        [0, 0],      # marker 0 = top-left
        [w, 0],      # marker 1 = top-right
        [w, h],      # marker 2 = bottom-right
        [0, h],      # marker 3 = bottom-left
    ], dtype=np.float32)

    homography, status = cv2.findHomography(src_pts, dst_pts)
    if homography is None:
        logger.error("Failed to compute homography")
        return None

    logger.info("Calibration successful: %dx%d cm room mapped", w, h)
    return {
        "homography": homography,
        "room_width_cm": w,
        "room_height_cm": h,
        "timestamp": time.time(),
    }


def pixel_to_room(px_x: float, px_y: float, homography: np.ndarray) -> tuple[float, float]:
    """Transform pixel coordinates to room cm coordinates using homography."""
    pt = np.array([[[px_x, px_y]]], dtype=np.float32)
    transformed = cv2.perspectiveTransform(pt, homography)
    x_cm = float(transformed[0][0][0])
    y_cm = float(transformed[0][0][1])
    return x_cm, y_cm


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
    _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    return base64.b64encode(buf).decode('ascii')


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


import threading

# Non-blocking spatial update: fire-and-forget in a thread so the camera loop isn't blocked
def send_spatial_observe(device_id: str, x_cm: float, y_cm: float,
                         control_plane_url: str, confidence: float = 0.9) -> None:
    """POST a spatial observation to the control plane (non-blocking)."""
    def _post():
        try:
            httpx.post(
                f"{control_plane_url}/spatial/observe",
                json={
                    "device_id": device_id,
                    "x_cm": round(x_cm, 1),
                    "y_cm": round(y_cm, 1),
                    "confidence": confidence,
                    "source": "camera_aruco",
                },
                timeout=2.0,
            )
        except Exception as e:
            logger.debug("Spatial observe failed for %s: %s", device_id, e)
    threading.Thread(target=_post, daemon=True).start()


# ── ArUco processing ─────────────────────────────────────────────

def process_aruco_detections(corners, ids, calibration: dict | None,
                             control_plane_url: str) -> None:
    """Process detected ArUco markers, send spatial updates for known devices."""
    if calibration is None or "homography" not in calibration:
        return

    homography = calibration["homography"]
    if isinstance(homography, list):
        homography = np.array(homography, dtype=np.float64)

    ids_flat = ids.flatten().tolist()
    for i, marker_id in enumerate(ids_flat):
        # Skip corner markers
        if marker_id in CORNER_MARKER_IDS:
            continue

        # Compute center of marker in pixel space
        center = corners[i][0].mean(axis=0)
        x_cm, y_cm = pixel_to_room(center[0], center[1], homography)

        # Map marker IDs to device IDs
        device_id = None
        if marker_id == ARUCO_ROVER_ID:
            device_id = "rover"

        if device_id:
            send_spatial_observe(device_id, x_cm, y_cm, control_plane_url)


# ── Room config fetching ─────────────────────────────────────────

def fetch_room_config(control_plane_url: str) -> dict:
    """Fetch room config from the control plane."""
    try:
        resp = httpx.get(f"{control_plane_url}/room", timeout=5.0)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.warning("Failed to fetch room config: %s", e)
    return {"width_cm": 500, "height_cm": 400}


# ── Main loop ────────────────────────────────────────────────────

def run_test_mode(control_plane_url: str) -> None:
    """Send a single test frame (solid gray image) and exit."""
    logger.info("Test mode: generating a synthetic test frame")
    # Create a simple 640x480 gray test image
    test_frame = np.full((480, 640, 3), 128, dtype=np.uint8)
    # Add some text
    cv2.putText(test_frame, "ClaudeHome Test Frame", (100, 240),
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)

    image_b64 = encode_frame(test_frame)
    logger.info("Test frame encoded: %d bytes base64", len(image_b64))

    success = send_frame_event(image_b64, control_plane_url)
    if success:
        logger.info("Test frame sent successfully")
    else:
        logger.error("Failed to send test frame")
    sys.exit(0 if success else 1)


def run_calibration_mode(control_plane_url: str, camera_index: int = 0) -> None:
    """Capture one frame, attempt calibration, save and exit."""
    logger.info("Calibration mode: capturing one frame for corner marker detection")
    cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        logger.error("Cannot open camera %d", CAMERA_INDEX)
        sys.exit(1)

    ret, frame = cap.read()
    cap.release()

    if not ret:
        logger.error("Failed to capture frame for calibration")
        sys.exit(1)

    detector = create_aruco_detector()
    room_config = fetch_room_config(control_plane_url)
    result = calibrate_from_frame(frame, detector, room_config)

    if result is None:
        logger.error("Calibration failed -- ensure 4 corner markers (IDs 0-3) are visible")
        sys.exit(1)

    save_calibration(result)
    logger.info("Calibration saved successfully")
    sys.exit(0)


def main() -> None:
    parser = argparse.ArgumentParser(description="ClaudeHome Vision Service")
    parser.add_argument("--camera", type=int, default=CAMERA_INDEX, help="Camera index")
    parser.add_argument("--control-plane-url", default=CONTROL_PLANE_URL, help="Control plane URL")
    parser.add_argument("--calibrate", action="store_true", help="Run calibration and exit")
    parser.add_argument("--test", action="store_true", help="Send a test frame and exit")
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

    detector = create_aruco_detector()
    room_config = fetch_room_config(control_plane_url)

    # Load or attempt calibration
    calibration = load_calibration()
    if calibration is None:
        logger.warning("No calibration file found -- ArUco spatial tracking disabled")
        logger.warning("Run with --calibrate to set up corner markers")

    # State for motion detection and frame cooldown
    prev_gray = None
    last_frame_sent = 0.0

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                logger.warning("Failed to read frame, retrying...")
                time.sleep(0.1)
                continue

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            # ── Fast path: ArUco detection every frame ────────────
            corners, ids, _ = detector.detectMarkers(gray)
            if ids is not None and calibration is not None:
                process_aruco_detections(corners, ids, calibration, control_plane_url)

            # Try auto-calibration if we don't have it yet
            if calibration is None and ids is not None:
                ids_flat = ids.flatten().tolist()
                if all(m in ids_flat for m in CORNER_MARKER_IDS):
                    logger.info("All corner markers detected -- attempting auto-calibration")
                    calibration = calibrate_from_frame(frame, detector, room_config)
                    if calibration:
                        save_calibration(calibration)

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
