// timeline.js — Live activity reducer for the ClaudeHome dashboard.
// Instead of rendering a right-hand feed, this module normalizes
// events/master-log data into lightweight HUD snapshots for the full-map UI.

let _container = null;
let _seenIds = new Set();
let _feedItems = [];
let _deviceActivity = {};
let _latestReasoning = null;
let _latestBrainTriggerAt = null;

const MAX_FEED_ITEMS = 40;
const MAX_DEBUG_LINES = 12;
const QUIET_EVENT_KINDS = new Set(['heartbeat']);
const BRAIN_TRIGGER_KINDS = new Set(['transcript', 'vision_result', 'tick', 'frame']);
const BRAIN_PENDING_WINDOW_MS = 30000;

export function initTimeline(containerElement) {
  _container = containerElement || null;
  _seenIds = new Set();
  _feedItems = [];
  _deviceActivity = {};
  _latestReasoning = null;
  _latestBrainTriggerAt = null;

  if (_container) {
    _container.innerHTML = '';
  }
}

export function updateTimeline(events, masterLog, pendingDeterministic) {
  const unified = [];

  if (Array.isArray(events)) {
    for (const event of events) {
      const entry = _processEvent(event, pendingDeterministic);
      if (entry) unified.push(entry);
    }
  }

  if (Array.isArray(masterLog)) {
    for (const entry of masterLog) {
      const expanded = _processMasterLog(entry);
      for (const item of expanded) unified.push(item);
    }
  }

  unified.sort((a, b) => _toMs(a.timestamp) - _toMs(b.timestamp));

  let changed = false;

  for (const entry of unified) {
    if (_seenIds.has(entry.id)) continue;

    _seenIds.add(entry.id);
    changed = true;

    const feedItem = _toFeedItem(entry);
    if (feedItem) {
      _feedItems.push(feedItem);
      while (_feedItems.length > MAX_FEED_ITEMS) {
        _feedItems.shift();
      }
    }

    _applyDeviceActivity(entry);

    if (_isBrainTrigger(entry)) {
      _latestBrainTriggerAt = entry.timestamp;
    }

    if (entry.type === 'reasoning') {
      _latestReasoning = {
        timestamp: entry.timestamp,
        model: entry.model || 'master',
        summary: entry.summary,
        latency: entry.latency,
        totalTokens: (entry.inputTokens || 0) + (entry.outputTokens || 0),
        outcome: entry.outcome,
      };
    }
  }

  if (changed && _container) {
    _renderDebugTimeline();
  }

  const tickerItems = _feedItems.slice(-MAX_FEED_ITEMS);
  return {
    feedItems: tickerItems,
    tickerText: tickerItems.map((item) => item.text).join('   ✦   '),
    brain: _latestReasoning ? { ..._latestReasoning } : null,
    brainPending: _isBrainPending(),
    brainPendingAt: _latestBrainTriggerAt,
    deviceActivity: _cloneDeviceActivity(),
    deviceLogs: _buildDeviceLogs(),
  };
}

function _processEvent(event, pendingDeterministic) {
  if (!event) return null;

  const kind = event.kind || event.event_kind || 'unknown';
  const timestamp = event.timestamp || event.ts || event.created_at || null;
  const base = {
    id: `evt_${timestamp}_${event.device_id}_${kind}_${_stableSuffix(event.payload)}`,
    timestamp,
    device: event.device_id,
    kind,
    payload: event.payload || {},
  };

  if (kind === 'transcript' && Array.isArray(pendingDeterministic)) {
    const text = (event.payload && event.payload.text) || '';
    const matchIndex = pendingDeterministic.findIndex((candidate) => candidate.text === text);
    if (matchIndex >= 0) {
      const match = pendingDeterministic.splice(matchIndex, 1)[0];
      return {
        ...base,
        type: 'deterministic',
        deterministicDevice: match.device,
        deterministicAction: match.action,
      };
    }
  }

  if (kind === 'action_result') {
    return {
      ...base,
      type: 'result',
      status: event.payload && event.payload.status,
      output: event.payload && (event.payload.detail || event.payload.message || event.payload.status),
    };
  }

  if (kind === 'tick') {
    return { ...base, type: 'system' };
  }

  return {
    ...base,
    type: 'in',
  };
}

