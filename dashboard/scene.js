// scene.js — Isolated 3D scene module for the ClaudeHome dashboard
// Uses Three.js via import map defined in index.html

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

// ---------------------------------------------------------------------------
// Module-level state
// ---------------------------------------------------------------------------
let _container = null;
let _scene = null;
let _camera = null;
let _renderer = null;
let _controls = null;
let _brain = null;
let _brainGlow = null;
let _deviceMeshes = {};
let _activePulses = [];
let _pulsedIds = new Set();
let _initialized = false;

// Device layout definitions
const DEVICE_DEFS = {
  lamp:   { pos: new THREE.Vector3(-3, 0, 0), color: 0x00f0ff,  geo: () => new THREE.SphereGeometry(0.3, 16, 16) },
  mirror: { pos: new THREE.Vector3( 3, 0, 0), color: 0x8b5cf6,  geo: () => new THREE.OctahedronGeometry(0.35) },
  radio:  { pos: new THREE.Vector3( 0, 0,-3), color: 0xf59e0b,  geo: () => new THREE.BoxGeometry(0.4, 0.4, 0.4) },
  rover:  { pos: new THREE.Vector3( 0, 0, 3), color: 0x22c55e,  geo: () => new THREE.BoxGeometry(0.5, 0.2, 0.3) },
};

// ---------------------------------------------------------------------------
// initScene
// ---------------------------------------------------------------------------
export function initScene(containerElement) {
  try {
    if (_initialized) return;
    _container = containerElement;

    // --- Renderer ---
    _renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    _renderer.setClearColor(0x06060a);
    _renderer.setPixelRatio(window.devicePixelRatio);
    _renderer.setSize(_container.clientWidth, _container.clientHeight);
    _container.appendChild(_renderer.domElement);

    // --- Camera ---
    const aspect = _container.clientWidth / _container.clientHeight;
    _camera = new THREE.PerspectiveCamera(50, aspect, 0.1, 100);
    _camera.position.set(0, 4, 8);
    _camera.lookAt(0, 0, 0);

    // --- Scene ---
    _scene = new THREE.Scene();

    // --- Central brain wireframe ---
    const brainGeo = new THREE.IcosahedronGeometry(1.0, 1);
    const brainMat = new THREE.MeshBasicMaterial({ color: 0x8b5cf6, wireframe: true });
    _brain = new THREE.Mesh(brainGeo, brainMat);
    _scene.add(_brain);

    // --- Inner glow ---
    const glowGeo = new THREE.IcosahedronGeometry(0.7, 1);
    const glowMat = new THREE.MeshBasicMaterial({ color: 0x8b5cf6, transparent: true, opacity: 0.15 });
    _brainGlow = new THREE.Mesh(glowGeo, glowMat);
    _scene.add(_brainGlow);

    // --- Device nodes + dashed lines ---
    const origin = new THREE.Vector3(0, 0, 0);

    for (const [id, def] of Object.entries(DEVICE_DEFS)) {
      // Device mesh
      const mesh = new THREE.Mesh(def.geo(), new THREE.MeshBasicMaterial({ color: def.color }));
      mesh.position.copy(def.pos);
      _scene.add(mesh);
      _deviceMeshes[id] = mesh;

      // Dashed line from brain to device
      const lineGeo = new THREE.BufferGeometry().setFromPoints([origin, def.pos]);
      const lineMat = new THREE.LineDashedMaterial({
        color: 0xffffff,
        transparent: true,
        opacity: 0.15,
        dashSize: 0.3,
        gapSize: 0.2,
      });
      const line = new THREE.Line(lineGeo, lineMat);
      line.computeLineDistances();
      _scene.add(line);
    }

    // --- Grid floor ---
    const grid = new THREE.GridHelper(10, 20, 0xffffff, 0xffffff);
    grid.position.y = -1;
    grid.material.opacity = 0.05;
    grid.material.transparent = true;
    _scene.add(grid);

    // --- Lighting ---
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.3);
    _scene.add(ambientLight);

    const pointLight = new THREE.PointLight(0x8b5cf6, 1, 20);
    pointLight.position.set(0, 5, 0);
    _scene.add(pointLight);

    // --- OrbitControls ---
    _controls = new OrbitControls(_camera, _renderer.domElement);
    _controls.autoRotate = true;
    _controls.autoRotateSpeed = 0.5;
    _controls.enableDamping = true;
    _controls.dampingFactor = 0.05;
    _controls.enablePan = false;
    _controls.minDistance = 4;
    _controls.maxDistance = 15;

    // --- ResizeObserver ---
    const resizeObserver = new ResizeObserver(() => {
      const w = _container.clientWidth;
      const h = _container.clientHeight;
      if (w === 0 || h === 0) return;
      _camera.aspect = w / h;
      _camera.updateProjectionMatrix();
      _renderer.setSize(w, h);
    });
    resizeObserver.observe(_container);

    // --- Animation loop ---
    function animate() {
      requestAnimationFrame(animate);

      // Rotate brain
      _brain.rotation.y += 0.003;
      _brainGlow.rotation.y += 0.003;

      // Update controls
      _controls.update();

      // Update pulse beams
      _updatePulses();

      // Render
      _renderer.render(_scene, _camera);
    }
    animate();

    _initialized = true;
  } catch (err) {
    console.error('Failed to initialize 3D scene:', err);
    containerElement.innerHTML = '<div class="scene-fallback">3D scene unavailable</div>';
  }
}

