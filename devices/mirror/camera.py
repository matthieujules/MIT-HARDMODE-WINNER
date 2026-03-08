from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from PIL import Image, ImageDraw

try:
    from .models import CapturedFrame
except ImportError:
    from models import CapturedFrame

try:
    import cv2
except ImportError:
    cv2 = None

try:
    from picamera2 import Picamera2
except ImportError:
    Picamera2 = None

logger = logging.getLogger(__name__)


class MirrorCamera:
    """Persistent camera capture thread.

    Opens ``cv2.VideoCapture`` once and continuously publishes the latest frame.
    If the camera is not available, the device keeps running and returns
    placeholder frames instead of crashing.
    """

    def __init__(
        self,
        device: str = "/dev/video0",
        capture_size: tuple[int, int] = (640, 480),
        on_frame_callback: Callable[[CapturedFrame], None] | None = None,
    ) -> None:
        self.device = device
        self.capture_size = (int(capture_size[0]), int(capture_size[1]))
        self.on_frame_callback = on_frame_callback

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._latest_frame: CapturedFrame | None = None
        self._available = False
        self._status_message = "Camera has not started."

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._capture_loop,
            name="mirror-camera",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def get_frame(self) -> CapturedFrame:
        with self._lock:
            if self._latest_frame is not None:
                return self._latest_frame
            reason = self._status_message
        return self.placeholder_frame(reason=reason)

    def capture(self) -> CapturedFrame:
        """Compatibility wrapper for older callers."""
        return self.get_frame()

    def is_available(self) -> bool:
        with self._lock:
            return self._available

    def placeholder_frame(self, reason: str) -> CapturedFrame:
        width, height = self.capture_size
        image = Image.new("RGB", (width, height), (18, 24, 38))
        draw = ImageDraw.Draw(image)

        for y in range(height):
            blue = int(38 + (y / max(1, height - 1)) * 60)
            draw.line([(0, y), (width, y)], fill=(20, 30, blue))

        draw.ellipse(
            (width * 0.28, height * 0.16, width * 0.72, height * 0.82),
            outline=(126, 214, 223),
            width=4,
        )
        draw.text((24, 24), "Camera fallback", fill=(236, 244, 252))
        draw.text((24, 54), reason, fill=(166, 190, 212))

        return CapturedFrame(
            image=image,
            source="placeholder",
            captured_at=time.time(),
            metadata={
                "reason": reason,
                "width": image.width,
                "height": image.height,
            },
        )

    def _capture_loop(self) -> None:
        # Try picamera2 first (Pi 5 CSI cameras), then fall back to OpenCV.
        if Picamera2 is not None:
            try:
                self._capture_loop_picamera2()
                return
            except Exception as exc:
                logger.warning("picamera2 failed (%s), falling back to OpenCV", exc)

        if cv2 is not None:
            self._capture_loop_opencv()
        else:
            self._publish_placeholder("neither picamera2 nor opencv-python is installed")

    def _capture_loop_picamera2(self) -> None:
        requested_width, requested_height = self.capture_size
        cam = Picamera2()
        config = cam.create_video_configuration(
            main={"size": (requested_width, requested_height), "format": "RGB888"},
        )
        cam.configure(config)
        cam.start()

        actual_width, actual_height = requested_width, requested_height
        self._update_status(
            available=True,
            message=f"Camera picamera2 opened ({actual_width}x{actual_height})",
        )
        logger.info("picamera2 opened (%dx%d)", actual_width, actual_height)

        try:
            while not self._stop_event.is_set():
                arr = cam.capture_array()
                image = Image.fromarray(arr)
                frame = CapturedFrame(
                    image=image,
                    source="camera",
                    captured_at=time.time(),
                    metadata={
                        "device": "picamera2",
                        "width": image.width,
                        "height": image.height,
                    },
                )
                self._publish_frame(frame)
        finally:
            cam.stop()
            cam.close()
            self._update_status(available=False, message="Camera stopped.")
            logger.info("picamera2 closed")

    def _capture_loop_opencv(self) -> None:
        capture_target = self._camera_target()
        camera = cv2.VideoCapture(capture_target)
        if not camera.isOpened():
            self._publish_placeholder(f"camera {self.device} could not be opened")
            return

        requested_width, requested_height = self.capture_size
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, requested_width)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, requested_height)
        try:
            camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

        actual_width = int(camera.get(cv2.CAP_PROP_FRAME_WIDTH) or requested_width)
        actual_height = int(camera.get(cv2.CAP_PROP_FRAME_HEIGHT) or requested_height)
        self._update_status(
            available=True,
            message=f"Camera {self.device} opened successfully ({actual_width}x{actual_height})",
        )
        logger.info("Camera %s opened successfully (%dx%d)", self.device, actual_width, actual_height)

        read_failure_logged = False
        try:
            while not self._stop_event.is_set():
                ok, frame_bgr = camera.read()
                if not ok or frame_bgr is None:
                    if not read_failure_logged:
                        logger.warning("Camera not available: camera %s returned no frame. Mirror mode will use placeholder.", self.device)
                        read_failure_logged = True
                    self._publish_placeholder("camera returned no frame", log_message=False)
                    self._stop_event.wait(0.1)
                    continue

                read_failure_logged = False
                frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                image = Image.fromarray(frame_rgb)
                frame = CapturedFrame(
                    image=image,
                    source="camera",
                    captured_at=time.time(),
                    metadata={
                        "device": self.device,
                        "width": image.width,
                        "height": image.height,
                    },
                )
                self._publish_frame(frame)
        finally:
            camera.release()
            self._update_status(available=False, message="Camera stopped.")
            logger.info("Camera %s released", self.device)

    def _publish_frame(self, frame: CapturedFrame) -> None:
        with self._lock:
            self._latest_frame = frame
            self._available = frame.source == "camera"
            self._status_message = (
                "Camera live."
                if frame.source == "camera"
                else str(frame.metadata.get("reason", "Camera unavailable."))
            )

        if self.on_frame_callback is not None:
            self.on_frame_callback(frame)

    def _publish_placeholder(self, reason: str, log_message: bool = True) -> None:
        message = f"Camera not available: {reason}. Mirror mode will use placeholder."
        if log_message:
            logger.warning(message)
        self._update_status(available=False, message=reason)
        self._publish_frame(self.placeholder_frame(reason))

    def _update_status(self, available: bool, message: str) -> None:
        with self._lock:
            self._available = available
            self._status_message = message

    def _camera_target(self) -> int | str:
        if self.device.isdigit():
            return int(self.device)
        if self.device.startswith("/dev/video"):
            try:
                return int(self.device.replace("/dev/video", ""))
            except ValueError:
                return self.device
        return self.device