function _processMasterLog(entry) {
  if (!entry) return [];
  if (entry.outcome === 'error') return [];

  const items = [];
  const toolCalls = entry.tool_calls || [];
  const outcome = entry.outcome || 'unknown';
  const summary =
    outcome === 'no_op'
      ? `no_op: ${entry.no_op_reason || '—'}`
      : `${toolCalls.length} tool calls → ${outcome}`;

  items.push({
    id: `master_${entry.timestamp}`,
    type: 'reasoning',
    timestamp: entry.timestamp,
    model: entry.model || 'master',
    latency: entry.latency_ms,
    inputTokens: entry.input_tokens,
    outputTokens: entry.output_tokens,
    toolCalls,
    outcome,
    summary,
  });

  const dispatches = entry.dispatches || [];
  for (let index = 0; index < dispatches.length; index += 1) {
    const dispatch = dispatches[index];
    items.push({
      id: `dispatch_${entry.timestamp}_${dispatch.device}_${index}`,
      type: 'out',
      timestamp: entry.timestamp,
      device: dispatch.device,
      instruction: dispatch.instruction,
      result: dispatch.result,
    });
  }

  return items;
}

function _toFeedItem(entry) {
  if (!entry) return null;
  if (QUIET_EVENT_KINDS.has(entry.kind)) return null;

  const time = _formatTime(entry.timestamp);

  switch (entry.type) {
    case 'deterministic':
      return {
        id: entry.id,
        timestamp: entry.timestamp,
        device: entry.deterministicDevice,
        text: `[${time}] ${_jsonCompact({
          from: 'router',
          to: entry.deterministicDevice,
          action: entry.deterministicAction || 'dispatch',
          transcript: entry.payload && entry.payload.text,
        })}`,
      };

    case 'result':
      return {
        id: entry.id,
        timestamp: entry.timestamp,
        device: entry.device,
        text: `[${time}] ${_jsonCompact({
          from: entry.device,
          type: 'action_result',
          status: entry.status || 'ok',
          output: entry.output || '',
        })}`,
      };

    case 'out':
      return {
        id: entry.id,
        timestamp: entry.timestamp,
        device: entry.device,
        text: `[${time}] ${_jsonCompact({
          from: 'brain',
          to: entry.device,
          tool: `send_to_${entry.device}`,
          input: { instruction: entry.instruction || 'dispatch' },
        })}`,
      };

    case 'reasoning':
      return {
        id: entry.id,
        timestamp: entry.timestamp,
        text: `[${time}] ${_jsonCompact({
          from: entry.model || 'master',
          latency_ms: entry.latency || '?',
          summary: entry.summary || 'reasoning',
        })}`,
      };

    case 'system':
      return {
        id: entry.id,
        timestamp: entry.timestamp,
        text: `[${time}] ${_jsonCompact({ from: 'system', type: 'tick' })}`,
      };

    case 'in': {
      if (entry.kind === 'transcript') {
        return {
          id: entry.id,
          timestamp: entry.timestamp,
          device: entry.device,
          text: `[${time}] ${_jsonCompact({
            from: entry.device || 'global_mic',
            type: 'transcript',
            payload: entry.payload || {},
          })}`,
        };
      }

      return {
        id: entry.id,
        timestamp: entry.timestamp,
        device: entry.device,
        text: `[${time}] ${_jsonCompact({
          from: entry.device,
          type: entry.kind,
          payload: entry.payload || {},
        })}`,
      };
    }

    default:
      return null;
  }
}

