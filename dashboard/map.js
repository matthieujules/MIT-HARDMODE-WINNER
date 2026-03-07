// map.js — 2D SVG room map for ClaudeHome dashboard
// Replaces scene.js (Three.js 3D wireframe)
// Renders a "mission control" floor plan with device icons, rover animation,
// command pulses, and drag-to-calibrate.

// ── Module state ────────────────────────────────────────────────────
let _container = null;
let _svg = null;
let _initialized = false;

// Room config (fetched once from /room)
let _roomConfig = null;
let _roomWidth = 500;
let _roomHeight = 400;

// SVG groups for layered rendering
let _gGrid = null;
let _gFurniture = null;
let _gWaypoints = null;
let _gPaths = null;
let _gTrail = null;
let _gDevices = null;
let _gPulses = null;
let _gUser = null;
let _gTooltip = null;

// Device elements keyed by device_id
const _deviceGroups = {};

// Pulse dedup
const _pulsedIds = new Set();
const _activePulses = [];

// Rover trail (recent positions)
const _roverTrail = [];
const MAX_TRAIL = 10;

// Rover animation state
let _roverAnimating = false;
let _roverAnimStart = null;
let _roverAnimDuration = 0;
let _roverAnimFrom = { x: 0, y: 0 };
let _roverAnimTo = { x: 0, y: 0 };
let _roverCurrentPos = { x: 0, y: 0 };
let _animFrameId = null;

// Trail render throttle
let _lastTrailRender = 0;

// Dragging state
let _dragging = false;
let _dragDeviceId = null;
let _dragOffset = { x: 0, y: 0 };

// Device accent colors
const DEVICE_COLORS = {
  lamp: '#00f0ff',
  mirror: '#8b5cf6',
  radio: '#f59e0b',
  rover: '#22c55e',
};

// Device status from last update
let _lastSpatial = null;
let _lastDevicesData = null;

// ── SVG namespace ───────────────────────────────────────────────────
const NS = 'http://www.w3.org/2000/svg';

function svgEl(tag, attrs = {}) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    el.setAttribute(k, v);
  }
  return el;
}

// ── Initialization ──────────────────────────────────────────────────

export async function initMap(containerElement) {
  if (_initialized) return;
  _container = containerElement;

  // Fetch room config
  try {
    const res = await fetch('/room');
    if (res.ok) {
      _roomConfig = await res.json();
      _roomWidth = _roomConfig.width_cm || 500;
      _roomHeight = _roomConfig.height_cm || 400;
    }
  } catch (e) {
    console.warn('Failed to fetch /room config, using defaults:', e);
  }

  // Create SVG
  _svg = svgEl('svg', {
    viewBox: `0 0 ${_roomWidth} ${_roomHeight}`,
    preserveAspectRatio: 'xMidYMid meet',
    class: 'room-map-svg',
  });
  _svg.style.width = '100%';
  _svg.style.height = '100%';
  _svg.style.display = 'block';

  // Add SVG filter definitions
  _buildDefs();

  // Create layer groups (render order matters)
  _gGrid = svgEl('g', { class: 'layer-grid' });
  _gFurniture = svgEl('g', { class: 'layer-furniture' });
  _gWaypoints = svgEl('g', { class: 'layer-waypoints' });
  _gPaths = svgEl('g', { class: 'layer-paths' });
  _gTrail = svgEl('g', { class: 'layer-trail' });
  _gUser = svgEl('g', { class: 'layer-user' });
  _gDevices = svgEl('g', { class: 'layer-devices' });
  _gPulses = svgEl('g', { class: 'layer-pulses' });
  _gTooltip = svgEl('g', { class: 'layer-tooltip', style: 'pointer-events:none' });

  _svg.appendChild(_gGrid);
  _svg.appendChild(_gFurniture);
  _svg.appendChild(_gWaypoints);
  _svg.appendChild(_gPaths);
  _svg.appendChild(_gTrail);
  _svg.appendChild(_gUser);
  _svg.appendChild(_gDevices);
  _svg.appendChild(_gPulses);
  _svg.appendChild(_gTooltip);

  _container.appendChild(_svg);

  // Draw static elements
  _drawGrid();
  _drawRoomBoundary();
  if (_roomConfig) {
    _drawFurniture(_roomConfig.furniture || []);
    _drawWaypoints(_roomConfig.waypoints || []);
  }

  // Wire up drag events
  _svg.addEventListener('mousedown', _onDragStart);
  _svg.addEventListener('mousemove', _onDragMove);
  _svg.addEventListener('mouseup', _onDragEnd);
  _svg.addEventListener('mouseleave', _onDragEnd);
  // Touch
  _svg.addEventListener('touchstart', _onTouchStart, { passive: false });
  _svg.addEventListener('touchmove', _onTouchMove, { passive: false });
  _svg.addEventListener('touchend', _onDragEnd);

  // Start animation loop
  _animFrameId = requestAnimationFrame(_animationLoop);

  _initialized = true;
}

