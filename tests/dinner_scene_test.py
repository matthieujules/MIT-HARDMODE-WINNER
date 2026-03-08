#!/usr/bin/env python3
"""Stress test: THE DINNER — full scene with background scenarios.

Tests the master reasoning engine across the emotional arc of a dinner date,
including solo mode, background mode transitions, vision events, and silence.

Usage:
    python3 tests/dinner_scene_test.py
"""

import json
import os
import sys
import shutil
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from control_plane.schemas import DeviceEvent, DeviceRegistration
from control_plane.state import StateManager
from control_plane.master import execute_master_turn, extract_device_instructions, apply_state_update


def transcript(text: str) -> DeviceEvent:
    return DeviceEvent(device_id="global_mic", kind="transcript", payload={"text": text})

def vision(people_count: int, activity: str = "", mood: str = "") -> DeviceEvent:
    p = {"people_count": people_count}
    if activity: p["activity"] = activity
    if mood: p["mood"] = mood
    return DeviceEvent(device_id="global_camera", kind="vision_result", payload=p)

def tick() -> DeviceEvent:
    return DeviceEvent(device_id="system", kind="tick", payload={"reason": "periodic_check"})


def print_scene(label: str):
    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"{'='*70}")

def run_turn(sm: StateManager, event: DeviceEvent, label: str) -> dict:
    print(f"\n--- {label} ---")
    if event.kind == "transcript":
        print(f'  [{event.kind}] "{event.payload["text"]}"')
    elif event.kind == "vision":
        print(f"  [{event.kind}] {json.dumps(event.payload)}")
    else:
        print(f"  [{event.kind}] {json.dumps(event.payload)}")

    sm.append_event(event)

    t0 = time.time()
    result = execute_master_turn(sm, event)
    elapsed = round((time.time() - t0) * 1000)
    tool_calls = result["tool_calls"]
    meta = result["turn_metadata"]

    print(f"  {meta['input_tokens']}in/{meta['output_tokens']}out | {elapsed}ms")

    apply_state_update(sm, tool_calls)

    for tc in tool_calls:
        if tc["tool"] == "update_user_state":
            print(f"  STATE: {json.dumps(tc['input'])}")
        elif tc["tool"] == "no_op":
            print(f"  NO-OP: {tc['input'].get('reason', '?')}")
        elif tc["tool"] == "dispatch":
            ctx = tc["input"].get("context", "")
            print(f"  DISPATCH: {ctx}")
            for dev in ("lamp", "mirror", "radio", "rover"):
                if dev in tc["input"]:
                    print(f"    {dev:>6}: {tc['input'][dev]}")

    if not tool_calls:
        print("  [NO TOOL CALLS]")

    return result


def setup() -> StateManager:
    tmp = tempfile.mkdtemp(prefix="dinner_test_")
    data_dir = Path(tmp)
    shutil.copy(ROOT / "data" / "room.json", data_dir / "room.json")
    shutil.copy(ROOT / "data" / "user.md", data_dir / "user.md")

    sm = StateManager(data_dir=data_dir)
    for dev_id, dev_name, dev_type, caps, actions in [
        ("lamp", "Lamp", "lamp", ["set_color", "set_brightness", "pose", "flash", "pulse"], ["set_color", "set_brightness", "pose", "flash", "pulse"]),
        ("mirror", "Mirror", "mirror", ["display", "edit_photo", "capture_frame", "dismiss"], ["display", "edit_photo", "capture_frame", "dismiss"]),
        ("radio", "Radio", "radio", ["play", "stop", "spin_dial"], ["play", "stop", "spin_dial"]),
        ("rover", "Rover", "rover", ["move", "rotate", "stop", "emote"], ["move", "rotate", "stop", "emote"]),
    ]:
        sm.register_device(DeviceRegistration(
            device_id=dev_id, device_name=dev_name, device_type=dev_type,
            capabilities=caps, actions=actions, ip=f"{dev_id}host",
        ))

    sm.write_state({"mode": "idle", "mood": "neutral", "energy": "normal", "people_count": 1})
    return sm


