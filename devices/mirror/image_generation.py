from __future__ import annotations

import base64
import io
import math
import os
import time
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps

try:
    from .models import CapturedFrame, DisplayPlan, GenerationResult
except ImportError:
    from models import CapturedFrame, DisplayPlan, GenerationResult

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


class MirrorImageGenerator:
    def __init__(
        self,
        output_dir: Path,
        display_size: tuple[int, int] = (1080, 1920),
    ) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.display_size = display_size
        self.client = self._build_image_client()
        self.image_model = os.getenv("MIRROR_IMAGE_MODEL", "gpt-image-1")

    def generate(self, plan: DisplayPlan, frame: CapturedFrame) -> GenerationResult:
        generated = None
        if self.client is not None:
            generated = self._try_generate_with_api(plan, frame)

        if generated is None:
            image = self._render_local(plan, frame)
            source = "local_fallback"
        else:
            image = generated
            source = "image_api"

        saved_path = self._save_output(image, plan)
        return GenerationResult(
            image=image,
            source=source,
            saved_path=saved_path,
            metadata={
                "prompt": plan.prompt,
                "display_mode": plan.display_mode,
                "icon_name": plan.icon_name,
            },
        )

    def _build_image_client(self) -> OpenAI | None:
        api_key = os.getenv("MIRROR_IMAGE_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key or OpenAI is None:
            return None

        base_url = os.getenv("MIRROR_IMAGE_BASE_URL")
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        return OpenAI(**kwargs)

    def _try_generate_with_api(self, plan: DisplayPlan, frame: CapturedFrame) -> Image.Image | None:
        try:
            if plan.wants_camera_context:
                return self._edit_from_camera_frame(plan, frame)
            return self._generate_from_prompt(plan)
        except Exception:
            return None

    def _edit_from_camera_frame(self, plan: DisplayPlan, frame: CapturedFrame) -> Image.Image:
        buffer = io.BytesIO()
        frame.image.convert("RGBA").save(buffer, format="PNG")
        buffer.seek(0)
        response = self.client.images.edit(
            model=self.image_model,
            image=buffer,
            prompt=plan.prompt,
            size=self._api_size(),
        )
        return self._decode_response_image(response).resize(self.display_size, Image.Resampling.LANCZOS)

    def _generate_from_prompt(self, plan: DisplayPlan) -> Image.Image:
        response = self.client.images.generate(
            model=self.image_model,
            prompt=plan.prompt,
            size=self._api_size(),
        )
        return self._decode_response_image(response).resize(self.display_size, Image.Resampling.LANCZOS)

    def _api_size(self) -> str:
        width, height = self.display_size
        if height >= width:
            return "1024x1536"
        return "1536x1024"

    def _decode_response_image(self, response: object) -> Image.Image:
        payload = response.data[0]
        if getattr(payload, "b64_json", None):
            decoded = base64.b64decode(payload.b64_json)
            return Image.open(io.BytesIO(decoded)).convert("RGB")
        if getattr(payload, "url", None):
            raise RuntimeError("image URL responses are not supported in offline runtime mode")
        raise RuntimeError("image response did not include b64_json")

    def _render_local(self, plan: DisplayPlan, frame: CapturedFrame) -> Image.Image:
        background = self._base_background(plan, frame).convert("RGBA")
        draw = ImageDraw.Draw(background)

        self._draw_glow(background, plan.accent_color)
        self._draw_icon(draw, plan, background.size)
        self._draw_caption(draw, plan, background.size)

        return background.convert("RGB")

    def _base_background(self, plan: DisplayPlan, frame: CapturedFrame) -> Image.Image:
        fitted = ImageOps.fit(frame.image.convert("RGB"), self.display_size, method=Image.Resampling.LANCZOS)
        softened = fitted.filter(ImageFilter.GaussianBlur(radius=18))
        tinted = ImageEnhance.Color(softened).enhance(0.55)

        overlay = Image.new("RGB", self.display_size, plan.background_color)
        blended = Image.blend(tinted, overlay, 0.45)

        vignette = Image.new("L", self.display_size, 0)
        mask_draw = ImageDraw.Draw(vignette)
        mask_draw.ellipse((-160, -200, self.display_size[0] + 160, self.display_size[1] + 220), fill=180)
        vignette = ImageOps.invert(vignette).filter(ImageFilter.GaussianBlur(radius=120))
        dark_layer = Image.new("RGB", self.display_size, (0, 0, 0))
        return Image.composite(dark_layer, blended, vignette)

    def _draw_glow(self, image: Image.Image, accent_color: tuple[int, int, int]) -> None:
        glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(glow)
        width, height = image.size
        draw.ellipse(
            (width * 0.12, height * 0.1, width * 0.88, height * 0.84),
            outline=accent_color + (90,),
            width=10,
        )
        draw.ellipse(
            (width * 0.2, height * 0.18, width * 0.8, height * 0.76),
            outline=accent_color + (45,),
            width=6,
        )
        glow = glow.filter(ImageFilter.GaussianBlur(radius=18))
        image.alpha_composite(glow)

    def _draw_icon(self, draw: ImageDraw.ImageDraw, plan: DisplayPlan, size: tuple[int, int]) -> None:
        width, height = size
        cx = width / 2
        cy = height * 0.44
        radius = min(width, height) * 0.16
        accent = plan.accent_color

        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=accent, width=12)

        if plan.icon_name == "check":
            points = [
                (cx - radius * 0.46, cy + radius * 0.02),
                (cx - radius * 0.1, cy + radius * 0.36),
                (cx + radius * 0.52, cy - radius * 0.34),
            ]
            draw.line(points, fill=accent, width=18, joint="curve")
        elif plan.icon_name == "smile":
            eye_r = radius * 0.12
            draw.ellipse((cx - radius * 0.42, cy - radius * 0.22, cx - radius * 0.18, cy + radius * 0.02), fill=accent)
            draw.ellipse((cx + radius * 0.18, cy - radius * 0.22, cx + radius * 0.42, cy + radius * 0.02), fill=accent)
            draw.arc((cx - radius * 0.5, cy - radius * 0.2, cx + radius * 0.5, cy + radius * 0.56), start=20, end=160, fill=accent, width=14)
        elif plan.icon_name == "heart":
            draw.polygon(
                [
                    (cx, cy + radius * 0.62),
                    (cx - radius * 0.74, cy - radius * 0.04),
                    (cx - radius * 0.54, cy - radius * 0.56),
                    (cx, cy - radius * 0.18),
                    (cx + radius * 0.54, cy - radius * 0.56),
                    (cx + radius * 0.74, cy - radius * 0.04),
                ],
                outline=accent,
                fill=None,
                width=10,
            )
        elif plan.icon_name == "focus":
            for scale in (1.0, 0.66, 0.34):
                r = radius * scale
                draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=accent, width=8)
            draw.ellipse((cx - radius * 0.08, cy - radius * 0.08, cx + radius * 0.08, cy + radius * 0.08), fill=accent)
        elif plan.icon_name == "calm":
            for idx in range(3):
                y = cy - radius * 0.34 + idx * radius * 0.34
                draw.arc((cx - radius * 0.76, y - radius * 0.16, cx + radius * 0.76, y + radius * 0.16), start=10, end=170, fill=accent, width=10)
        elif plan.icon_name == "alert":
            draw.polygon(
                [
                    (cx, cy - radius * 0.72),
                    (cx - radius * 0.62, cy + radius * 0.6),
                    (cx + radius * 0.62, cy + radius * 0.6),
                ],
                outline=accent,
                width=10,
            )
            draw.line((cx, cy - radius * 0.28, cx, cy + radius * 0.2), fill=accent, width=16)
            draw.ellipse((cx - radius * 0.08, cy + radius * 0.34, cx + radius * 0.08, cy + radius * 0.5), fill=accent)
        else:
            for angle in range(0, 360, 45):
                dx = radius * 0.72
                dy = radius * 0.72
                draw.line(
                    (
                        cx,
                        cy,
                        cx + dx * math.cos(math.radians(angle)),
                        cy + dy * math.sin(math.radians(angle)),
                    ),
                    fill=accent,
                    width=10,
                )

    def _draw_caption(self, draw: ImageDraw.ImageDraw, plan: DisplayPlan, size: tuple[int, int]) -> None:
        width, height = size
        caption = plan.caption or plan.raw_instruction
        text_box = (width * 0.12, height * 0.74, width * 0.88, height * 0.89)
        draw.rounded_rectangle(text_box, radius=28, fill=(6, 10, 18, 196), outline=plan.accent_color, width=3)
        draw.text((text_box[0] + 28, text_box[1] + 28), caption, fill=(241, 245, 249))

    def _save_output(self, image: Image.Image, plan: DisplayPlan) -> Path:
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        slug = "".join(c if c.isalnum() else "-" for c in plan.icon_name)[:24].strip("-") or "mirror"
        path = self.output_dir / f"{timestamp}-{slug}.png"
        image.convert("RGB").save(path)

        latest = self.output_dir / "latest.png"
        image.convert("RGB").save(latest)
        return path