// ── SVG Defs (filters for glow effects) ─────────────────────────────

function _buildDefs() {
  const defs = svgEl('defs');

  // Glow filters for each device color
  for (const [id, color] of Object.entries(DEVICE_COLORS)) {
    const filter = svgEl('filter', {
      id: `glow-${id}`,
      x: '-50%', y: '-50%',
      width: '200%', height: '200%',
    });

    const blur = svgEl('feGaussianBlur', {
      stdDeviation: '4',
      result: 'coloredBlur',
    });
    const merge = svgEl('feMerge');
    const mn1 = svgEl('feMergeNode', { in: 'coloredBlur' });
    const mn2 = svgEl('feMergeNode', { in: 'SourceGraphic' });
    merge.appendChild(mn1);
    merge.appendChild(mn2);
    filter.appendChild(blur);
    filter.appendChild(merge);
    defs.appendChild(filter);
  }

  // Soft glow for executing status (pulsing ring)
  const pulseFilter = svgEl('filter', {
    id: 'glow-pulse',
    x: '-100%', y: '-100%',
    width: '300%', height: '300%',
  });
  const pb = svgEl('feGaussianBlur', { stdDeviation: '6', result: 'pb' });
  const pm = svgEl('feMerge');
  pm.appendChild(svgEl('feMergeNode', { in: 'pb' }));
  pm.appendChild(svgEl('feMergeNode', { in: 'SourceGraphic' }));
  pulseFilter.appendChild(pb);
  pulseFilter.appendChild(pm);
  defs.appendChild(pulseFilter);

  // Command pulse glow
  const cmdFilter = svgEl('filter', {
    id: 'glow-cmd',
    x: '-100%', y: '-100%',
    width: '300%', height: '300%',
  });
  const cb = svgEl('feGaussianBlur', { stdDeviation: '3', result: 'cb' });
  const cm = svgEl('feMerge');
  cm.appendChild(svgEl('feMergeNode', { in: 'cb' }));
  cm.appendChild(svgEl('feMergeNode', { in: 'SourceGraphic' }));
  cmdFilter.appendChild(cb);
  cmdFilter.appendChild(cm);
  defs.appendChild(cmdFilter);

  _svg.appendChild(defs);
}

// ── Static drawing ──────────────────────────────────────────────────

function _drawGrid() {
  const spacing = 50; // every 50cm
  for (let x = spacing; x < _roomWidth; x += spacing) {
    const line = svgEl('line', {
      x1: x, y1: 0, x2: x, y2: _roomHeight,
      stroke: 'rgba(255,255,255,0.03)',
      'stroke-width': '0.5',
    });
    _gGrid.appendChild(line);
  }
  for (let y = spacing; y < _roomHeight; y += spacing) {
    const line = svgEl('line', {
      x1: 0, y1: y, x2: _roomWidth, y2: y,
      stroke: 'rgba(255,255,255,0.03)',
      'stroke-width': '0.5',
    });
    _gGrid.appendChild(line);
  }
}

function _drawRoomBoundary() {
  const rect = svgEl('rect', {
    x: 1, y: 1,
    width: _roomWidth - 2,
    height: _roomHeight - 2,
    rx: 6, ry: 6,
    fill: 'none',
    stroke: 'rgba(255,255,255,0.08)',
    'stroke-width': '1.5',
  });
  _gGrid.appendChild(rect);

  // Corner labels
  const label = svgEl('text', {
    x: 10, y: _roomHeight - 8,
    fill: 'rgba(255,255,255,0.12)',
    'font-size': '8',
    'font-family': "'JetBrains Mono', monospace",
  });
  label.textContent = `${_roomWidth}x${_roomHeight}cm`;
  _gGrid.appendChild(label);
}

function _drawFurniture(furniture) {
  for (const item of furniture) {
    const g = svgEl('g', { class: 'furniture-item' });

    const rect = svgEl('rect', {
      x: item.x_cm,
      y: item.y_cm,
      width: item.w_cm,
      height: item.h_cm,
      rx: 3, ry: 3,
      fill: 'rgba(255,255,255,0.025)',
      stroke: 'rgba(255,255,255,0.06)',
      'stroke-width': '0.5',
    });
    g.appendChild(rect);

    const label = svgEl('text', {
      x: item.x_cm + item.w_cm / 2,
      y: item.y_cm + item.h_cm / 2 + 3,
      fill: 'rgba(255,255,255,0.12)',
      'font-size': '7',
      'font-family': "'JetBrains Mono', monospace",
      'text-anchor': 'middle',
    });
    label.textContent = item.label;
    g.appendChild(label);

    _gFurniture.appendChild(g);
  }
}

