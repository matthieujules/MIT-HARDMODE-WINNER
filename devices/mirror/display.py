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
        rendered = image.convert("RGB").resize((screen_w, screen_h), Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(rendered)

        label = tk.Label(root, image=photo, bg="black", borderwidth=0, highlightthickness=0)
        label.pack(fill="both", expand=True)
        label.image = photo

        if hold_seconds > 0:
            root.after(hold_seconds * 1000, root.destroy)
        root.mainloop()
        return latest
