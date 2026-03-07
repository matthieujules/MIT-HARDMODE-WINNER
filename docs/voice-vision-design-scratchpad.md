# Voice + Vision Pipeline — Converged Design

## Voice Pipeline (Speech → Transcript → Control Plane)

### Architecture: Sidecar process on laptop
- `voice/voice_service.py` — standalone Python process
- Communicates with control plane via `POST /events`
- Isolated from FastAPI event loop (no GIL contention)

### Flow
1. `sounddevice` captures 16kHz mono PCM16
2. Silero VAD detects speech start/end (30ms frames, 300ms silence threshold)
3. On speech end: accumulate audio buffer → send to STT
4. STT returns transcript → POST /events as DeviceEvent(kind="transcript", device_id="global_mic")
5. Control plane routes through existing pipeline (deterministic router or master reasoning)

### STT Choice: Groq Whisper API (primary) + local faster-whisper (fallback)
- Groq: 20-50ms for full transcript, offloads CPU, simple HTTP POST
- Local faster-whisper base.en: 200-500ms, no network dependency
- Configurable via env var STT_BACKEND=groq|local

### Voice Lock
- Before forwarding transcript, check GET /state for voice_lock
- If voice_lock active, suppress forwarding (but keep listening for emergency stop)
- Emergency stop patterns bypass voice lock (check locally before suppressing)

### Latency Budget (speech end → transcript ready)
- VAD silence detection: ~300ms
- Groq Whisper: ~50ms (network + processing)
- POST to control plane: ~1ms
- **Total: ~350ms** (excellent for hackathon)

---

## Vision Pipeline (Camera → Scene Analysis + Spatial Tracking)

### Architecture: Sidecar process on laptop
- `vision/vision_service.py` — standalone Python process
- Single camera, dual-rate processing

### Dual-Rate Processing
**Fast path (every frame, ~10-15fps):**
- ArUco marker detection via OpenCV
- If rover marker detected: POST /spatial/observe with corrected position
- Lightweight, local, no API calls

**Slow path (motion-gated, cooldown 8-10s):**
- Frame differencing for motion detection
- If significant motion + cooldown elapsed: capture JPEG (640x480, q80)
- POST /events as DeviceEvent(kind="frame")
- Control plane's vision.py calls Claude Vision API
- Analysis compared against current state
- If mood/people_count changed significantly → generate vision_result event → master reasoning

### Control Plane Integration
- `control_plane/vision.py` — new module:
  - `analyze_frame(image_b64)` → calls Claude Vision, returns analysis dict
  - `should_trigger_master(analysis, current_state)` → bool
  - Frame handler in app.py calls vision.py, conditionally creates vision_result event

### Spatial Integration (ArUco)
- `POST /spatial/observe` — new endpoint
  - Accepts: `{"device_id": "rover", "x_cm": N, "y_cm": N, "theta_deg": N, "confidence": 0.9, "source": "camera"}`
  - Updates spatial state, overriding command estimates with high-confidence camera data
  - Also accepts user position from person detection

---

## Convergence Points (Codex + Gemini + Claude agreed)
1. Both pipelines as sidecar processes, NOT in FastAPI
2. Silero VAD for voice activity detection
3. Single camera process for both spatial + scene analysis
4. Motion-gated Claude Vision (not every frame)
5. ArUco markers for spatial tracking (not color tracking)
6. Voice lock: suppress forwarding, not listening (emergency stop bypass)
7. Configurable STT backend (cloud primary, local fallback)
