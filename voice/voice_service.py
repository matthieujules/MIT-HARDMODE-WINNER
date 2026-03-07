"""Voice capture service for ClaudeHome.

Sidecar process: captures audio, runs VAD, sends transcripts to control plane.

Usage:
    python3 -m voice.voice_service [--backend groq|local] [--control-plane-url http://localhost:8000] [--test]

Environment:
    GROQ_API_KEY        -- required if using groq backend
    STT_BACKEND         -- groq or local (default: groq)
    CONTROL_PLANE_URL   -- default http://localhost:8000
"""

import argparse
import io
import logging
import os
import re
import struct
import sys
import time
import wave
from collections import deque
from queue import Empty, Queue

import threading

import httpx
import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [voice] %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("voice")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SAMPLE_RATE = 16000          # Hz
CHANNELS = 1                 # mono
DTYPE = "int16"
CHUNK_SAMPLES = 512          # 32 ms at 16 kHz -- Silero VAD native frame size
SILENCE_DURATION_MS = 300    # ms of silence to mark end of speech
SPEECH_PAD_MS = 150          # ms of padding kept before/after speech
MIN_SPEECH_DURATION_S = 0.5  # ignore very short noises

EMERGENCY_STOP_PATTERN = re.compile(
    r"\b(stop|halt|freeze|wait|no\s*no\s*no)\b", re.IGNORECASE
)

# How many chunks equal the silence / pad durations
_chunks_per_ms = SAMPLE_RATE / (CHUNK_SAMPLES * 1000)
SILENCE_CHUNKS = max(1, int(SILENCE_DURATION_MS * _chunks_per_ms))
PAD_CHUNKS = max(1, int(SPEECH_PAD_MS * _chunks_per_ms))

# ---------------------------------------------------------------------------
# WAV encoding helper
# ---------------------------------------------------------------------------


def pcm_to_wav_bytes(pcm: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Wrap raw int16 PCM in a proper WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)  # int16 = 2 bytes
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


# ---------------------------------------------------------------------------
# STT backends
# ---------------------------------------------------------------------------


