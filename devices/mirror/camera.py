from __future__ import annotations

import time
from pathlib import Path

from PIL import Image, ImageDraw

try:
    from .models import CapturedFrame
except ImportError:
    from models import CapturedFrame

try:
    import cv2
except ImportError:
    cv2 = None


class MirrorCamera:
    def __init__(
        self,
        device: str = "/dev/video0",
        capture_size: tuple[int, int] = (640, 480),
        warmup_frames: int = 8,
    ) -> None:
        self.device = device
        self.capture_size = capture_size
        self.warmup_frames = warmup_frames

    def capture(self) -> CapturedFrame:
        if cv2 is None:
            return self.placeholder_frame(reason="opencv-python is not installed")

        capture_target: int | str = self._camera_target()
        camera = cv2.VideoCapture(capture_target)
        if not camera.isOpened():
            return self.placeholder_frame(reason=f"camera {self.device} could not be opened")

        width, height = self.capture_size
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

        frame_bgr = None
        try:
            for _ in range(self.warmup_frames):
                ok, frame_bgr = camera.read()
                if not ok:
                    frame_bgr = None
                    continue
                time.sleep(0.03)
        finally:
            camera.release()

        if frame_bgr is None:
            return self.placeholder_frame(reason="camera returned no frame")

        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(frame_rgb)
        return CapturedFrame(
            image=image,
            source="camera",
            captured_at=time.time(),
            metadata={
                "device": self.device,
                "width": image.width,
                "height": image.height,
            },
        )

    def _camera_target(self) -> int | str:
        if self.device.isdigit():
            return int(self.device)
        if self.device.startswith("/dev/video"):
            try:
                return int(self.device.replace("/dev/video", ""))
            except ValueError:
                return self.device
        return self.device

    def placeholder_frame(self, reason: str) -> CapturedFrame:
        width, height = self.capture_size
        image = Image.new("RGB", (width, height), (18, 24, 38))
        draw = ImageDraw.Draw(image)

        for y in range(height):
            blue = int(38 + (y / max(1, height - 1)) * 60)
            draw.line([(0, y), (width, y)], fill=(20, 30, blue))

        draw.ellipse((width * 0.28, height * 0.16, width * 0.72, height * 0.82), outline=(126, 214, 223), width=4)
        draw.text((24, 24), "Camera fallback", fill=(236, 244, 252))
        draw.text((24, 54), reason, fill=(166, 190, 212))

        return CapturedFrame(
            image=image,
            source="placeholder",
            captured_at=time.time(),
            metadata={"reason": reason},
        )