function _drawWaypoints(waypoints) {
  for (const wp of waypoints) {
    const g = svgEl('g', { class: 'waypoint-item' });

    // Small cross
    const size = 4;
    const h = svgEl('line', {
      x1: wp.x_cm - size, y1: wp.y_cm,
      x2: wp.x_cm + size, y2: wp.y_cm,
      stroke: 'rgba(255,255,255,0.08)',
      'stroke-width': '0.5',
    });
    const v = svgEl('line', {
      x1: wp.x_cm, y1: wp.y_cm - size,
      x2: wp.x_cm, y2: wp.y_cm + size,
      stroke: 'rgba(255,255,255,0.08)',
      'stroke-width': '0.5',
    });
    g.appendChild(h);
    g.appendChild(v);

    // Dot
    const dot = svgEl('circle', {
      cx: wp.x_cm, cy: wp.y_cm, r: 1.5,
      fill: 'rgba(255,255,255,0.1)',
    });
    g.appendChild(dot);

    // Label (very faint)
    const label = svgEl('text', {
      x: wp.x_cm + 6,
      y: wp.y_cm + 3,
      fill: 'rgba(255,255,255,0.1)',
      'font-size': '6',
      'font-family': "'JetBrains Mono', monospace",
    });
    label.textContent = wp.label;
    g.appendChild(label);

    _gWaypoints.appendChild(g);
  }
}

// ── Device rendering ────────────────────────────────────────────────

function _getOrCreateDevice(deviceId) {
  if (_deviceGroups[deviceId]) return _deviceGroups[deviceId];

  const color = DEVICE_COLORS[deviceId] || '#e2e8f0';
  const g = svgEl('g', {
    class: `device-group device-${deviceId}`,
    'data-device-id': deviceId,
    cursor: deviceId === 'rover' ? 'grab' : 'default',
  });

  // Status ring (for executing pulse)
  const statusRing = svgEl('circle', {
    cx: 0, cy: 0, r: 14,
    fill: 'none',
    stroke: color,
    'stroke-width': '1',
    opacity: '0',
    class: 'device-status-ring',
  });
  g.appendChild(statusRing);

  // Device-specific icon
  const icon = _createDeviceIcon(deviceId, color);
  g.appendChild(icon);

  // Label
  const label = svgEl('text', {
    x: 0, y: 18,
    fill: color,
    'font-size': '7',
    'font-family': "'JetBrains Mono', monospace",
    'text-anchor': 'middle',
    'font-weight': '500',
    'letter-spacing': '0.05em',
    class: 'device-label',
  });
  label.textContent = deviceId.toUpperCase();
  g.appendChild(label);

  // Speaking indicator (hidden by default)
  const speakGroup = svgEl('g', { class: 'speak-indicator', opacity: '0' });
  for (let i = 0; i < 3; i++) {
    const bar = svgEl('rect', {
      x: -6 + i * 5,
      y: -20,
      width: 2,
      height: 6,
      rx: 1,
      fill: color,
      class: `speak-bar speak-bar-${i}`,
    });
    speakGroup.appendChild(bar);
  }
  g.appendChild(speakGroup);

  _gDevices.appendChild(g);
  _deviceGroups[deviceId] = {
    group: g,
    statusRing,
    speakIndicator: speakGroup,
    label,
    icon,
    color,
  };

  // Tooltip on hover
  g.addEventListener('mouseenter', (e) => _showTooltip(deviceId, e));
  g.addEventListener('mouseleave', _hideTooltip);

  return _deviceGroups[deviceId];
}