def transcribe_groq(audio_bytes: bytes) -> str:
    """Send WAV audio to Groq's Whisper API. Returns transcript text."""
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        logger.error("GROQ_API_KEY not set -- cannot use groq backend")
        return ""

    try:
        resp = httpx.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": ("audio.wav", audio_bytes, "audio/wav")},
            data={
                "model": "whisper-large-v3-turbo",
                "response_format": "text",
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.text.strip()
    except Exception as e:
        logger.error("Groq transcription failed: %s", e)
        return ""


_local_model = None


def transcribe_local(audio_bytes: bytes) -> str:
    """Use faster-whisper locally with base.en model."""
    global _local_model
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        logger.error("faster-whisper not installed -- pip install faster-whisper")
        return ""

    if _local_model is None:
        logger.info("Loading faster-whisper base.en model (first time)...")
        _local_model = WhisperModel("base.en", compute_type="int8")

    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
        tmp.write(audio_bytes)
        tmp.flush()
        segments, _ = _local_model.transcribe(tmp.name, beam_size=1)
        return " ".join(seg.text.strip() for seg in segments).strip()


# ---------------------------------------------------------------------------
# Silero VAD loader
# ---------------------------------------------------------------------------


def load_vad_model():
    """Load Silero VAD v5 via torch.hub."""
    import torch

    logger.info("Loading Silero VAD model...")
    model, utils = torch.hub.load(
        repo_or_dir="snakers4/silero-vad",
        model="silero_vad",
        trust_repo=True,
    )
    logger.info("Silero VAD loaded.")
    return model


# ---------------------------------------------------------------------------
# Control plane helpers
# ---------------------------------------------------------------------------


def check_voice_lock(cp_url: str) -> bool:
    """GET /state and check if voice_lock is active."""
    try:
        resp = httpx.get(f"{cp_url}/state", timeout=2.0)
        resp.raise_for_status()
        state = resp.json()
        voice_lock = state.get("voice_lock", {})
        now = time.time()
        for device_id, lock_info in voice_lock.items():
            if lock_info.get("is_speaking"):
                locked_at = lock_info.get("locked_at", 0)
                if now - locked_at < 10:  # same 10s timeout as router.py
                    return True
        return False
    except Exception as e:
        logger.warning("Could not check voice lock: %s", e)
        return False  # fail open -- don't suppress on error


def post_transcript(cp_url: str, text: str) -> bool:
    """POST transcript event to control plane."""
    event = {
        "device_id": "global_mic",
        "kind": "transcript",
        "payload": {"text": text},
    }
    try:
        resp = httpx.post(
            f"{cp_url}/events",
            json=event,
            timeout=10.0,
        )
        resp.raise_for_status()
        result = resp.json()
        logger.info("Control plane response: %s", result)
        return True
    except Exception as e:
        logger.error("Failed to POST transcript: %s", e)
        return False


# ---------------------------------------------------------------------------
# Test mode
# ---------------------------------------------------------------------------


def run_test_mode(cp_url: str):
    """Send a hardcoded test transcript without requiring a microphone."""
    test_phrases = [
        "Hello, this is a test transcript from the voice service.",
        "lamp blue",
        "I need to lock in",
    ]
    logger.info("=== TEST MODE === Sending %d test transcripts to %s", len(test_phrases), cp_url)

    # Check if control plane is reachable
    try:
        resp = httpx.get(f"{cp_url}/health", timeout=3.0)
        resp.raise_for_status()
        logger.info("Control plane healthy: %s", resp.json())
    except Exception as e:
        logger.error("Control plane not reachable at %s: %s", cp_url, e)
        logger.error("Start the control plane first: python3 -m uvicorn control_plane.app:app --host 0.0.0.0 --port 8000")
        return

    for phrase in test_phrases:
        logger.info("Sending: %r", phrase)
        ok = post_transcript(cp_url, phrase)
        if ok:
            logger.info("  -> sent OK")
        else:
            logger.error("  -> FAILED")
        time.sleep(0.5)

    logger.info("=== TEST MODE COMPLETE ===")


# ---------------------------------------------------------------------------
# Main audio capture + VAD loop
# ---------------------------------------------------------------------------


def _transcribe_and_post(transcribe_fn, wav_bytes: bytes, cp_url: str) -> None:
    """Transcribe audio and post result. Runs in background thread."""
    t0 = time.time()
    text = transcribe_fn(wav_bytes)
    latency = time.time() - t0

    if not text:
        logger.info("Empty transcript, skipping (latency=%.2fs)", latency)
        return

    logger.info("Transcript (%.2fs): %r", latency, text)

    # Check emergency stop before voice lock
    is_emergency = bool(EMERGENCY_STOP_PATTERN.search(text))

    if not is_emergency and check_voice_lock(cp_url):
        logger.info("Voice lock active, suppressing: %r", text)
        return

    # Post to control plane
    post_transcript(cp_url, text)


def run_voice_capture(backend: str, cp_url: str):
    """Main loop: capture audio, run VAD, transcribe, post to control plane."""

    import torch
    import sounddevice as sd

    transcribe = transcribe_groq if backend == "groq" else transcribe_local

    # Load VAD
    vad_model = load_vad_model()

    # Audio queue: sounddevice callback pushes chunks here
    audio_queue: Queue = Queue()

    def audio_callback(indata, frames, time_info, status):
        """sounddevice callback -- must be fast, just enqueue."""
        if status:
            logger.warning("Audio status: %s", status)
        # indata is (frames, channels) int16 -- copy to avoid overwrite
        audio_queue.put(indata[:, 0].copy())

    # State machine
    is_speaking = False
    speech_buffer: list[np.ndarray] = []
    silence_counter = 0
    # Ring buffer for pre-speech padding
    pre_buffer: deque = deque(maxlen=PAD_CHUNKS)

    logger.info("Starting audio capture: %d Hz, mono, %d-sample chunks", SAMPLE_RATE, CHUNK_SAMPLES)
    logger.info("STT backend: %s | Control plane: %s", backend, cp_url)
    logger.info("Press Ctrl+C to stop.")

    try:
        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=DTYPE,
            blocksize=CHUNK_SAMPLES,
            callback=audio_callback,
        ):
            while True:
                try:
                    chunk = audio_queue.get(timeout=0.1)
                except Empty:
                    continue

                # Convert to float32 for Silero (expects [-1, 1])
                chunk_f32 = chunk.astype(np.float32) / 32768.0
                chunk_tensor = torch.from_numpy(chunk_f32)

                # Run VAD
                speech_prob = vad_model(chunk_tensor, SAMPLE_RATE).item()

                if not is_speaking:
                    pre_buffer.append(chunk)
                    if speech_prob > 0.5:
                        # Speech started
                        is_speaking = True
                        silence_counter = 0
                        # Include pre-speech padding
                        speech_buffer = list(pre_buffer)
                        speech_buffer.append(chunk)
                        logger.debug("Speech started (prob=%.2f)", speech_prob)
                else:
                    speech_buffer.append(chunk)
                    if speech_prob < 0.5:
                        silence_counter += 1
                    else:
                        silence_counter = 0

                    if silence_counter >= SILENCE_CHUNKS:
                        # Speech ended
                        is_speaking = False
                        duration_s = len(speech_buffer) * CHUNK_SAMPLES / SAMPLE_RATE

                        if duration_s < MIN_SPEECH_DURATION_S:
                            logger.debug("Ignoring short noise (%.2fs)", duration_s)
                            speech_buffer = []
                            continue

                        logger.info("Speech ended (%.2fs), transcribing...", duration_s)

                        # Concatenate and encode as WAV
                        pcm = np.concatenate(speech_buffer)
                        wav_bytes = pcm_to_wav_bytes(pcm)
                        speech_buffer = []

                        # Transcribe + post in a background thread so VAD keeps running
                        threading.Thread(
                            target=_transcribe_and_post,
                            args=(transcribe, wav_bytes, cp_url),
                            daemon=True,
                        ).start()

    except KeyboardInterrupt:
        logger.info("Shutting down voice service.")
    except Exception as e:
        logger.error("Voice service error: %s", e, exc_info=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="ClaudeHome voice capture service")
    parser.add_argument(
        "--backend",
        choices=["groq", "local"],
        default=os.environ.get("STT_BACKEND", "groq"),
        help="STT backend (default: groq, or set STT_BACKEND env var)",
    )
    parser.add_argument(
        "--control-plane-url",
        default=os.environ.get("CONTROL_PLANE_URL", "http://localhost:8000"),
        help="Control plane URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Send hardcoded test transcripts instead of capturing audio",
    )
    args = parser.parse_args()

    if args.test:
        run_test_mode(args.control_plane_url)
    else:
        run_voice_capture(args.backend, args.control_plane_url)


if __name__ == "__main__":
    main()
