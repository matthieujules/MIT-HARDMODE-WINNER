// timeline.js — Communication timeline for ClaudeHome dashboard
// Hero module: merges events + master reasoning into a unified, sorted,
// incrementally-rendered card stream.

/* ------------------------------------------------------------------ */
/*  Module state                                                       */
/* ------------------------------------------------------------------ */

let _container = null;
let _renderedIds = new Set();

const MAX_CARDS = 200;

const TOOL_ICONS = {
  update_user_state: '\u{1F9E0}',
  send_to_lamp:      '\u{1F4A1}',
  send_to_mirror:    '\u{1FA9E}',
  send_to_radio:     '\u{1F4FB}',
  send_to_rover:     '\u{1F697}',
  no_op:             '\u23F8\uFE0F',
};
const DEFAULT_TOOL_ICON = '\u{1F527}';

/* ------------------------------------------------------------------ */
/*  Public API                                                         */
/* ------------------------------------------------------------------ */

/**
 * Bind the timeline to a container element and reset tracking state.
 * @param {HTMLElement} containerElement
 */
export function initTimeline(containerElement) {
  _container = containerElement;
  _renderedIds = new Set();
}

/**
 * Build a unified timeline from two data sources, render only new entries.
 * @param {Array} events           — from GET /events
 * @param {Array} masterLog        — from GET /master-log
 * @param {Array} pendingDeterministic — deterministic matches [{text, device, action}]
 */
export function updateTimeline(events, masterLog, pendingDeterministic) {
  if (!_container) return;

  const unified = [];

  // --- A) Process raw events ---
  if (Array.isArray(events)) {
    for (const evt of events) {
      const entry = _processEvent(evt, pendingDeterministic);
      if (entry) unified.push(entry);
    }
  }

  // --- B) Process master-log entries ---
  if (Array.isArray(masterLog)) {
    for (const ml of masterLog) {
      const entries = _processMasterLog(ml);
      for (const e of entries) unified.push(e);
    }
  }

  // Sort newest-first
  unified.sort((a, b) => {
    const ta = new Date(a.timestamp).getTime() || 0;
    const tb = new Date(b.timestamp).getTime() || 0;
    return tb - ta;
  });

  // Collect only unseen entries (keep insertion order = newest first)
  const fresh = [];
  for (const entry of unified) {
    if (!_renderedIds.has(entry.id)) {
      _renderedIds.add(entry.id);
      fresh.push(entry);
    }
  }

  // Render new cards — prepend each so newest ends up on top
  // fresh is already newest-first; we reverse so prepend order is correct
  for (let i = fresh.length - 1; i >= 0; i--) {
    const card = _renderCard(fresh[i]);
    if (card) {
      _container.prepend(card);
    }
  }

  // Cap rendered cards
  while (_container.children.length > MAX_CARDS) {
    _container.removeChild(_container.lastChild);
  }
}

/* ------------------------------------------------------------------ */
/*  Event processing                                                   */
/* ------------------------------------------------------------------ */

function _processEvent(event, pendingDeterministic) {
  const id = `evt_${event.timestamp}_${event.device_id}_${event.kind}`;
  const kind = event.kind || event.event_kind;

  const entry = {
    id,
    type: 'in',
    timestamp: event.timestamp,
    device_id: event.device_id,
    kind,
    payload: event.payload,
  };

  // Transcript → check for deterministic match
  if (kind === 'transcript' && Array.isArray(pendingDeterministic)) {
    const text = (event.payload && event.payload.text) || '';
    const match = pendingDeterministic.find((d) => d.text === text);
    if (match) {
      entry.type = 'deterministic';
      entry.deterministicDevice = match.device;
      entry.deterministicAction = match.action;
    }
  }

  return entry;
}

/* ------------------------------------------------------------------ */
/*  Master-log processing                                              */
/* ------------------------------------------------------------------ */

