"""Dinner Scene: Full E2E test of the ClaudeHome master reasoning pipeline.

Sends a sequence of transcript events simulating the dinner scenario,
waits for master reasoning, and captures all responses.

Usage: python3 tests/dinner_scene.py
"""

import json
import pathlib
import time

import requests

BASE = "http://localhost:8000"
STATE_PATH = pathlib.Path("data/state.json")


def set_state(patch):
    """Directly write state patch to file."""
    current = json.loads(STATE_PATH.read_text())
    for k, v in patch.items():
        if k == "spatial" and isinstance(v, dict):
            if "spatial" not in current:
                current["spatial"] = {}
            for sk, sv in v.items():
                if isinstance(sv, dict) and sk in current["spatial"]:
                    current["spatial"][sk].update(sv)
                else:
                    current["spatial"][sk] = sv
        else:
            current[k] = v
    STATE_PATH.write_text(json.dumps(current, default=str))
    print(f"  [STATE] {patch}")


def set_people(count, names=None):
    """Set people_count and adjust people array."""
    state = json.loads(STATE_PATH.read_text())
    names = names or ["Sally", "Tom"]
    people = []
    positions = [(248, 248), (280, 260), (200, 200)]
    for i in range(count):
        x, y = positions[i] if i < len(positions) else (250, 250)
        people.append({
            "x_cm": x, "y_cm": y, "confidence": 0.95,
            "source": "camera_detector",
            "id": f"person_{i+1}",
            "label": names[i] if i < len(names) else f"User {i+1}"
        })
    state["people_count"] = count
    state.setdefault("spatial", {})["people"] = people
    if people:
        state["spatial"]["user"] = {
            "x_cm": people[0]["x_cm"], "y_cm": people[0]["y_cm"],
            "label": people[0]["label"], "source": "camera_detector"
        }
    STATE_PATH.write_text(json.dumps(state, default=str))
    print(f"  [PEOPLE] count={count} ({', '.join(p['label'] for p in people)})")


def send_transcript(text):
    resp = requests.post(f"{BASE}/events", json={
        "device_id": "global_mic",
        "kind": "transcript",
        "payload": {"text": text}
    }, timeout=30)
    return resp.json()


def send_vision(description):
    # Vision events trigger master reasoning synchronously, so they can take 30s+
    resp = requests.post(f"{BASE}/events", json={
        "device_id": "camera",
        "kind": "vision_result",
        "payload": {"analysis": {"description": description}, "source_device": "camera"}
    }, timeout=120)
    return resp.json()


def get_log_count():
    return len(requests.get(f"{BASE}/master-log", params={"limit": 500}, timeout=10).json())


