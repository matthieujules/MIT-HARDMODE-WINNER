# 2D Room Map — Converged Design

## Convergence: All 3 models (Codex/Gemini/Claude) agreed on these decisions

### 1. State Representation
- **Live spatial state** in `state.json` under a `spatial` key (device positions, x/y/theta)
- **Static room layout** in `data/room.json` (dimensions, furniture, zones, waypoints)
- `write_state` upgraded to **deep merge** to avoid wiping nested spatial data
- Each device has: `x_cm`, `y_cm`, `theta_deg`, `fixed` (bool), `source`, `confidence`
- Rover has additional: `motion` (current movement target for animation)

### 2. Position Tracking
- **Stationary devices**: positions from room.json anchors, never change
- **Rover**: position updated when master calls `send_to_rover` with optional `target` param
- Master gets spatial context (room layout + current positions) in prompt
- Master outputs explicit waypoint or coordinate targets for rover
- Control plane updates spatial state before dispatching to physical device
- Dashboard animates movement client-side (interpolation over duration)

### 3. Room Configuration (`data/room.json`)
- Units: centimeters, origin: top-left
- Room dimensions (width_cm, height_cm)
- **Furniture** as obstacles with labels: desks, chairs, shelves, etc.
- **Zones**: labeled areas (desk_area, lounge, etc.)
- **Anchors**: fixed device mount points
- **Waypoints**: named positions for rover navigation (wp_desk, wp_center, wp_dock)

### 4. API Design
- `GET /room` — static room.json
- `GET /spatial` — current spatial snapshot from state.json
- `POST /spatial/calibrate` — manual "set rover here" override (drag-drop from dashboard)
- Keep existing 2s polling for dashboard updates (simple, works)

### 5. Dashboard Rendering
- **SVG** — best for 4 devices + labels + furniture + zones + pulses + trails
- Ghost rover for movement intent (translucent at target)
- Pulse rings when commands dispatch
- Rover movement trail (fading path)
- Device status indicators (online/offline glow)
- Drag-and-drop rover for manual calibration

### 6. Camera Integration Path (future)
- ArUco markers on rover + 4 room corners
- OpenCV detects markers, homography maps pixel->room coords
- Camera writes corrected pose into state.json spatial
- Fallback to command estimate if camera drops

---

## Implementation Plan

### Backend (control_plane/)
1. `data/room.json` — static room layout (includes furniture)
2. `control_plane/spatial.py` — spatial module (load room, init state, deep merge, waypoint resolution, position update, bounds clamping)
3. `control_plane/state.py` — deep merge in write_state, add read_room_config, init spatial on startup
4. `control_plane/app.py` — GET /room, GET /spatial, POST /spatial/calibrate, spatial update on rover dispatch
5. `control_plane/master.py` — spatial context in prompt, extend send_to_rover tool with optional target

### Frontend (dashboard/)
6. `dashboard/map.js` — SVG 2D room map replacing scene.js
7. `dashboard/index.html` — swap Three.js for map.js
8. `dashboard/styles.css` — map-specific styles

### Testing
9. Unit tests for spatial module
10. Integration test: send drive command -> verify position updates
11. Visual verification of dashboard
