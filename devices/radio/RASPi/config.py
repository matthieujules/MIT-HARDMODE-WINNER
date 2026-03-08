from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class DialConfig:
    driver: str = "pca9685"
    gpio_pin: int = 18
    pca9685_bus: int = 1
    pca9685_address: int = 0x40
    pca9685_channel: int = 0
    pca9685_frequency_hz: int = 50
    min_pulse_us: int = 700
    neutral_pulse_us: int = 1510
    max_pulse_us: int = 2300
    neutral_trim: float = +0.04
    min_pulse_width: float = 0.001
    max_pulse_width: float = 0.002
    stop_value: float = 0.0
    clockwise_value: float = 0.8
    counterclockwise_value: float = -0.8
    turn_duration_seconds: float = 0.6
    settle_seconds: float = 0.1


@dataclass
class AudioConfig:
    output_device_hint: str = ""
    media_library_dir: str = "../media"
    generated_audio_dir: str = "../output"
    default_volume: int = 55


@dataclass
class RadioRuntimeConfig:
    speaker_enabled: bool
    dial: DialConfig
    audio: AudioConfig
    raw: dict[str, Any]


def load_runtime_config(config_path: Path) -> RadioRuntimeConfig:
    data = yaml.safe_load(config_path.read_text()) or {}
    hardware = data.get("hardware", {})

    dial_data = hardware.get("dial", {})
    audio_data = hardware.get("audio", {})
    speaker = hardware.get("speaker", {})

    return RadioRuntimeConfig(
        speaker_enabled=bool(speaker.get("enabled", True)),
        dial=DialConfig(
            driver=str(dial_data.get("driver", "pca9685")).strip().lower() or "pca9685",
            gpio_pin=int(dial_data.get("gpio_pin", 18)),
            pca9685_bus=int(dial_data.get("pca9685_bus", 1)),
            pca9685_address=int(str(dial_data.get("pca9685_address", "0x40")), 0),
            pca9685_channel=int(dial_data.get("pca9685_channel", 0)),
            pca9685_frequency_hz=int(dial_data.get("pca9685_frequency_hz", 50)),
            min_pulse_us=int(dial_data.get("min_pulse_us", 700)),
            neutral_pulse_us=int(dial_data.get("neutral_pulse_us", 1500)),
            max_pulse_us=int(dial_data.get("max_pulse_us", 2300)),
            neutral_trim=float(dial_data.get("neutral_trim", 0.0)),
            min_pulse_width=float(dial_data.get("min_pulse_width", 0.001)),
            max_pulse_width=float(dial_data.get("max_pulse_width", 0.002)),
            stop_value=float(dial_data.get("stop_value", 0.0)),
            clockwise_value=float(dial_data.get("clockwise_value", 0.8)),
            counterclockwise_value=float(dial_data.get("counterclockwise_value", -0.8)),
            turn_duration_seconds=float(dial_data.get("turn_duration_seconds", 0.6)),
            settle_seconds=float(dial_data.get("settle_seconds", 0.1)),
        ),
        audio=AudioConfig(
            output_device_hint=str(audio_data.get("output_device_hint", "")),
            media_library_dir=str(audio_data.get("media_library_dir", "../media")),
            generated_audio_dir=str(audio_data.get("generated_audio_dir", "../output")),
            default_volume=int(audio_data.get("default_volume", 55)),
        ),
        raw=data,
    )