function _createDeviceIcon(deviceId, color) {
  const g = svgEl('g', { class: 'device-icon' });

  switch (deviceId) {
    case 'lamp': {
      // Outer glow circle
      const glow = svgEl('circle', {
        cx: 0, cy: 0, r: 10,
        fill: color,
        opacity: '0.1',
        filter: `url(#glow-${deviceId})`,
      });
      g.appendChild(glow);
      // Main circle
      const main = svgEl('circle', {
        cx: 0, cy: 0, r: 7,
        fill: 'rgba(0,0,0,0.6)',
        stroke: color,
        'stroke-width': '1.5',
      });
      g.appendChild(main);
      // Inner light dot
      const inner = svgEl('circle', {
        cx: 0, cy: 0, r: 3,
        fill: color,
        opacity: '0.8',
      });
      g.appendChild(inner);
      // Light rays
      for (let i = 0; i < 4; i++) {
        const angle = (i * 90) * Math.PI / 180;
        const ray = svgEl('line', {
          x1: Math.cos(angle) * 8,
          y1: Math.sin(angle) * 8,
          x2: Math.cos(angle) * 11,
          y2: Math.sin(angle) * 11,
          stroke: color,
          'stroke-width': '0.8',
          opacity: '0.4',
        });
        g.appendChild(ray);
      }
      break;
    }

    case 'mirror': {
      // Diamond shape
      const glow = svgEl('rect', {
        x: -10, y: -10,
        width: 20, height: 20,
        rx: 2,
        fill: color,
        opacity: '0.08',
        filter: `url(#glow-${deviceId})`,
        transform: 'rotate(45)',
      });
      g.appendChild(glow);
      const diamond = svgEl('rect', {
        x: -7, y: -7,
        width: 14, height: 14,
        rx: 1,
        fill: 'rgba(0,0,0,0.6)',
        stroke: color,
        'stroke-width': '1.5',
        transform: 'rotate(45)',
      });
      g.appendChild(diamond);
      // Reflection line
      const ref1 = svgEl('line', {
        x1: -3, y1: -3, x2: 3, y2: 3,
        stroke: color,
        'stroke-width': '0.8',
        opacity: '0.5',
      });
      g.appendChild(ref1);
      const ref2 = svgEl('line', {
        x1: -1, y1: -5, x2: 5, y2: 1,
        stroke: color,
        'stroke-width': '0.5',
        opacity: '0.3',
      });
      g.appendChild(ref2);
      break;
    }

    case 'radio': {
      // Speaker shape
      const glow = svgEl('rect', {
        x: -9, y: -9,
        width: 18, height: 18,
        rx: 3,
        fill: color,
        opacity: '0.08',
        filter: `url(#glow-${deviceId})`,
      });
      g.appendChild(glow);
      const body = svgEl('rect', {
        x: -7, y: -7,
        width: 14, height: 14,
        rx: 2,
        fill: 'rgba(0,0,0,0.6)',
        stroke: color,
        'stroke-width': '1.5',
      });
      g.appendChild(body);
      // Speaker cone
      const cone = svgEl('circle', {
        cx: 0, cy: 0, r: 3.5,
        fill: 'none',
        stroke: color,
        'stroke-width': '1',
        opacity: '0.6',
      });
      g.appendChild(cone);
      const dot = svgEl('circle', {
        cx: 0, cy: 0, r: 1.5,
        fill: color,
        opacity: '0.8',
      });
      g.appendChild(dot);
      break;
    }

    case 'rover': {
      // Arrow/chevron shape pointing in theta direction
      const glow = svgEl('circle', {
        cx: 0, cy: 0, r: 10,
        fill: color,
        opacity: '0.08',
        filter: `url(#glow-${deviceId})`,
      });
      g.appendChild(glow);
      // Body (rounded rect)
      const body = svgEl('rect', {
        x: -8, y: -5,
        width: 16, height: 10,
        rx: 3,
        fill: 'rgba(0,0,0,0.6)',
        stroke: color,
        'stroke-width': '1.5',
      });
      g.appendChild(body);
      // Direction arrow
      const arrow = svgEl('polygon', {
        points: '6,-3 10,0 6,3',
        fill: color,
        opacity: '0.8',
      });
      g.appendChild(arrow);
      // Wheels
      const w1 = svgEl('rect', { x: -7, y: -7, width: 3, height: 2, rx: 0.5, fill: color, opacity: '0.4' });
      const w2 = svgEl('rect', { x: -7, y: 5, width: 3, height: 2, rx: 0.5, fill: color, opacity: '0.4' });
      const w3 = svgEl('rect', { x: 4, y: -7, width: 3, height: 2, rx: 0.5, fill: color, opacity: '0.4' });
      const w4 = svgEl('rect', { x: 4, y: 5, width: 3, height: 2, rx: 0.5, fill: color, opacity: '0.4' });
      g.appendChild(w1);
      g.appendChild(w2);
      g.appendChild(w3);
      g.appendChild(w4);
      break;
    }

    default: {
      const circ = svgEl('circle', {
        cx: 0, cy: 0, r: 6,
        fill: 'rgba(0,0,0,0.6)',
        stroke: color,
        'stroke-width': '1.5',
      });
      g.appendChild(circ);
    }
  }

  return g;
}

// ── Update from polling data ────────────────────────────────────────