function _applyDeviceActivity(entry) {
  const targetDevice =
    entry.type === 'deterministic'
      ? entry.deterministicDevice
      : entry.device;

  if (!targetDevice) return;

  if (!_deviceActivity[targetDevice]) {
    _deviceActivity[targetDevice] = {
      instruction: '',
      instructionAt: null,
      output: '',
      outputAt: null,
      route: '',
      status: 'idle',
    };
  }

  const activity = _deviceActivity[targetDevice];

  if (entry.type === 'out') {
    activity.instruction = entry.instruction || 'dispatch';
    activity.instructionAt = entry.timestamp;
    activity.route = 'brain';

    if (entry.result && entry.result.detail) {
      activity.dispatch = entry.result.detail;
    }
    return;
  }

  if (entry.type === 'deterministic') {
    activity.instruction = entry.deterministicAction || 'deterministic dispatch';
    activity.instructionAt = entry.timestamp;
    activity.route = 'router';
    return;
  }

  if (entry.type === 'result') {
    activity.output = entry.output || entry.status || 'ok';
    activity.outputAt = entry.timestamp;
    activity.status = entry.status || activity.status || 'idle';
  }
}

function _renderDebugTimeline() {
  if (!_container) return;

  const recent = _feedItems.slice(-MAX_DEBUG_LINES).reverse();
  _container.innerHTML = recent
    .map((item) => `<div class="timeline-debug-line">${_esc(item.text)}</div>`)
    .join('');
}

function _cloneDeviceActivity() {
  const copy = {};

  for (const [deviceId, value] of Object.entries(_deviceActivity)) {
    copy[deviceId] = { ...value };
  }

  return copy;
}

function _buildDeviceLogs() {
  const grouped = {};
  const MAX_DEVICE_LOGS = 12;

  for (const item of _feedItems) {
    if (!item.device) continue;
    grouped[item.device] = grouped[item.device] || [];
    grouped[item.device].push({
      id: item.id,
      timestamp: item.timestamp,
      text: item.text,
    });
    if (grouped[item.device].length > MAX_DEVICE_LOGS) {
      grouped[item.device].shift();
    }
  }

  return grouped;
}

function _isBrainTrigger(entry) {
  if (!entry) return false;
  if (entry.type === 'system') return true;
  return entry.type === 'in' && BRAIN_TRIGGER_KINDS.has(entry.kind);
}

function _isBrainPending() {
  const triggerMs = _toMs(_latestBrainTriggerAt);
  if (!triggerMs) return false;

  const reasoningMs = _toMs(_latestReasoning && _latestReasoning.timestamp);
  if (reasoningMs && reasoningMs >= triggerMs) return false;

  return (Date.now() - triggerMs) < BRAIN_PENDING_WINDOW_MS;
}

function _payloadSummary(kind, payload) {
  if (!payload) return '';
  if (kind === 'transcript') return payload.text || '';
  if (kind === 'vision_result') return payload.summary || payload.detail || 'vision update';
  if (kind === 'action_result') return payload.detail || payload.status || '';

  try {
    return JSON.stringify(payload);
  } catch {
    return String(payload);
  }
}

function _formatTime(value) {
  if (!value) return '--:--:--';

  try {
    const date = new Date(value);
    return [date.getHours(), date.getMinutes(), date.getSeconds()]
      .map((part) => String(part).padStart(2, '0'))
      .join(':');
  } catch {
    return '--:--:--';
  }
}

function _toMs(value) {
  const ms = new Date(value).getTime();
  return Number.isFinite(ms) ? ms : 0;
}

function _stableSuffix(payload) {
  if (!payload) return 'none';

  try {
    return JSON.stringify(payload).slice(0, 48);
  } catch {
    return String(payload).slice(0, 48);
  }
}

function _truncate(value, maxLen) {
  const str = typeof value === 'string' ? value : (value == null ? '' : String(value));
  if (str.length <= maxLen) return str;
  return `${str.slice(0, maxLen)}…`;
}

function _jsonCompact(value) {
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function _upper(value) {
  return (value || 'device').toUpperCase();
}

function _esc(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
