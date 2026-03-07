"""Pydantic v2 schemas for the ClaudeHome control plane."""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Upward event envelope ──────────────────────────────────────────

class DeviceEvent(BaseModel):
    device_id: str
    kind: Literal[
        "transcript",
        "frame",
        "vision_result",
        "action_result",
        "heartbeat",
        "manual_override",
        "tick",
    ]
    ts: datetime = Field(default_factory=_utcnow)
    payload: dict


# ── Per-kind payload models (for validation where needed) ──────────

class TranscriptPayload(BaseModel):
    text: str


class FramePayload(BaseModel):
    image_b64: str
    resolution: list[int]
    trigger: str


class VisionResultPayload(BaseModel):
    analysis: dict
    previous_mood: str | None
    source_device: str


class ActionResultPayload(BaseModel):
    request_id: str
    status: Literal["ok", "error", "timeout", "offline"]
    detail: str


class ManualOverridePayload(BaseModel):
    target: str
    type: Literal["command", "spawn"]
    instruction: str | None = None
    action: str | None = None
    params: dict = Field(default_factory=dict)


class TickPayload(BaseModel):
    elapsed_since_last_interaction_s: int


# ── Downward messages (control plane → device) ────────────────────

class DeviceCommand(BaseModel):
    type: Literal["command"]
    action: str
    params: dict = Field(default_factory=dict)
    request_id: str


class DeviceSpawn(BaseModel):
    type: Literal["spawn"]
    instruction: str
    request_id: str
    max_iterations: int = 10
    time_budget_ms: int = 15000


# ── Device registry ───────────────────────────────────────────────

class DeviceRegistration(BaseModel):
    device_id: str
    device_name: str
    device_type: str
    capabilities: list[str]
    actions: list[str]
    ip: str


class DeviceInfo(DeviceRegistration):
    status: str = "online"
    last_seen: datetime | None = None
    is_virtual: bool = False