export function updateMap(stateData, devicesData, recentDispatches) {
  if (!_initialized) return;

  _lastDevicesData = devicesData;

  // Extract spatial data
  const spatial = (stateData && stateData.spatial) || null;
  _lastSpatial = spatial;

  if (spatial && spatial.devices) {
    for (const [deviceId, info] of Object.entries(spatial.devices)) {
      const devEl = _getOrCreateDevice(deviceId);

      const x = info.x_cm;
      const y = info.y_cm;
      const theta = info.theta_deg || 0;
      const status = info.status || 'idle';
      const isFixed = info.fixed !== false;

      // Handle rover animation
      if (deviceId === 'rover' && info.motion) {
        const motion = info.motion;
        const targetX = motion.target_x_cm;
        const targetY = motion.target_y_cm;

        if (!_roverAnimating || _roverAnimTo.x !== targetX || _roverAnimTo.y !== targetY) {
          // Start new animation
          _roverAnimFrom = { x: _roverCurrentPos.x || x, y: _roverCurrentPos.y || y };
          _roverAnimTo = { x: targetX, y: targetY };
          _roverAnimStart = performance.now();
          _roverAnimDuration = motion.duration_ms || 3000;
          _roverAnimating = true;

          // Draw path line and ghost
          _drawRoverPath(_roverAnimFrom, _roverAnimTo);
          _drawGhostRover(targetX, targetY, theta);
        }
      } else if (deviceId === 'rover') {
        if (_roverAnimating && status === 'idle') {
          _roverAnimating = false;
          _clearRoverPath();
        }
        if (!_roverAnimating) {
          _roverCurrentPos = { x, y };
          // Add to trail
          _addTrailPoint(x, y);
        }
      }

      // Position the device (non-rover, or rover when not animating)
      if (deviceId !== 'rover' || !_roverAnimating) {
        devEl.group.setAttribute('transform', `translate(${x}, ${y}) rotate(${theta})`);
      }

      // Update status visuals
      _updateDeviceStatus(deviceId, status, devEl);

      // Update cursor for draggable devices
      if (!isFixed) {
        devEl.group.style.cursor = 'grab';
      }
    }
  }

  // Update user position
  if (spatial && spatial.user) {
    _drawUser(spatial.user.x_cm, spatial.user.y_cm);
  } else if (_roomConfig && _roomConfig.user_default_position) {
    _drawUser(_roomConfig.user_default_position.x_cm, _roomConfig.user_default_position.y_cm);
  }

  // Handle command pulses
  if (Array.isArray(recentDispatches)) {
    _processDispatches(recentDispatches);
  }
}

// ── Device status updates ───────────────────────────────────────────

function _updateDeviceStatus(deviceId, status, devEl) {
  const { statusRing, speakIndicator, icon, color } = devEl;

  // Reset
  statusRing.setAttribute('opacity', '0');
  statusRing.classList.remove('ring-executing', 'ring-offline');
  speakIndicator.setAttribute('opacity', '0');
  icon.setAttribute('opacity', '1');

  switch (status) {
    case 'executing':
      statusRing.setAttribute('opacity', '1');
      statusRing.classList.add('ring-executing');
      break;
    case 'speaking':
      speakIndicator.setAttribute('opacity', '1');
      break;
    case 'offline':
      icon.setAttribute('opacity', '0.3');
      statusRing.setAttribute('stroke', 'var(--color-error)');
      statusRing.setAttribute('opacity', '0.6');
      statusRing.classList.add('ring-offline');
      break;
    case 'idle':
    default:
      // Normal appearance
      statusRing.setAttribute('stroke', color);
      break;
  }
}

// ── User position ───────────────────────────────────────────────────

let _userGroup = null;

function _drawUser(x, y) {
  if (!_userGroup) {
    _userGroup = svgEl('g', { class: 'user-marker' });

    // Person silhouette (head + body)
    const head = svgEl('circle', {
      cx: 0, cy: -5, r: 3,
      fill: 'none',
      stroke: 'rgba(226,232,240,0.35)',
      'stroke-width': '1',
    });
    _userGroup.appendChild(head);

    const body = svgEl('path', {
      d: 'M -4,0 Q 0,-2 4,0 L 3,7 Q 0,8 -3,7 Z',
      fill: 'none',
      stroke: 'rgba(226,232,240,0.25)',
      'stroke-width': '0.8',
    });
    _userGroup.appendChild(body);

    // Label
    const label = svgEl('text', {
      x: 0, y: 16,
      fill: 'rgba(226,232,240,0.3)',
      'font-size': '6',
      'font-family': "'JetBrains Mono', monospace",
      'text-anchor': 'middle',
    });
    label.textContent = 'USER';
    _userGroup.appendChild(label);

    _gUser.appendChild(_userGroup);
  }

  _userGroup.setAttribute('transform', `translate(${x}, ${y})`);
}

// ── Rover trail ─────────────────────────────────────────────────────

