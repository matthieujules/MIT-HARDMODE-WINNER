"""Audio clip catalog and playback assembly for the Radio device.

Scans the Sounds/ directory for audio files and builds playback
manifests.  Clip selection is handled by the Cerebras agent in
agent.py — this module only provides the catalog and assembly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


RADIO_DIR = Path(__file__).resolve().parent
SOUNDS_DIR = RADIO_DIR / "Sounds"

# Accept any single-letter alphabetic music code (A-Z).
MUSIC_CODE_PATTERN = r"[A-Za-z]"


@dataclass(frozen=True)
class AudioChoice:
	code: str
	path: Path
	kind: str  # "music" | "soundbite" | "glitch"
	label: str


def _load_choices() -> tuple[dict[str, AudioChoice], AudioChoice | None]:
	"""Scan Sounds/ directory and return all available clips + glitch."""
	choices: dict[str, AudioChoice] = {}
	glitch: AudioChoice | None = None

	if not SOUNDS_DIR.exists():
		return choices, glitch

	for path in sorted(SOUNDS_DIR.glob("*.mp3"), key=lambda item: item.name.lower()):
		stem = path.stem

		if re.match(r"^00(?:_|$)", stem):
			label = re.sub(r"^00_?", "", stem).strip() or "Glitch"
			glitch = AudioChoice(code="00", path=path, kind="glitch", label=label)
			continue

		music_match = re.match(rf"^({MUSIC_CODE_PATTERN})_", stem)
		if music_match:
			code = music_match.group(1).upper()
			if code not in choices:
				label = re.sub(rf"^{MUSIC_CODE_PATTERN}_", "", stem).replace("_", " ").strip()
				choices[code] = AudioChoice(code=code, path=path, kind="music", label=label)
			continue

		bite_match = re.match(r"^(\d{1,2})_", stem)
		if bite_match:
			code = bite_match.group(1)
			if code != "00" and code not in choices:
				label = re.sub(r"^\d{1,2}_", "", stem).replace("_", " ").strip()
				choices[code] = AudioChoice(code=code, path=path, kind="soundbite", label=label)

	return choices, glitch


def _clip_entry(index: int, choice: AudioChoice) -> dict[str, Any]:
	relative_file = f"Sounds/{choice.path.name}"
	return {
		"index": index,
		"token": choice.code,
		"kind": choice.kind,
		"label": choice.label,
		"file": relative_file,
		"audio_file": relative_file,
		"audio_url": None,
		"text": choice.label,
	}


def get_clip_catalog() -> list[dict[str, str]]:
	"""Return the clip catalog for agent system prompt and tool enum."""
	choices, _ = _load_choices()
	catalog: list[dict[str, str]] = []
	for code in sorted(choices, key=lambda c: (0 if c.isalpha() else 1, c)):
		choice = choices[code]
		catalog.append({"code": code, "kind": choice.kind, "label": choice.label})
	return catalog


def build_playback_for_code(code: str) -> dict[str, Any]:
	"""Build a playback result dict for a pre-selected clip code.  No LLM call."""
	choices, glitch = _load_choices()

	if code == "stop":
		stop_clips: list[dict[str, Any]] = []
		if glitch is not None:
			stop_clips.append(_clip_entry(1, glitch))
		return {
			"ok": True,
			"command": code,
			"selection": "stop",
			"plan": {"action": "stop_audio", "turn_radio": False, "selection": "stop"},
			"execution": {
				"clips_generated": stop_clips,
				"playback": {"type": "stop", "status": "stop_requested", "audio_items": stop_clips, "audio_queue": []},
			},
		}

	choice = choices.get(code)
	if choice is None:
		return {
			"ok": False,
			"error": f"No audio clip found for code '{code}'",
			"command": code,
			"selection": code,
		}

	clips: list[dict[str, Any]] = []
	next_index = 1
	if glitch is not None:
		clips.append(_clip_entry(next_index, glitch))
		next_index += 1
	clips.append(_clip_entry(next_index, choice))

	playback_type = "music" if choice.kind == "music" else "podcast"
	return {
		"ok": True,
		"command": code,
		"selection": code,
		"plan": {
			"action": "output_podcast",
			"turn_radio": True,
			"selection": code,
			"category": choice.kind,
		},
		"execution": {
			"final_selection": code,
			"clips_generated": clips,
			"playback": {
				"type": playback_type,
				"status": "queued_local_files",
				"audio_items": clips,
				"audio_queue": [],
			},
		},
	}
