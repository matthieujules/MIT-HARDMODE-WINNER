from __future__ import annotations

import logging
import os
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

try:
    from .models import CapturedFrame
except ImportError:
    from models import CapturedFrame

logger = logging.getLogger(__name__)


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class DisplayRequest:
    kind: str
    image: Image.Image | None = None
    ttl_s: float = 20.0


class MirrorDisplay:
    """Pygame-based fullscreen display manager.

    States:
    - MIRROR: live camera feed or placeholder
    - GENERATED: static generated image with TTL

    Only the main thread should touch Pygame. Other threads communicate through
    ``display_queue`` via ``show_generated`` and ``show_mirror``.
    """

    MIRROR = "mirror"
    GENERATED = "generated"

    def __init__(
        self,
        output_dir: Path,
        display_size: tuple[int, int],
        fps: int = 30,
    ) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.display_size = (int(display_size[0]), int(display_size[1]))
        self.fps = fps
        self.latest_screen_path = self.output_dir / "latest-screen.png"

        self.display_queue: queue.Queue[DisplayRequest] = queue.Queue()
        self._camera_lock = threading.Lock()
        self._save_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._stop_event = threading.Event()

        self._camera_frame: CapturedFrame | None = None
        self._camera_available = False
        self._camera_status = "Camera initializing."

        self.state = self.MIRROR
        self._generated_image: Image.Image | None = None
        self._generated_surface: Any = None
        self._generated_expires_at: float | None = None

        self.headless = _env_flag("MIRROR_HEADLESS")
        self.windowed = not self.headless and _env_flag("MIRROR_WINDOWED")

        self._pygame: Any = None
        self._screen: Any = None
        self._clock: Any = None
        self._window_size = self.display_size
        self._placeholder_cache_key: tuple[tuple[int, int], str] | None = None
        self._placeholder_surface: Any = None

        self._init_runtime()

    def show_generated(self, image: Image.Image, ttl_s: float = 20.0) -> Path:
        rendered = ImageOps.fit(
            image.convert("RGB"),
            self.display_size,
            method=Image.Resampling.LANCZOS,
        )
        self._save_latest_screen(rendered)

        if self.headless:
            with self._state_lock:
                self._apply_show_generated(DisplayRequest("show_generated", rendered, ttl_s))
            return self.latest_screen_path

        self.display_queue.put(DisplayRequest("show_generated", rendered, ttl_s))
        return self.latest_screen_path

    def show(self, image: Image.Image, hold_seconds: int = 20, preview: bool = False) -> Path:
        """Compatibility wrapper around ``show_generated``.

        ``preview`` is ignored because the persistent Pygame runtime owns previewing.
        """
        if preview:
            logger.debug("MirrorDisplay.show(preview=True) is deprecated; using the persistent display window.")
        ttl_s = 20 if hold_seconds <= 0 else hold_seconds
        return self.show_generated(image, ttl_s=ttl_s)

    def show_mirror(self) -> None:
        if self.headless:
            with self._state_lock:
                self._apply_show_mirror("command")
            return

        self.display_queue.put(DisplayRequest("show_mirror"))

    def set_camera_frame(self, frame: Image.Image | CapturedFrame) -> None:
        captured = self._coerce_frame(frame)
        with self._camera_lock:
            self._camera_frame = captured
            self._camera_available = captured.source == "camera"
            if captured.source == "camera":
                self._camera_status = "Camera live."
            else:
                self._camera_status = str(captured.metadata.get("reason", "Camera unavailable."))
        if captured.source != "camera":
            self._placeholder_cache_key = None

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        if self.headless:
            logger.info("Display run loop active in headless mode")
            try:
                while not self._stop_event.wait(0.25):
                    with self._state_lock:
                        self._maybe_expire_generated_image()
            finally:
                logger.info("Display run loop stopped")
            return

        if self._pygame is None or self._screen is None or self._clock is None:
            raise RuntimeError("Pygame display was not initialized")

        logger.info("Display render loop starting")
        try:
            self._save_latest_screen(self._mirror_snapshot_image(self.display_size))
            while not self._stop_event.is_set():
                self._process_display_queue()
                self._handle_events()
                self._render_current_state()
                self._pygame.display.flip()
                self._clock.tick(self.fps)
        finally:
            self._pygame.quit()
            logger.info("Display run loop stopped")

    def _init_runtime(self) -> None:
        if self.headless:
            logger.info("Display initialized in headless mode (MIRROR_HEADLESS=1)")
            return

        try:
            import pygame
        except ImportError:
            logger.warning("Pygame is not installed. Falling back to headless display mode.")
            self.headless = True
            self.windowed = False
            return

        self._pygame = pygame

        try:
            pygame.init()
            mode_size = self._resolve_mode_size()
            flags = 0 if self.windowed else pygame.FULLSCREEN
            self._screen = pygame.display.set_mode(mode_size, flags)
            self._clock = pygame.time.Clock()
            self._window_size = tuple(int(v) for v in self._screen.get_size())
            pygame.display.set_caption("Mirror")
            pygame.mouse.set_visible(False)
        except Exception as exc:
            logger.warning("Pygame display initialization failed: %s. Falling back to headless mode.", exc)
            try:
                pygame.quit()
            except Exception:
                pass
            self._pygame = None
            self._screen = None
            self._clock = None
            self.headless = True
            self.windowed = False
            return

        if self.windowed:
            logger.info(
                "Pygame display initialized: %dx%d windowed (MIRROR_WINDOWED=1)",
                self._window_size[0],
                self._window_size[1],
            )
        else:
            logger.info(
                "Pygame display initialized: %dx%d fullscreen",
                self._window_size[0],
                self._window_size[1],
            )

    def _resolve_mode_size(self) -> tuple[int, int]:
        if not self.windowed:
            return self.display_size

        width, height = self.display_size
        max_dimension = 960
        scale = min(1.0, max_dimension / max(width, height))
        scaled_width = max(320, int(width * scale))
        scaled_height = max(480, int(height * scale))
        return scaled_width, scaled_height

    def _process_display_queue(self) -> None:
        while True:
            try:
                request = self.display_queue.get_nowait()
            except queue.Empty:
                return

            if request.kind == "show_generated" and request.image is not None:
                self._apply_show_generated(request)
            elif request.kind == "show_mirror":
                self._apply_show_mirror("command")
            else:
                logger.warning("Unknown display request: %s", request.kind)

    def _apply_show_generated(self, request: DisplayRequest) -> None:
        previous_state = self.state
        self.state = self.GENERATED
        self._generated_image = request.image
        self._generated_surface = None
        self._generated_expires_at = time.monotonic() + max(0.0, request.ttl_s)

        if previous_state == self.MIRROR:
            logger.info("Display state: MIRROR -> GENERATED (ttl=%ss)", self._format_ttl(request.ttl_s))
        else:
            logger.info("Display state: GENERATED refreshed (ttl=%ss)", self._format_ttl(request.ttl_s))

    def _apply_show_mirror(self, reason: str) -> None:
        previous_state = self.state
        self.state = self.MIRROR
        self._generated_image = None
        self._generated_surface = None
        self._generated_expires_at = None
        self._save_latest_screen(self._mirror_snapshot_image(self.display_size))

        if previous_state != self.MIRROR:
            logger.info("Display state: GENERATED -> MIRROR (%s)", reason)

    def _maybe_expire_generated_image(self) -> None:
        if self.state != self.GENERATED or self._generated_expires_at is None:
            return
        if time.monotonic() >= self._generated_expires_at:
            self._apply_show_mirror("ttl expired")

    def _handle_events(self) -> None:
        assert self._pygame is not None
        for event in self._pygame.event.get():
            if event.type == self._pygame.QUIT:
                self.stop()
                return
            if event.type == self._pygame.KEYDOWN and event.key == self._pygame.K_ESCAPE:
                self.stop()
                return

    def _render_current_state(self) -> None:
        assert self._screen is not None

        self._maybe_expire_generated_image()

        if self.state == self.GENERATED and self._generated_image is not None:
            if self._generated_surface is None:
                fitted = ImageOps.fit(
                    self._generated_image,
                    self._window_size,
                    method=Image.Resampling.LANCZOS,
                )
                self._generated_surface = self._pil_to_surface(fitted)
            self._screen.blit(self._generated_surface, (0, 0))
            return

        frame = self._current_camera_frame()
        if frame is not None and frame.source == "camera":
            self._render_mirror_frame(frame.image)
            return

        self._render_placeholder()

    def _current_camera_frame(self) -> CapturedFrame | None:
        with self._camera_lock:
            return self._camera_frame

    def _render_mirror_frame(self, frame: Image.Image) -> None:
        assert self._screen is not None
        mirrored = ImageOps.mirror(frame.convert("RGB"))
        fitted = ImageOps.fit(
            mirrored,
            self._window_size,
            method=Image.Resampling.LANCZOS,
        )
        self._screen.blit(self._pil_to_surface(fitted), (0, 0))

    def _render_placeholder(self) -> None:
        assert self._screen is not None
        status = self._placeholder_status()
        cache_key = (self._window_size, status)
        if cache_key != self._placeholder_cache_key or self._placeholder_surface is None:
            image = self._build_placeholder_image(self._window_size, status)
            self._placeholder_surface = self._pil_to_surface(image)
            self._placeholder_cache_key = cache_key
        self._screen.blit(self._placeholder_surface, (0, 0))

    def _placeholder_status(self) -> str:
        with self._camera_lock:
            return self._camera_status

    def _mirror_snapshot_image(self, size: tuple[int, int]) -> Image.Image:
        frame = self._current_camera_frame()
        if frame is not None and frame.source == "camera":
            mirrored = ImageOps.mirror(frame.image.convert("RGB"))
            return ImageOps.fit(
                mirrored,
                size,
                method=Image.Resampling.LANCZOS,
            )
        return self._build_placeholder_image(size, self._placeholder_status())

    def _build_placeholder_image(self, size: tuple[int, int], status: str) -> Image.Image:
        width, height = size
        image = Image.new("RGB", size, (6, 10, 18))
        draw = ImageDraw.Draw(image)

        for y in range(height):
            ratio = y / max(1, height - 1)
            color = (
                int(8 + ratio * 14),
                int(12 + ratio * 24),
                int(20 + ratio * 52),
            )
            draw.line(((0, y), (width, y)), fill=color)

        glow = Image.new("RGBA", size, (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow)
        glow_draw.ellipse(
            (
                int(width * 0.14),
                int(height * 0.1),
                int(width * 0.86),
                int(height * 0.76),
            ),
            fill=(66, 150, 255, 50),
        )
        glow_draw.ellipse(
            (
                int(width * 0.24),
                int(height * 0.18),
                int(width * 0.76),
                int(height * 0.66),
            ),
            fill=(25, 208, 184, 30),
        )
        glow = glow.filter(ImageFilter.GaussianBlur(radius=max(12, int(min(size) * 0.04))))
        image = Image.alpha_composite(image.convert("RGBA"), glow)

        draw = ImageDraw.Draw(image)
        accent = (130, 226, 219)
        outline_width = max(4, int(min(size) * 0.008))
        draw.ellipse(
            (
                int(width * 0.22),
                int(height * 0.14),
                int(width * 0.78),
                int(height * 0.7),
            ),
            outline=accent + (225,),
            width=outline_width,
        )

        panel_top = int(height * 0.72)
        draw.rounded_rectangle(
            (
                int(width * 0.08),
                panel_top,
                int(width * 0.92),
                int(height * 0.9),
            ),
            radius=max(16, int(width * 0.03)),
            fill=(7, 16, 28, 220),
            outline=(72, 126, 176, 180),
            width=max(2, int(width * 0.004)),
        )

        title_font = self._load_font(max(28, int(height * 0.055)), bold=True)
        label_font = self._load_font(max(15, int(height * 0.02)), bold=True)
        body_font = self._load_font(max(18, int(height * 0.024)))

        title = "Mirror"
        subtitle = "Live reflection standby"
        title_x = int(width * 0.12)
        title_y = int(height * 0.76)
        draw.text((title_x, title_y), title, fill=(244, 249, 255), font=title_font)
        subtitle_y = title_y + self._font_height(title_font) + max(8, int(height * 0.01))
        draw.text((title_x, subtitle_y), subtitle, fill=(160, 184, 214), font=label_font)

        status_label_y = subtitle_y + self._font_height(label_font) + max(18, int(height * 0.02))
        draw.text((title_x, status_label_y), "CAMERA STATUS", fill=accent + (255,), font=label_font)

        status_y = status_label_y + self._font_height(label_font) + max(8, int(height * 0.01))
        for line in self._wrap_text(status.rstrip(".") + ".", body_font, int(width * 0.76)):
            draw.text((title_x, status_y), line, fill=(218, 228, 242), font=body_font)
            status_y += self._font_height(body_font) + max(4, int(height * 0.006))

        return image.convert("RGB")

    def _load_font(self, size: int, bold: bool = False) -> ImageFont.ImageFont:
        candidates = [
            "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/Library/Fonts/Arial Bold.ttf" if bold else "/Library/Fonts/Arial.ttf",
            "arialbd.ttf" if bold else "arial.ttf",
        ]
        for candidate in candidates:
            try:
                return ImageFont.truetype(candidate, size=size)
            except Exception:
                continue
        return ImageFont.load_default()

    def _wrap_text(self, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
        words = text.split()
        if not words:
            return [""]

        lines: list[str] = []
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if self._text_width(candidate, font) <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
        return lines

    def _text_width(self, text: str, font: ImageFont.ImageFont) -> int:
        left, _, right, _ = font.getbbox(text)
        return right - left

    def _font_height(self, font: ImageFont.ImageFont) -> int:
        _, top, _, bottom = font.getbbox("Ag")
        return bottom - top

    def _pil_to_surface(self, image: Image.Image) -> Any:
        if self._pygame is None:
            raise RuntimeError("Pygame is not available")
        surface = self._pygame.image.fromstring(
            image.convert("RGB").tobytes(),
            image.size,
            "RGB",
        )
        return surface.convert()

    def _coerce_frame(self, frame: Image.Image | CapturedFrame) -> CapturedFrame:
        if isinstance(frame, CapturedFrame):
            return frame
        image = frame.convert("RGB")
        return CapturedFrame(
            image=image,
            source="camera",
            captured_at=time.time(),
            metadata={
                "width": image.width,
                "height": image.height,
            },
        )

    def _save_latest_screen(self, image: Image.Image) -> None:
        with self._save_lock:
            image.convert("RGB").save(self.latest_screen_path)

    def _format_ttl(self, ttl_s: float) -> str:
        ttl_value = float(ttl_s)
        if ttl_value.is_integer():
            return str(int(ttl_value))
        return f"{ttl_value:.1f}"