function _addTrailPoint(x, y) {
  // Only add if moved significantly
  const last = _roverTrail[_roverTrail.length - 1];
  if (last && Math.abs(last.x - x) < 2 && Math.abs(last.y - y) < 2) return;

  _roverTrail.push({ x, y, time: performance.now() });
  if (_roverTrail.length > MAX_TRAIL) _roverTrail.shift();

  _renderTrail();
}

function _renderTrail() {
  // Clear existing trail dots
  while (_gTrail.firstChild) _gTrail.removeChild(_gTrail.firstChild);

  const now = performance.now();
  const fadeTime = 10000; // 10s fade

  for (let i = 0; i < _roverTrail.length; i++) {
    const pt = _roverTrail[i];
    const age = now - pt.time;
    const opacity = Math.max(0, 0.4 * (1 - age / fadeTime));
    if (opacity <= 0) continue;

    const dot = svgEl('circle', {
      cx: pt.x, cy: pt.y, r: 2,
      fill: DEVICE_COLORS.rover,
      opacity: String(opacity),
    });
    _gTrail.appendChild(dot);
  }
}

// ── Rover path and ghost ────────────────────────────────────────────

let _pathLine = null;
let _ghostGroup = null;

function _drawRoverPath(from, to) {
  _clearRoverPath();

  // Dashed line from current to target
  _pathLine = svgEl('line', {
    x1: from.x, y1: from.y,
    x2: to.x, y2: to.y,
    stroke: DEVICE_COLORS.rover,
    'stroke-width': '1',
    'stroke-dasharray': '4,4',
    opacity: '0.4',
    class: 'rover-path-line',
  });
  _gPaths.appendChild(_pathLine);
}

function _drawGhostRover(x, y, theta) {
  if (_ghostGroup) {
    _ghostGroup.remove();
  }

  _ghostGroup = svgEl('g', {
    class: 'ghost-rover',
    transform: `translate(${x}, ${y}) rotate(${theta})`,
  });

  // Ghost body (translucent)
  const body = svgEl('rect', {
    x: -8, y: -5,
    width: 16, height: 10,
    rx: 3,
    fill: 'none',
    stroke: DEVICE_COLORS.rover,
    'stroke-width': '1',
    'stroke-dasharray': '2,2',
    opacity: '0.3',
  });
  _ghostGroup.appendChild(body);

  // Ghost target marker
  const target = svgEl('circle', {
    cx: 0, cy: 0, r: 3,
    fill: DEVICE_COLORS.rover,
    opacity: '0.15',
  });
  _ghostGroup.appendChild(target);

  // Label
  const label = svgEl('text', {
    x: 0, y: 18,
    fill: DEVICE_COLORS.rover,
    'font-size': '5',
    'font-family': "'JetBrains Mono', monospace",
    'text-anchor': 'middle',
    opacity: '0.4',
  });
  label.textContent = 'TARGET';
  _ghostGroup.appendChild(label);

  _gPaths.appendChild(_ghostGroup);
}

function _clearRoverPath() {
  if (_pathLine) { _pathLine.remove(); _pathLine = null; }
  if (_ghostGroup) { _ghostGroup.remove(); _ghostGroup = null; }
}

// ── Command pulse effects ───────────────────────────────────────────

function _processDispatches(dispatches) {
  const now = Date.now();
  const centerX = _roomWidth / 2;
  const centerY = _roomHeight / 2;

  for (const dispatch of dispatches) {
    const pulseId = `${dispatch.device}-${dispatch.timestamp}-${dispatch.instruction}`;
    if (_pulsedIds.has(pulseId)) continue;

    const dispatchTime = typeof dispatch.timestamp === 'number'
      ? dispatch.timestamp
      : new Date(dispatch.timestamp).getTime();

    if (now - dispatchTime > 3000) continue;

    // Find target device position
    const devEl = _deviceGroups[dispatch.device];
    if (!devEl) continue;

    const transform = devEl.group.getAttribute('transform') || '';
    const match = transform.match(/translate\(([\d.]+),\s*([\d.]+)\)/);
    if (!match) continue;

    const targetX = parseFloat(match[1]);
    const targetY = parseFloat(match[2]);
    const color = DEVICE_COLORS[dispatch.device] || '#e2e8f0';

    _pulsedIds.add(pulseId);

    // Create pulse dot
    const pulse = svgEl('circle', {
      cx: centerX, cy: centerY, r: 3,
      fill: color,
      opacity: '0.9',
      filter: 'url(#glow-cmd)',
    });
    _gPulses.appendChild(pulse);

    // Expanding ring at center
    const ring = svgEl('circle', {
      cx: centerX, cy: centerY, r: 5,
      fill: 'none',
      stroke: color,
      'stroke-width': '1',
      opacity: '0.6',
    });
    _gPulses.appendChild(ring);

    _activePulses.push({
      dot: pulse,
      ring,
      startX: centerX, startY: centerY,
      targetX, targetY,
      startTime: performance.now(),
      duration: 800,
      color,
    });
  }

  // Prevent unbounded growth
  if (_pulsedIds.size > 500) _pulsedIds.clear();
}

