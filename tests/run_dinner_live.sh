#!/bin/bash
# THE DINNER — live sequential test through the control plane
# Scripted hackathon demo: Tom at home, Lucy arrives late
# Open http://localhost:8000 to watch the dashboard update in real-time
#
# Usage: bash tests/run_dinner_live.sh

BASE="http://localhost:8000"

send() {
    local text="$1"
    local label="$2"
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  $label"
    echo "  >> \"$text\""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    curl -s -X POST "$BASE/events" \
        -H 'Content-Type: application/json' \
        -d "{\"device_id\":\"global_mic\",\"kind\":\"transcript\",\"payload\":{\"text\":\"$text\"}}" \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print('  Status:', d.get('status','?'))" 2>/dev/null
}

send_vision() {
    local people="$1"
    local activity="$2"
    local label="$3"
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  $label"
    echo "  >> [VISION] people_count=$people, activity=$activity"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    curl -s -X POST "$BASE/events" \
        -H 'Content-Type: application/json' \
        -d "{\"device_id\":\"global_camera\",\"kind\":\"vision_result\",\"payload\":{\"people_count\":$people,\"activity\":\"$activity\"}}" \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print('  Status:', d.get('status','?'))" 2>/dev/null
}

SEEN_RESULTS=0
wait_for_master() {
    local seconds="${1:-15}"
    echo "  ... waiting ${seconds}s for master + device agents ..."
    sleep "$seconds"
    # Show latest master log entry + device action results
    curl -s "$BASE/master-log" | python3 -c "
import sys, json
entries = json.load(sys.stdin)
if entries:
    e = entries[-1]
    # Show the transcript/trigger input
    trigger = e.get('trigger', {})
    payload = trigger.get('payload', {})
    text = payload.get('text', '')
    kind = trigger.get('kind', '?')
    if text:
        print(f'  INPUT: \"{text}\"')
    elif kind == 'vision_result':
        pc = payload.get('people_count', '?')
        print(f'  INPUT: [VISION] people_count={pc}')
    tc = e.get('tool_calls', [])
    for t in tc:
        if t.get('tool') == 'update_user_state':
            print(f\"  STATE: {json.dumps(t['input'])}\")
        elif t.get('tool') == 'no_op':
            print(f\"  NO-OP: {t['input'].get('reason','?')}\")
        elif t.get('tool') == 'dispatch':
            ctx = t['input'].get('context','')
            print(f'  DISPATCH: {ctx[:100]}')
            for dev in ('lamp','mirror','radio','rover'):
                if dev in t['input']:
                    print(f'    {dev:>6}: {t[\"input\"][dev][:80]}')
    lat = e.get('latency_ms', '?')
    print(f'  Master latency: {lat}ms')
else:
    print('  (no master log entries yet)')
" 2>/dev/null
    # Show NEW device action_result events (skip already-seen ones)
    curl -s "$BASE/events" | python3 -c "
import sys, json, os
events = json.load(sys.stdin)
results = [e for e in events if e.get('kind') == 'action_result']
skip = $SEEN_RESULTS
new_results = results[skip:]
# Write new count to temp file for shell to pick up
open('/tmp/claudehome_seen', 'w').write(str(len(results)))
if new_results:
    print('  Device agent responses:')
    for r in new_results:
        dev = r.get('device_id', '?')
        p = r.get('payload', {})
        status = p.get('status', '?')
        detail = p.get('detail', '')[:120]
        elog = p.get('execution_log', [])
        tools_used = [step.get('tool','?') for step in elog if isinstance(step, dict) and 'tool' in step]
        tools_str = ' -> '.join(tools_used) if tools_used else ''
        print(f'    {dev:>10}: [{status}] {detail}')
        if tools_str:
            print(f'               tools: {tools_str}')
" 2>/dev/null
    SEEN_RESULTS=$(cat /tmp/claudehome_seen 2>/dev/null || echo "$SEEN_RESULTS")
}

set_people() {
    local count="$1"
    # Update state directly (the manual_override API expects different payload schema)
    python3 -c "
import json
state = json.loads(open('data/state.json').read())
state['people_count'] = $count
open('data/state.json','w').write(json.dumps(state))
"
    echo "  [set people_count=$count]"
}

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║        THE DINNER — Hackathon Demo Script            ║"
echo "║     Tom at home, Lucy arrives late for dinner        ║"
echo "║     Open http://localhost:8000 to watch dashboard    ║"
echo "╚══════════════════════════════════════════════════════╝"

# Set people_count to 1 (Tom alone)
set_people 1

# ── ACT 1: GETTING READY ── (Tom alone, objects wake up)
echo ""
echo "═══════════════════════════════════════════════════════"
echo "  ACT 1: GETTING READY — Tom alone, date in 5 min"
echo "═══════════════════════════════════════════════════════"

# Beat 1: Radio plays "A date!" + Careless Whisper, Lamp flashes pink
send "My date gets here in 5 minutes" "Beat 1: Tom announces his date"
wait_for_master 15

# Beat 2: Radio plays "Breath mint", Rover delivers mint
send "Haha funny, that's enough. You guys gotta help me" "Beat 2: Tom asks for help"
wait_for_master 15

# Beat 3: Radio plays "A classic style", Mirror shows outfit with tie
send "Hmm, what should I wear" "Beat 3: Tom asks about outfit"
wait_for_master 15

# Beat 4: Lamp nods warm amber, Rover celebrates, Radio plays Cheerful
send "Perfect!" "Beat 4: Tom approves"
wait_for_master 15

# Beat 5: Transition — Tom sits down to wait
send "Yeah, we are all looking good, she should be here any minute now" "Beat 5: Tom ready and waiting"
wait_for_master 15

# ── ACT 2: THE WAIT ── (mood deteriorates)
echo ""
echo "═══════════════════════════════════════════════════════"
echo "  ACT 2: THE WAIT — mood crashes over an hour"
echo "═══════════════════════════════════════════════════════"

# Beat 6: Lamp dims, Radio plays Sad/Adele, Rover sad wobble
send "She's still not here... it's been over an hour" "Beat 6: Tom losing hope"
wait_for_master 15

# ── ACT 3: LUCY ARRIVES ── (background mode: people=2)
echo ""
echo "═══════════════════════════════════════════════════════"
echo "  ACT 3: LUCY ARRIVES — the home snaps back to life"
echo "═══════════════════════════════════════════════════════"

set_people 2

# Beat 7: Radio plays Careless Whisper, Lamp full brightness, Mirror warm visual
send_vision 2 "second person entering through door" "Beat 7: Vision — Lucy at the door"
wait_for_master 15

# ── ACT 4: TENSION ── (Lucy on phone, Tom annoyed)
echo ""
echo "═══════════════════════════════════════════════════════"
echo "  ACT 4: TENSION — Lucy distracted, Tom frustrated"
echo "═══════════════════════════════════════════════════════"

# Beat 8: Radio stops, Rover bumps for attention, Lamp dims
send "Hey, sorry — the meeting just ran over, you know how it is" "Beat 8: Lucy casual apology (on phone)"
wait_for_master 15

# Beat 9: Lamp pulses toward Tom
send "Sorry! Shall we eat?" "Beat 9: Lucy tries to move on"
wait_for_master 15

# ── ACT 5: RECONCILIATION ── (genuine connection returns)
echo ""
echo "═══════════════════════════════════════════════════════"
echo "  ACT 5: RECONCILIATION — the evening is saved"
echo "═══════════════════════════════════════════════════════"

# Beat 10: Lamp full brightness, Radio plays "Better late than never", Mirror warm
send "I'm really sorry. Thank you for planning such a lovely date" "Beat 10: Lucy genuine apology"
wait_for_master 15

# Beat 11: Rover delivers bread, Radio plays Careless Whisper again
send "Yeah I deserve that" "Beat 11: Lucy laughs, warmth returns"
wait_for_master 15

# Beat 12: Lamp focuses warm light on dish
send "I made your favorite spaghetti meatballs" "Beat 12: Tom talks about the food"
wait_for_master 15

# ── FINALE ──
echo ""
echo "═══════════════════════════════════════════════════════"
echo "  FINALE — ClaudeHome"
echo "═══════════════════════════════════════════════════════"

# Beat 13: Radio plays hahaha, Lamp warm steady glow
send "That's not funny" "Beat 13: Final banter"
wait_for_master 15

# ── SUMMARY ──
echo ""
echo "═══════════════════════════════════════════════════════"
echo "  DONE — THE DINNER complete"
echo "═══════════════════════════════════════════════════════"
echo ""
echo "  Dashboard:   http://localhost:8000"
echo "  Master log:  curl localhost:8000/master-log | python3 -m json.tool"
echo "  State:       curl localhost:8000/state | python3 -m json.tool"
echo ""
echo "  Expected device actions:"
echo "    Radio:  7 (19+I, 07, 01, E, F, I, 06, I, 08)"
echo "    Lamp:   7 (flash pink, amber nod, dim, full bright, pulse, flood, dish focus)"
echo "    Rover:  5 (deliver mint, excitement, sad, bump, deliver bread)"
echo "    Mirror: 3 (outfit+tie, warm romantic, cool tension, warm golden)"
echo ""