function _processMasterLog(ml) {
  const entries = [];

  // 1. Reasoning card
  const toolCalls = ml.tool_calls || [];
  const outcome = ml.outcome || 'unknown';
  const summary =
    outcome === 'no_op'
      ? `no_op: ${ml.no_op_reason || '—'}`
      : `${toolCalls.length} tool calls \u2192 ${outcome}`;

  entries.push({
    id: `master_${ml.timestamp}`,
    type: 'reasoning',
    timestamp: ml.timestamp,
    model: ml.model || 'unknown',
    latency: ml.latency_ms,
    inputTokens: ml.input_tokens,
    outputTokens: ml.output_tokens,
    toolCalls,
    stateBefore: ml.state_before,
    stateAfter: ml.state_after,
    outcome,
    noOpReason: ml.no_op_reason,
    summary,
  });

  // 2. One OUT card per dispatch
  const dispatches = ml.dispatches || [];
  for (let i = 0; i < dispatches.length; i++) {
    const d = dispatches[i];
    entries.push({
      id: `out_${ml.timestamp}_${d.device}_${i}`,
      type: 'out',
      timestamp: ml.timestamp,
      device: d.device,
      instruction: d.instruction,
      result: d.result,
    });
  }

  return entries;
}

/* ------------------------------------------------------------------ */
/*  Card renderers                                                     */
/* ------------------------------------------------------------------ */

function _renderCard(entry) {
  switch (entry.type) {
    case 'in':            return _renderInCard(entry);
    case 'reasoning':     return _renderReasoningCard(entry);
    case 'out':           return _renderOutCard(entry);
    case 'deterministic': return _renderDeterministicCard(entry);
    default:              return null;
  }
}

// --- IN ---
function _renderInCard(entry) {
  const time = _formatTime(entry.timestamp);
  const summary = _payloadSummary(entry.kind, entry.payload);

  const card = document.createElement('div');
  card.className = 'timeline-card timeline-card--in';
  card.innerHTML =
    `<div class="timeline-card__header">` +
      `<span class="indicator indicator--in">\u25BC IN</span>` +
      `<span>${_esc(entry.device_id)} \u00B7 ${_esc(entry.kind)}</span>` +
      `<span style="margin-left:auto; font-size:11px; color:#64748b">${time}</span>` +
    `</div>` +
    `<div class="timeline-card__summary">${_esc(summary)}</div>`;
  return card;
}

// --- REASONING ---
function _renderReasoningCard(entry) {
  const time = _formatTime(entry.timestamp);
  const totalTok = (entry.inputTokens || 0) + (entry.outputTokens || 0);

  // Tool calls HTML
  let toolCallsHTML = '';
  for (const tc of entry.toolCalls) {
    const name = tc.name || tc.function || 'unknown';
    const icon = TOOL_ICONS[name] || DEFAULT_TOOL_ICON;
    const inputStr = tc.input
      ? JSON.stringify(tc.input)
      : (tc.arguments ? JSON.stringify(tc.arguments) : '{}');
    const compact = inputStr.length > 200 ? inputStr.slice(0, 200) + '\u2026' : inputStr;
    toolCallsHTML +=
      `<div class="tool-call">` +
        `<span class="tool-call__icon">${icon}</span>` +
        `<span>${_esc(name)}</span>` +
        `<span style="color:#64748b; margin-left:4px">${_esc(compact)}</span>` +
      `</div>`;
  }

  // State diff HTML
  const diffHTML = _buildStateDiff(entry.stateBefore, entry.stateAfter);

  const card = document.createElement('div');
  card.className = 'timeline-card timeline-card--reasoning';
  card.innerHTML =
    `<div class="timeline-card__header">` +
      `<span class="indicator indicator--reasoning">\u25C9 REASONING</span>` +
      `<span>${_esc(entry.model)} \u00B7 ${entry.latency || '?'}ms \u00B7 ${totalTok} tok</span>` +
      `<span class="chevron" style="margin-left:auto">\u25B8</span>` +
      `<span style="font-size:11px; color:#64748b">${time}</span>` +
    `</div>` +
    `<div class="timeline-card__summary">${_esc(entry.summary)}</div>` +
    `<div class="timeline-card__expandable">` +
      `<div class="timeline-card__detail">` +
        (toolCallsHTML || '<div style="color:#64748b">No tool calls</div>') +
        (diffHTML ? `<div class="state-diff">${diffHTML}</div>` : '') +
      `</div>` +
    `</div>`;

  // Wire up expand/collapse
  const header = card.querySelector('.timeline-card__header');
  const expandable = card.querySelector('.timeline-card__expandable');
  const chevron = card.querySelector('.chevron');

  header.addEventListener('click', () => {
    const isExpanded = expandable.classList.toggle('expanded');
    chevron.textContent = isExpanded ? '\u25BE' : '\u25B8';
  });

  return card;
}