def wait_for_master(count_before, timeout=60):
    """Poll master-log until a new entry appears. Tolerant of slow wifi."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            log = requests.get(f"{BASE}/master-log", params={"limit": 500}, timeout=10).json()
            if len(log) > count_before:
                return log[-1]
        except Exception:
            pass
        time.sleep(3)
    print("  [TIMEOUT] No master response within timeout")
    return None


def print_response(entry):
    if entry is None:
        print("  [NO RESPONSE]\n")
        return

    outcome = entry.get("outcome", "?")
    latency = entry.get("latency_ms", "?")

    if outcome == "error":
        print(f"  [ERROR] {entry.get('error', '?')} (logged, continuing)\n")
        return

    print(f"  Outcome: {outcome} | Latency: {latency}ms")

    if outcome == "no_op":
        print(f"  no_op: {entry.get('no_op_reason', '?')}")
    elif outcome in ("dispatch", "state_update_only"):
        # State updates
        for tc in entry.get("tool_calls", []):
            if tc["tool"] == "update_user_state":
                print(f"  State -> {tc['input']}")
        # Dispatches
        for d in entry.get("dispatches", []):
            instr = d["instruction"][:140]
            print(f"    -> {d['device']:8s} [{d['result']['status']}]: {instr}")

    print()


def run_beat(beat_num, name, transcript=None, vision=None, pre_state=None, pre_people=None):
    print(f"\n{'='*70}")
    print(f"  BEAT {beat_num}: {name}")
    print(f"{'='*70}")

    if pre_people is not None:
        set_people(*pre_people) if isinstance(pre_people, tuple) else set_people(pre_people)
    if pre_state:
        set_state(pre_state)

    count_before = get_log_count()

    if transcript:
        print(f"  SAY: \"{transcript}\"")
        try:
            send_transcript(transcript)
        except requests.exceptions.Timeout:
            print("  [request timed out, master may still be processing]")
    elif vision:
        print(f"  SEE: \"{vision[:100]}...\"")
        try:
            send_vision(vision)
        except requests.exceptions.Timeout:
            print("  [request timed out, master may still be processing]")

    print(f"  Waiting for master...")
    entry = wait_for_master(count_before)
    print_response(entry)

    # Small delay between beats to avoid Cerebras rate limits on lamp agent
    time.sleep(5)
    return entry


def main():
    # Verify server
    try:
        h = requests.get(f"{BASE}/health", timeout=5).json()
        print(f"Server OK: {h}")
    except Exception as e:
        print(f"Server not reachable: {e}")
        return

    print("\n" + "#"*70)
    print("  THE DINNER - ClaudeHome Full Scene Test")
    print("#"*70)

    # ── RESET ──
    print("\nResetting state...")
    set_state({"mode": "idle", "mood": "neutral", "energy": "normal", "activity": None, "voice_lock": {}, "overrides": {}})
    set_people(1, ["Sally"])
    # Clear logs
    pathlib.Path("data/event_log.jsonl").write_text("")
    pathlib.Path("data/master_log.jsonl").write_text("")
    time.sleep(1)

    # ══════════════════════════════════════════════════════════════════
    #  ACT 1: GETTING READY (Sally alone, people_count=1)
    # ══════════════════════════════════════════════════════════════════
    print("\n" + "~"*70)
    print("  ACT 1: GETTING READY")
    print("~"*70)

    run_beat(1, "Sally panicking",
        transcript="Oh my god my date is here in 5 minutes! I am so not ready!")

    run_beat(2, "Sally asks for help",
        transcript="Guys you gotta help me, I need to look perfect tonight")

    run_beat(3, "What to wear",
        transcript="Hmm, I wonder what I should wear tonight")

    run_beat(4, "Color dilemma",
        transcript="Pink is my favorite color! But Tom said in his hinge profile he thinks girls that wear pink are stupid. What do you think?")

    run_beat(5, "Sally in red dress - looks great",
        vision="Sally is standing in front of the mirror wearing a stunning red dress. She looks confident and radiant, doing a little twirl. She is smiling at her reflection.")

    run_beat(6, "Ready and waiting",
        transcript="Okay yeah, we are all looking good. He should be here any minute now.",
        pre_state={"activity": "waiting for date to arrive, table is set"})

    # ══════════════════════════════════════════════════════════════════
    #  ACT 2: THE LONG WAIT (Sally alone, an hour passes)
    # ══════════════════════════════════════════════════════════════════
    print("\n" + "~"*70)
    print("  ACT 2: THE LONG WAIT")
    print("~"*70)

    run_beat(7, "An hour later - still alone",
        pre_state={"mood": "anxious", "energy": "low", "activity": "waiting alone at dinner table, date is an hour late"},
        vision="Sally is sitting alone at a beautifully set dinner table. Candles have burned down. She checks her phone repeatedly, looking worried and disappointed. The food is getting cold. An hour has passed.")

    # ══════════════════════════════════════════════════════════════════
    #  ACT 3: TOM ARRIVES (people_count=2, BACKGROUND MODE)
    # ══════════════════════════════════════════════════════════════════
    print("\n" + "~"*70)
    print("  ACT 3: TOM ARRIVES (background mode)")
    print("~"*70)

    run_beat(8, "Tom walks in late",
        pre_people=(2, ["Sally", "Tom"]),
        pre_state={"activity": "Tom arriving an hour late to dinner"},
        transcript="Sorry for being late, the meeting just ran over, you know how it is")

    run_beat(9, "Tom distracted on phone",
        pre_state={"activity": "tense dinner, Tom still on phone"},
        transcript="Smells good. What did you make?")

    run_beat(10, "Tension building",
        vision="Tom is at the dinner table still typing on his phone. Sally sits across with arms crossed, visibly hurt and annoyed. She is staring at him. The tension is thick.")

    run_beat(11, "Sally confronts him",
        transcript="You said it while you were still looking at your phone, Tom.")

    run_beat(12, "The long silence",
        vision="Long uncomfortable silence. Tom has put his phone down. Both sitting at the table not speaking. Sally looking away. Tom staring at his hands. The weight of the moment is heavy.")

    run_beat(13, "Tom genuinely apologizes",
        pre_state={"activity": "genuine apology, emotional turning point"},
        transcript="I am sorry. Really. How long were you waiting?")

    run_beat(14, "Dinner together - warming up",
        pre_state={"activity": "sharing dinner together, mood warming"},
        transcript="I made your favorite, spaghetti and meatballs. The sauce is my grandmas recipe, I spent all afternoon on it.")

    # ══════════════════════════════════════════════════════════════════
    #  SUMMARY
    # ══════════════════════════════════════════════════════════════════
    print("\n" + "#"*70)
    print("  SCENE COMPLETE - THE DINNER")
    print("#"*70)

    state = requests.get(f"{BASE}/state", timeout=10).json()
    print(f"\nFinal state: mode={state.get('mode')}, mood={state.get('mood')}, energy={state.get('energy')}")
    print(f"People: {state.get('people_count')}")

    log = requests.get(f"{BASE}/master-log", params={"limit": 500}, timeout=10).json()
    successful = [e for e in log if e.get("outcome") not in ("error", None)]
    errors = [e for e in log if e.get("outcome") == "error"]

    device_counts = {}
    for entry in successful:
        for d in entry.get("dispatches", []):
            dev = d["device"]
            device_counts[dev] = device_counts.get(dev, 0) + 1

    print(f"\nBeats: {len(log)} total, {len(successful)} succeeded, {len(errors)} errors")
    print(f"\nDevice actions:")
    for dev, count in sorted(device_counts.items()):
        print(f"  {dev}: {count}")

    total_latency = sum(e.get("latency_ms", 0) or 0 for e in successful)
    total_in = sum(e.get("input_tokens", 0) or 0 for e in successful)
    total_out = sum(e.get("output_tokens", 0) or 0 for e in successful)
    print(f"\nTotal latency: {total_latency/1000:.1f}s (avg {total_latency/max(len(successful),1)/1000:.1f}s/beat)")
    print(f"Total tokens: {total_in:,} in / {total_out:,} out")


if __name__ == "__main__":
    main()
