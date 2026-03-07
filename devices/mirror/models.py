from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CapturedFrame:
    image: Any
    source: str
    captured_at: float
    path: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DisplayPlan:
    raw_instruction: str
    prompt: str
    display_mode: str
    icon_name: str
    caption: str
    wants_camera_context: bool = True
    accent_color: tuple[int, int, int] = (94, 234, 212)
    background_color: tuple[int, int, int] = (11, 18, 32)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GenerationResult:
    image: Any
    source: str
    saved_path: Path
    metadata: dict[str, Any] = field(default_factory=dict)