// ---------------------------------------------------------------------------
// updateScene
// ---------------------------------------------------------------------------
export function updateScene(devices, recentDispatches) {
  if (!_initialized) return;

  // --- Update device opacity based on online/offline status ---
  if (Array.isArray(devices)) {
    for (const device of devices) {
      const mesh = _deviceMeshes[device.device_id];
      if (!mesh) continue;

      if (device.status === 'online') {
        mesh.material.opacity = 1.0;
        mesh.material.transparent = false;
      } else {
        mesh.material.opacity = 0.2;
        mesh.material.transparent = true;
      }
    }
  }

  // --- Pulse beams for recent dispatches ---
  if (Array.isArray(recentDispatches)) {
    const now = Date.now();

    for (const dispatch of recentDispatches) {
      // Build a stable id for dedup
      const pulseId = `${dispatch.device}-${dispatch.timestamp}-${dispatch.instruction}`;

      // Skip if already pulsed or older than 3 seconds
      if (_pulsedIds.has(pulseId)) continue;

      const dispatchTime = typeof dispatch.timestamp === 'number'
        ? dispatch.timestamp
        : new Date(dispatch.timestamp).getTime();

      if (now - dispatchTime > 3000) continue;

      // Find the target device
      const def = DEVICE_DEFS[dispatch.device];
      if (!def) continue;

      _pulsedIds.add(pulseId);

      // Create pulse sphere
      const pulseGeo = new THREE.SphereGeometry(0.08, 8, 8);
      const pulseMat = new THREE.MeshBasicMaterial({ color: def.color });
      const pulseMesh = new THREE.Mesh(pulseGeo, pulseMat);
      pulseMesh.position.set(0, 0, 0);
      _scene.add(pulseMesh);

      _activePulses.push({
        mesh: pulseMesh,
        start: new THREE.Vector3(0, 0, 0),
        target: def.pos.clone(),
        startTime: now,
        duration: 800,
      });
    }
  }

  // Prevent unbounded growth of the pulsed-ids set
  if (_pulsedIds.size > 500) {
    _pulsedIds.clear();
  }
}

// ---------------------------------------------------------------------------
// Internal: update active pulse animations
// ---------------------------------------------------------------------------
function _updatePulses() {
  const now = Date.now();
  let i = _activePulses.length;

  while (i--) {
    const pulse = _activePulses[i];
    const elapsed = now - pulse.startTime;
    const t = Math.min(elapsed / pulse.duration, 1.0);

    if (t >= 1.0) {
      // Animation complete — remove
      _scene.remove(pulse.mesh);
      pulse.mesh.geometry.dispose();
      pulse.mesh.material.dispose();
      _activePulses.splice(i, 1);
    } else {
      // Lerp position
      pulse.mesh.position.lerpVectors(pulse.start, pulse.target, t);
    }
  }
}
