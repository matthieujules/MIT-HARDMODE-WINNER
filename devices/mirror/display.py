from __future__ import annotations

import os
from pathlib import Path

from PIL import Image


class MirrorDisplay:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def show(self, image: Image.Image, hold_seconds: int = 0) -> Path:
        latest = self.output_dir / "latest-screen.png"
        image.convert("RGB").save(latest)

        if os.getenv("MIRROR_SAVE_ONLY") == "1" or not os.getenv("DISPLAY"):
            return latest

        try:
            import tkinter as tk
            from PIL import ImageTk
        except Exception:
            return latest

        root = tk.Tk()
        root.title("Mirror Display")
        root.configure(bg="black")
        root.attributes("-fullscreen", True)
        root.bind("<Escape>", lambda event: root.destroy())
        root.focus_force()

        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()
        rendered = self._fit_for_screen(image.convert("RGB"), screen_w, screen_h)
        photo = ImageTk.PhotoImage(rendered)

        label = tk.Label(root, image=photo, bg="black", borderwidth=0, highlightthickness=0)
        label.pack(fill="both", expand=True)
        label.image = photo

        if hold_seconds > 0:
            root.after(hold_seconds * 1000, root.destroy)
        root.mainloop()
        return latest

    def _fit_for_screen(self, image: Image.Image, screen_w: int, screen_h: int) -> Image.Image:
        rotate_mode = os.getenv("MIRROR_ROTATE", "auto").strip().lower()
        rendered = image

        if rotate_mode in {"90", "270", "180"}:
            rendered = rendered.rotate(int(rotate_mode), expand=True)
        elif rotate_mode == "auto":
            screen_is_landscape = screen_w > screen_h
            image_is_portrait = rendered.height > rendered.width
            if screen_is_landscape and image_is_portrait:
                rendered = rendered.rotate(90, expand=True)

        return rendered.resize((screen_w, screen_h), Image.Resampling.LANCZOS)