// --- OUT ---
function _renderOutCard(entry) {
  const time = _formatTime(entry.timestamp);
  const summary = _truncate(entry.instruction || '', 120);

  const card = document.createElement('div');
  card.className = 'timeline-card timeline-card--out';
  card.innerHTML =
    `<div class="timeline-card__header">` +
      `<span class="indicator indicator--out">\u25B2 OUT</span>` +
      `<span>master \u2192 ${_esc(entry.device)}</span>` +
      `<span style="margin-left:auto; font-size:11px; color:#64748b">${time}</span>` +
    `</div>` +
    `<div class="timeline-card__summary">${_esc(summary)}</div>`;
  return card;
}

// --- DETERMINISTIC ---
function _renderDeterministicCard(entry) {
  const time = _formatTime(entry.timestamp);
  const text = (entry.payload && entry.payload.text) || '';
  const summary = `${entry.deterministicAction || '?'} \u00B7 ${text}`;

  const card = document.createElement('div');
  card.className = 'timeline-card timeline-card--deterministic';
  card.innerHTML =
    `<div class="timeline-card__header">` +
      `<span class="indicator indicator--deterministic">\u26A1 DET</span>` +
      `<span>router \u2192 ${_esc(entry.deterministicDevice || '?')}</span>` +
      `<span style="margin-left:auto; font-size:11px; color:#64748b">${time}</span>` +
    `</div>` +
    `<div class="timeline-card__summary">${_esc(summary)}</div>`;
  return card;
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

/**
 * Format ISO timestamp to HH:MM:SS.
 */
function _formatTime(isoString) {
  if (!isoString) return '--:--:--';
  try {
    const d = new Date(isoString);
    return [
      String(d.getHours()).padStart(2, '0'),
      String(d.getMinutes()).padStart(2, '0'),
      String(d.getSeconds()).padStart(2, '0'),
    ].join(':');
  } catch {
    return '--:--:--';
  }
}

/**
 * Build a human-readable payload summary depending on event kind.
 */
function _payloadSummary(kind, payload) {
  if (!payload) return '';
  if (kind === 'transcript') {
    return payload.text || '';
  }
  if (kind === 'action_result') {
    return `${payload.status || '?'} \u2014 ${payload.detail || ''}`;
  }
  if (kind === 'heartbeat') {
    return 'heartbeat';
  }
  if (kind === 'tick') {
    return 'system tick';
  }
  // Fallback: compact JSON
  try {
    const str = JSON.stringify(payload);
    return str.length > 120 ? str.slice(0, 120) + '\u2026' : str;
  } catch {
    return String(payload);
  }
}

/**
 * Compare two state objects and return an HTML string of changed keys.
 */
function _buildStateDiff(before, after) {
  if (!before || !after) return '';
  const allKeys = new Set([...Object.keys(before), ...Object.keys(after)]);
  const diffs = [];
  for (const key of allKeys) {
    const bv = JSON.stringify(before[key]);
    const av = JSON.stringify(after[key]);
    if (bv !== av) {
      diffs.push(`${_esc(key)}: ${_esc(String(before[key]))} \u2192 ${_esc(String(after[key]))}`);
    }
  }
  return diffs.length > 0
    ? diffs.map((d) => `<div>${d}</div>`).join('')
    : '';
}

/**
 * Truncate a string to maxLen characters with ellipsis.
 */
function _truncate(str, maxLen) {
  if (str.length <= maxLen) return str;
  return str.slice(0, maxLen) + '\u2026';
}

/**
 * Minimal HTML escaping to prevent XSS when interpolating user data.
 */
function _esc(str) {
  if (typeof str !== 'string') return '';
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
