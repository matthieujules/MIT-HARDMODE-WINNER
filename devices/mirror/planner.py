from __future__ import annotations

import re

try:
    from .models import DisplayPlan
except ImportError:
    from models import DisplayPlan


class MirrorInstructionPlanner:
    """Deterministic regex planner for mirror display instructions.

    Maps instructions to DisplayPlan with icon, colors, and prompt.
    High-level reasoning is handled by the agent (Layer 2); this planner
    only needs to produce a sensible visual plan from a concrete instruction.
    """

    def plan(self, instruction: str) -> DisplayPlan:
        instruction = instruction.strip()
        if not instruction:
            raise ValueError("instruction cannot be empty")
        return self._plan_locally(instruction)

    def _plan_locally(self, instruction: str) -> DisplayPlan:
        lowered = instruction.lower()

        if self._matches(lowered, r"\b(check|checkmark|done|complete|completed|success|ok)\b"):
            return self._symbol_plan(instruction, "check", "Done.", (52, 211, 153), (7, 33, 27))
        if self._matches(lowered, r"\b(smile|smiley|happy|joy|cheer|encourage)\b"):
            return self._symbol_plan(instruction, "smile", "You got this.", (250, 204, 21), (38, 26, 8))
        if self._matches(lowered, r"\b(heart|love|care|warmth)\b"):
            return self._symbol_plan(instruction, "heart", "With you.", (244, 114, 182), (59, 18, 33))
        if self._matches(lowered, r"\b(focus|lock in|concentrate|deep work)\b"):
            return self._symbol_plan(instruction, "focus", "Focus mode.", (96, 165, 250), (10, 25, 51))
        if self._matches(lowered, r"\b(calm|relax|breathe|soften)\b"):
            return self._symbol_plan(instruction, "calm", "Take a breath.", (125, 211, 252), (8, 35, 46))
        if self._matches(lowered, r"\b(alert|warning|stop|danger|error|no)\b"):
            return self._symbol_plan(instruction, "alert", "Pay attention.", (248, 113, 113), (69, 10, 10))

        return DisplayPlan(
            raw_instruction=instruction,
            prompt=self._default_prompt(instruction, "sparkle"),
            display_mode="scene",
            icon_name="sparkle",
            caption=self._caption(instruction),
            metadata={"planner": "local"},
        )

    def _symbol_plan(
        self,
        instruction: str,
        icon_name: str,
        caption: str,
        accent_color: tuple[int, int, int],
        background_color: tuple[int, int, int],
    ) -> DisplayPlan:
        return DisplayPlan(
            raw_instruction=instruction,
            prompt=self._default_prompt(instruction, icon_name),
            display_mode="symbol",
            icon_name=icon_name,
            caption=caption,
            wants_camera_context=False,
            accent_color=accent_color,
            background_color=background_color,
            metadata={"planner": "local"},
        )

    def _default_prompt(self, instruction: str, icon_name: str) -> str:
        return (
            "Create a vertical smart-mirror display image for a Raspberry Pi LCD. "
            "The user is standing in front of the mirror. Use the captured camera frame "
            "for pose, lighting, and general silhouette context, then add a polished visual "
            f"response based on this request: {instruction!r}. "
            f"Preferred motif: {icon_name}. "
            "Make it legible at a distance, elegant, bold, and suitable for portrait orientation."
        )

    def _caption(self, instruction: str) -> str:
        collapsed = re.sub(r"\s+", " ", instruction).strip()
        if len(collapsed) <= 60:
            return collapsed
        return collapsed[:57].rstrip() + "..."

    def _matches(self, text: str, pattern: str) -> bool:
        return re.search(pattern, text) is not None
