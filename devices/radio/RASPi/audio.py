from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable

import httpx

from config import AudioConfig


class AudioPlaybackError(RuntimeError):
    pass


class RadioAudio:
    def __init__(self, config: AudioConfig) -> None:
        self.config = config
        self.media_library_dir = (Path(__file__).resolve().parent / config.media_library_dir).resolve()
        self.generated_audio_dir = (Path(__file__).resolve().parent / config.generated_audio_dir).resolve()
        self.generated_audio_dir.mkdir(parents=True, exist_ok=True)

    def play_music(self, query: str, preview_url: str | None = None) -> dict:
        local_match = self._find_local_track(query)
        if local_match is not None:
            self._play_path(local_match)
            return {
                "status": "played_local",
                "source": "local_media",
                "track": str(local_match),
            }

        if preview_url:
            downloaded = self._download_preview(preview_url)
            self._play_path(downloaded)
            return {
                "status": "played_preview",
                "source": "downloaded_preview",
                "track": str(downloaded),
            }

        return {
            "status": "no_audio_found",
            "source": "none",
            "track": None,
        }

    def play_files(self, files: Iterable[Path]) -> dict:
        played: list[str] = []
        missing: list[str] = []
        for path in files:
            if path.exists():
                self._play_path(path)
                played.append(str(path))
            else:
                missing.append(str(path))
        return {
            "status": "played" if played else "missing",
            "played": played,
            "missing": missing,
        }

    def _find_local_track(self, query: str) -> Path | None:
        if not self.media_library_dir.exists():
            return None

        query_terms = [term for term in query.lower().split() if term]
        candidates = sorted(
            [
                path
                for path in self.media_library_dir.rglob("*")
                if path.suffix.lower() in {".mp3", ".wav", ".ogg", ".m4a", ".flac"}
            ]
        )
        if not query_terms:
            return candidates[0] if candidates else None

        scored: list[tuple[int, Path]] = []
        for path in candidates:
            haystack = path.stem.lower()
            score = sum(1 for term in query_terms if term in haystack)
            if score > 0:
                scored.append((score, path))
        scored.sort(key=lambda item: (-item[0], str(item[1])))
        return scored[0][1] if scored else None

    def _download_preview(self, preview_url: str) -> Path:
        fd, tmp_path = tempfile.mkstemp(prefix="radio-preview-", suffix=".mp3", dir=self.generated_audio_dir)
        Path(tmp_path).unlink(missing_ok=True)
        out_path = Path(tmp_path)
        with httpx.Client(timeout=30.0) as client:
            response = client.get(preview_url)
            response.raise_for_status()
            out_path.write_bytes(response.content)
        return out_path

    def _play_path(self, path: Path) -> None:
        player = self._player_command(path)
        if player is None:
            raise AudioPlaybackError(
                "No supported local audio player found. Install mpg123, ffplay, cvlc, or aplay."
            )
        subprocess.run(player, check=True)

    def _player_command(self, path: Path) -> list[str] | None:
        if shutil.which("mpg123"):
            return ["mpg123", "-q", str(path)]
        if shutil.which("ffplay"):
            return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)]
        if shutil.which("cvlc"):
            return ["cvlc", "--play-and-exit", str(path)]
        if shutil.which("aplay") and path.suffix.lower() == ".wav":
            return ["aplay", str(path)]
        return None