// ── Tooltip ─────────────────────────────────────────────────────────

let _tooltipGroup = null;

function _showTooltip(deviceId, event) {
  _hideTooltip();

  const devEl = _deviceGroups[deviceId];
  if (!devEl) return;

  const transform = devEl.group.getAttribute('transform') || '';
  const match = transform.match(/translate\(([\d.]+),\s*([\d.]+)\)/);
  if (!match) return;

  const x = parseFloat(match[1]);
  const y = parseFloat(match[2]);

  // Build tooltip info
  let statusText = 'unknown';
  let lastAction = '';
  if (_lastSpatial && _lastSpatial.devices && _lastSpatial.devices[deviceId]) {
    statusText = _lastSpatial.devices[deviceId].status || 'idle';
  }
  if (_lastDevicesData && Array.isArray(_lastDevicesData)) {
    const dev = _lastDevicesData.find(d => d.device_id === deviceId);
    if (dev) {
      statusText = dev.status || statusText;
    }
  }

  _tooltipGroup = svgEl('g', { class: 'tooltip-group' });

  // Position tooltip above the device
  const tooltipX = x;
  const tooltipY = y - 30;

  // Background
  const bg = svgEl('rect', {
    x: tooltipX - 40,
    y: tooltipY - 14,
    width: 80,
    height: 20,
    rx: 3,
    fill: 'rgba(10,10,20,0.9)',
    stroke: devEl.color,
    'stroke-width': '0.5',
  });
  _tooltipGroup.appendChild(bg);

  // Text
  const text = svgEl('text', {
    x: tooltipX,
    y: tooltipY,
    fill: '#e2e8f0',
    'font-size': '6',
    'font-family': "'JetBrains Mono', monospace",
    'text-anchor': 'middle',
  });
  text.textContent = `${deviceId} / ${statusText}`;
  _tooltipGroup.appendChild(text);

  _gTooltip.appendChild(_tooltipGroup);
}

function _hideTooltip() {
  if (_tooltipGroup) {
    _tooltipGroup.remove();
    _tooltipGroup = null;
  }
}

// ── Drag-to-calibrate ───────────────────────────────────────────────

function _svgPoint(clientX, clientY) {
  const pt = _svg.createSVGPoint();
  pt.x = clientX;
  pt.y = clientY;
  const svgPt = pt.matrixTransform(_svg.getScreenCTM().inverse());
  return { x: svgPt.x, y: svgPt.y };
}

function _onDragStart(e) {
  const target = e.target.closest('[data-device-id]');
  if (!target) return;

  const deviceId = target.getAttribute('data-device-id');
  // Only allow dragging rover (non-fixed devices)
  if (_lastSpatial && _lastSpatial.devices && _lastSpatial.devices[deviceId]) {
    if (_lastSpatial.devices[deviceId].fixed) return;
  } else if (deviceId !== 'rover') {
    return;
  }

  e.preventDefault();
  _dragging = true;
  _dragDeviceId = deviceId;
  target.style.cursor = 'grabbing';

  const svgPt = _svgPoint(e.clientX, e.clientY);
  const transform = target.getAttribute('transform') || '';
  const match = transform.match(/translate\(([\d.]+),\s*([\d.]+)\)/);
  if (match) {
    _dragOffset.x = parseFloat(match[1]) - svgPt.x;
    _dragOffset.y = parseFloat(match[2]) - svgPt.y;
  }
}

function _onDragMove(e) {
  if (!_dragging || !_dragDeviceId) return;
  e.preventDefault();

  const svgPt = _svgPoint(e.clientX, e.clientY);
  let newX = Math.max(0, Math.min(_roomWidth, svgPt.x + _dragOffset.x));
  let newY = Math.max(0, Math.min(_roomHeight, svgPt.y + _dragOffset.y));

  const devEl = _deviceGroups[_dragDeviceId];
  if (devEl) {
    devEl.group.setAttribute('transform', `translate(${newX}, ${newY})`);
  }
}

function _onDragEnd(e) {
  if (!_dragging || !_dragDeviceId) return;

  const devEl = _deviceGroups[_dragDeviceId];
  if (devEl) {
    devEl.group.style.cursor = 'grab';

    // Extract final position
    const transform = devEl.group.getAttribute('transform') || '';
    const match = transform.match(/translate\(([\d.]+),\s*([\d.]+)\)/);
    if (match) {
      const x = Math.round(parseFloat(match[1]));
      const y = Math.round(parseFloat(match[2]));

      // POST calibration
      fetch('/spatial/calibrate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          device_id: _dragDeviceId,
          x_cm: x,
          y_cm: y,
        }),
      }).catch(err => console.warn('Calibrate POST failed:', err));

      _roverCurrentPos = { x, y };
      _addTrailPoint(x, y);
    }
  }

  _dragging = false;
  _dragDeviceId = null;
}

