#!/bin/bash
# THE DINNER — live sequential test through the control plane
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
echo "║           THE DINNER — Live Sequential Test          ║"
echo "║     Open http://localhost:8000 to watch dashboard    ║"
echo "╚══════════════════════════════════════════════════════╝"

# Set people_count to 1 (Sally alone)
set_people 1

# ── ACT 1: GETTING READY ──
echo ""
echo "═══════════════════════════════════════════════════════"
echo "  ACT 1: GETTING READY — Sally alone, date in 5 min"
echo "═══════════════════════════════════════════════════════"

send "Omg my date is here in 5 minutes!" "Sally panicking"
wait_for_master 15

send "Guys you gotta help me!" "Sally rallies the house"
wait_for_master 15

send "Pink is my favorite color! But Tom said in his hinge profile he thinks girls that wear pink are stupid" "Sally conflicted about pink"
wait_for_master 15

send "Perfect! Okay yeah, we are all looking good, he should be here any minute" "Sally ready and waiting"
wait_for_master 15

# ── ACT 2: THE WAIT ──
echo ""
echo "═══════════════════════════════════════════════════════"
echo "  ACT 2: THE WAIT — mood deteriorates"
echo "═══════════════════════════════════════════════════════"

send "He's 30 minutes late... he hasn't even texted" "30 min late"
wait_for_master 15

send "Maybe he's not coming" "Sally losing hope"
wait_for_master 15

# ── ACT 3: TOM ARRIVES ──
echo ""
echo "═══════════════════════════════════════════════════════"
echo "  ACT 3: TOM ARRIVES — background mode (people=2)"
echo "═══════════════════════════════════════════════════════"

set_people 2
send_vision 2 "person entering through door" "Vision: second person at door"
wait_for_master 15

send "Omg finally he's here" "Sally relieved"
wait_for_master 15

send "Hey, sorry — the meeting just ran over, you know how it is" "Tom casual apology"
wait_for_master 15

# ── ACT 4: DINNER + TENSION ──
echo ""
echo "═══════════════════════════════════════════════════════"
echo "  ACT 4: TENSION — background mode"
echo "═══════════════════════════════════════════════════════"

send "Smells good. What did you make?" "Tom small talk"
wait_for_master 15

send "I said I was sorry." "Tom defensive"
wait_for_master 15

send "You said it while you were still looking at your phone, Tom." "Sally calls him out"
wait_for_master 15

send "How long were you waiting." "Tom vulnerability"
wait_for_master 15

# ── ACT 5: RECONCILIATION ──
echo ""
echo "═══════════════════════════════════════════════════════"
echo "  ACT 5: RECONCILIATION"
echo "═══════════════════════════════════════════════════════"

send "I'm sorry." "Tom genuine apology"
wait_for_master 15

send "I made your favorite spaghetti meatballs" "Sally softens"
wait_for_master 15

send "This is really good, you went all out" "Tom genuine compliment"
wait_for_master 15

# ── ACT 6: TOM LEAVES ──
echo ""
echo "═══════════════════════════════════════════════════════"
echo "  ACT 6: TOM LEAVES — back to solo"
echo "═══════════════════════════════════════════════════════"

set_people 1
send_vision 1 "person left through door" "Vision: Tom left"
wait_for_master 15

send "That actually went pretty well" "Sally reflecting"
wait_for_master 15

# ── SUMMARY ──
echo ""
echo "═══════════════════════════════════════════════════════"
echo "  DONE — check the dashboard and master-log"
echo "═══════════════════════════════════════════════════════"
echo ""
echo "  Dashboard: http://localhost:8000"
echo "  Master log: curl localhost:8000/master-log | python3 -m json.tool"
echo "  State: curl localhost:8000/state | python3 -m json.tool"
echo ""
