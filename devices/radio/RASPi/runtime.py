from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from audio import AudioPlaybackError, PlaybackInterrupted, RadioAudio
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
        self._next_glitch_clockwise = True
        self._dial_spin_lock = threading.Lock()
        self._dial_threads: list[threading.Thread] = []

    @classmethod
    def from_repo_defaults(cls) -> "RadioRuntime":
        radio_dir = Path(__file__).resolve().parent.parent
        load_dotenv(radio_dir / ".env")
        config = load_runtime_config(radio_dir / "config.yaml")
        return cls(config)

    def handle_command(self, command: str) -> dict[str, Any]:
        self.audio.clear_interrupt()
        self.dial.attach()
        result = run_radio_command(command)
        plan = result.get("plan", {})
        execution = result.get("execution", {})
        playback = execution.get("playback", {})

        dial_events = execution.get("dial_events") or playback.get("dial_events") or []
        for event in dial_events:
            degrees = int(event.get("degrees", 55))
            self._perform_dial_step(degrees)

        native_playback = self._execute_playback(plan, playback, execution)
        self._wait_for_dial_threads()
        self.dial.detach()
        result["raspi"] = {
            "dial_history": [event.__dict__ for event in self.dial.history()],
            "native_playback": native_playback,
        }
        return result

    def close(self) -> None:
        self.audio.stop_current_playback()
        self.dial.close()

    def interrupt_playback(self) -> None:
        self.audio.stop_current_playback()

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
                clip_meta: list[dict[str, Any]] = []
                for clip in execution.get("clips_generated", []):
                    relative = clip.get("file")
                    if relative:
                        files.append((RADIO_DIR / relative.lstrip("/")).resolve())
                        clip_meta.append(clip)

                def _on_clip_start(index: int, _: Path) -> None:
                    if 0 <= index < len(clip_meta) and str(clip_meta[index].get("kind", "")).lower() == "glitch":
                        self._trigger_glitch_spin()

                return self.audio.play_files(files, on_play_start=_on_clip_start)
        except PlaybackInterrupted:
            return {
                "status": "interrupted",
                "message": "Playback interrupted by a newer command.",
            }
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

    def _trigger_glitch_spin(self) -> None:
        clockwise = self._next_glitch_clockwise
        self._next_glitch_clockwise = not self._next_glitch_clockwise

        def _run_spin() -> None:
            with self._dial_spin_lock:
                if clockwise:
                    self.dial.nudge_clockwise(duration_seconds=1.2)
                else:
                    self.dial.nudge_counterclockwise(duration_seconds=1.2)

        thread = threading.Thread(target=_run_spin, daemon=True)
        self._dial_threads.append(thread)
        thread.start()

    def _wait_for_dial_threads(self) -> None:
        for thread in self._dial_threads:
            thread.join(timeout=2.5)
        self._dial_threads.clear()