// Touch handlers
function _onTouchStart(e) {
  if (e.touches.length !== 1) return;
  const touch = e.touches[0];
  const fakeEvent = {
    clientX: touch.clientX,
    clientY: touch.clientY,
    target: document.elementFromPoint(touch.clientX, touch.clientY),
    preventDefault: () => e.preventDefault(),
  };
  fakeEvent.target = fakeEvent.target || e.target;
  // Need closest to work
  if (fakeEvent.target.closest) {
    _onDragStart(fakeEvent);
  }
}

function _onTouchMove(e) {
  if (!_dragging) return;
  if (e.touches.length !== 1) return;
  const touch = e.touches[0];
  _onDragMove({
    clientX: touch.clientX,
    clientY: touch.clientY,
    preventDefault: () => e.preventDefault(),
  });
}

// ── Animation loop ──────────────────────────────────────────────────

function _animationLoop(timestamp) {
  _animFrameId = requestAnimationFrame(_animationLoop);

  // Animate rover movement
  if (_roverAnimating && _roverAnimStart !== null) {
    const elapsed = timestamp - _roverAnimStart;
    const t = Math.min(elapsed / _roverAnimDuration, 1.0);

    // Ease-in-out
    const eased = t < 0.5
      ? 2 * t * t
      : 1 - Math.pow(-2 * t + 2, 2) / 2;

    const cx = _roverAnimFrom.x + (_roverAnimTo.x - _roverAnimFrom.x) * eased;
    const cy = _roverAnimFrom.y + (_roverAnimTo.y - _roverAnimFrom.y) * eased;
    _roverCurrentPos = { x: cx, y: cy };

    // Calculate angle toward target
    const dx = _roverAnimTo.x - _roverAnimFrom.x;
    const dy = _roverAnimTo.y - _roverAnimFrom.y;
    const angle = Math.atan2(dy, dx) * 180 / Math.PI;

    const devEl = _deviceGroups['rover'];
    if (devEl) {
      devEl.group.setAttribute('transform', `translate(${cx}, ${cy}) rotate(${angle})`);
    }

    // Update path line start to current pos
    if (_pathLine) {
      _pathLine.setAttribute('x1', cx);
      _pathLine.setAttribute('y1', cy);
    }

    // Add trail points during movement
    if (t > 0 && t < 1) {
      _addTrailPoint(cx, cy);
    }

    if (t >= 1.0) {
      _roverAnimating = false;
      _roverAnimStart = null;
      _clearRoverPath();
      _addTrailPoint(_roverAnimTo.x, _roverAnimTo.y);
    }
  }

  // Animate command pulses
  _updatePulseAnimations(timestamp);

  // Periodically rebuild trail (throttled to avoid DOM thrash)
  if (timestamp - _lastTrailRender > 500) {
    _renderTrail();
    _lastTrailRender = timestamp;
  }
}

function _updatePulseAnimations(timestamp) {
  let i = _activePulses.length;
  while (i--) {
    const p = _activePulses[i];
    const elapsed = timestamp - p.startTime;
    const t = Math.min(elapsed / p.duration, 1.0);

    if (t >= 1.0) {
      // Remove pulse
      p.dot.remove();
      p.ring.remove();
      _activePulses.splice(i, 1);

      // Flash the target device
      const devId = Object.keys(DEVICE_COLORS).find(id => DEVICE_COLORS[id] === p.color);
      if (devId && _deviceGroups[devId]) {
        const sr = _deviceGroups[devId].statusRing;
        sr.setAttribute('opacity', '0.8');
        sr.setAttribute('r', '16');
        setTimeout(() => {
          sr.setAttribute('opacity', '0');
          sr.setAttribute('r', '14');
        }, 300);
      }
    } else {
      // Lerp dot position
      const x = p.startX + (p.targetX - p.startX) * t;
      const y = p.startY + (p.targetY - p.startY) * t;
      p.dot.setAttribute('cx', x);
      p.dot.setAttribute('cy', y);
      p.dot.setAttribute('opacity', String(0.9 * (1 - t * 0.5)));
      p.dot.setAttribute('r', String(3 - t * 1.5));

      // Expand ring at origin
      const ringR = 5 + t * 20;
      p.ring.setAttribute('r', ringR);
      p.ring.setAttribute('opacity', String(0.6 * (1 - t)));
    }
  }
}