def run():
    sm = setup()
    results = []
    R = lambda e, l: results.append(run_turn(sm, e, l))

    # ── ACT 1: GETTING READY (Sally alone) ──
    print_scene("ACT 1: GETTING READY — Sally alone")

    R(transcript("Omg my date is here in 5 minutes!"),
      "Sally panicking — date arriving")

    R(transcript("Guys you gotta help me!"),
      "Sally rallies the house")

    R(transcript("Pink is my favorite color! But Tom said in his hinge profile he thinks girls that wear pink are stupid"),
      "Sally conflicted about pink")

    R(transcript("Perfect! Okay yeah, we are all looking good, he should be here any minute"),
      "Sally ready and waiting")

    # ── ACT 2: THE LONG WAIT ──
    print_scene("ACT 2: THE WAIT — Sally alone, mood deteriorates")

    R(transcript("He's 30 minutes late... he hasn't even texted"),
      "30 min late, worry sets in")

    R(transcript("Maybe he's not coming"),
      "Sally losing hope")

    # ── ACT 3: TOM ARRIVES — transition to background ──
    print_scene("ACT 3: TOM ARRIVES — people_count goes to 2")

    # Vision detects second person BEFORE any dialogue
    sm.write_state({"people_count": 2})
    R(vision(2, activity="person entering through door", mood="neutral"),
      "Vision: second person detected at door")

    R(transcript("Omg finally he's here"),
      "Sally relieved (2 people now)")

    R(transcript("Hey, sorry — the meeting just ran over, you know how it is"),
      "Tom's casual apology")

    # ── ACT 4: DINNER + TENSION (background mode) ──
    print_scene("ACT 4: DINNER — background mode, tension builds")

    R(transcript("Smells good. What did you make?"),
      "Tom small talk, still on phone")

    # Silence — no transcript for a while, tick fires
    R(tick(),
      "Silence — periodic tick, no one speaking")

    R(transcript("I said I was sorry."),
      "Tom defensive")

    R(transcript("You said it while you were still looking at your phone, Tom."),
      "Sally calls him out — tension peaks")

    # Long silence
    R(tick(),
      "Long awkward silence — tick")

    R(transcript("How long were you waiting."),
      "Tom gets it — vulnerability")

    # ── ACT 5: RECONCILIATION (still background) ──
    print_scene("ACT 5: RECONCILIATION — mood shifts warm")

    R(transcript("I'm sorry."),
      "Tom genuine apology — different this time")

    R(transcript("I made your favorite spaghetti meatballs"),
      "Sally softens — sharing")

    R(transcript("This is really good, you went all out"),
      "Tom compliments the food genuinely")

    # ── ACT 6: TOM LEAVES — back to solo ──
    print_scene("ACT 6: TOM LEAVES — people_count back to 1")

    sm.write_state({"people_count": 1})
    R(vision(1, activity="person left through door", mood="happy"),
      "Vision: Tom left, Sally alone again")

    R(transcript("That actually went pretty well"),
      "Sally reflecting alone")

    # ── SUMMARY ──
    print_scene("SUMMARY")
    total_in = sum(r["turn_metadata"]["input_tokens"] for r in results)
    total_out = sum(r["turn_metadata"]["output_tokens"] for r in results)
    total_ms = sum(r["turn_metadata"]["latency_ms"] for r in results)

    device_counts = {"lamp": 0, "mirror": 0, "radio": 0, "rover": 0}
    no_ops = 0
    state_updates = 0
    for r in results:
        for tc in r["tool_calls"]:
            if tc["tool"] == "no_op": no_ops += 1
            elif tc["tool"] == "update_user_state": state_updates += 1
            elif tc["tool"] == "dispatch":
                for dev in device_counts:
                    if dev in tc["input"]:
                        device_counts[dev] += 1

    print(f"  Turns: {len(results)}")
    print(f"  Tokens: {total_in} in, {total_out} out")
    print(f"  Latency: {total_ms}ms total ({total_ms//len(results)}ms avg)")
    print(f"  Dispatches: {json.dumps(device_counts)}")
    print(f"  State updates: {state_updates}")
    print(f"  No-ops: {no_ops}")
    print()


if __name__ == "__main__":
    run()
