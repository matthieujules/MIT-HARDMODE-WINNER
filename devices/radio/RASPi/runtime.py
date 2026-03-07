from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from audio import AudioPlaybackError, RadioAudio
from config import RadioRuntimeConfig, load_runtime_config
from dial import RadioDial

RADIO_DIR = Path(__file__).resolve().parent.parent
if str(RADIO_DIR) not in sys.path:
    sys.path.insert(0, str(RADIO_DIR))

from brain import run_radio_command  # noqa: E402


class RadioRuntime:
    def __init__(self, config: RadioRuntimeConfig) -> None:
        self.config = config
        self.audio = RadioAudio(config.audio)
        self.dial = RadioDial(config.dial, enabled=True)

    @classmethod
    def from_repo_defaults(cls) -> "RadioRuntime":
        radio_dir = Path(__file__).resolve().parent.parent
        load_dotenv(radio_dir / ".env")
        config = load_runtime_config(radio_dir / "config.yaml")
        return cls(config)

    def handle_command(self, command: str) -> dict[str, Any]:
        result = run_radio_command(command)
        plan = result.get("plan", {})
        execution = result.get("execution", {})
        playback = execution.get("playback", {})

        dial_events = execution.get("dial_events") or playback.get("dial_events") or []
        for event in dial_events:
            degrees = int(event.get("degrees", 55))
            self._perform_dial_step(degrees)

        native_playback = self._execute_playback(plan, playback, execution)
        result["raspi"] = {
            "dial_history": [event.__dict__ for event in self.dial.history()],
            "native_playback": native_playback,
        }
        return result

    def close(self) -> None:
        self.dial.close()

    def _perform_dial_step(self, degrees: int) -> None:
        steps = max(1, round(abs(degrees) / 55))
        for _ in range(steps):
            if degrees >= 0:
                self.dial.nudge_clockwise()
            else:
                self.dial.nudge_counterclockwise()

    def _execute_playback(self, plan: dict, playback: dict, execution: dict) -> dict[str, Any]:
        action = plan.get("action")
        try:
            if action == "output_music":
                return self.audio.play_music(
                    query=plan.get("spotify_query", ""),
                    preview_url=playback.get("audio_url"),
                )

            if action == "output_podcast":
                files = []
                for clip in execution.get("clips_generated", []):
                    relative = clip.get("file")
                    if relative:
                        files.append((RADIO_DIR / relative.lstrip("/")).resolve())
                return self.audio.play_files(files)
        except AudioPlaybackError as exc:
            return {
                "status": "audio_error",
                "error": str(exc),
            }
        except Exception as exc:
            return {
                "status": "runtime_error",
                "error": str(exc),
            }

        return {
            "status": "noop",
            "message": "No playback action executed.",
        }
