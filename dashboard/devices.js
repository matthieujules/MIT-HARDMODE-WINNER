// devices.js — Device status grid for ClaudeHome dashboard

const DEVICE_ICONS = {
  lamp: '\u{1F4A1}',
  companion: '\u{1FA9E}',
  speaker: '\u{1F4FB}',
  mobile_coaster: '\u{1F697}',
};

let _container = null;

function _iconFor(device) {
  if (DEVICE_ICONS[device.device_type]) {
    return DEVICE_ICONS[device.device_type];
  }
  return (device.device_name || '?')[0].toUpperCase();
}

function _relativeTime(isoString) {
  if (!isoString) return 'never';
  const diff = Math.floor((Date.now() - new Date(isoString).getTime()) / 1000);
  if (diff < 0) return 'just now';
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}

function _buildCardHTML(device) {
  const icon = _iconFor(device);
  const dotClass = device.status === 'online' ? 'status-dot--online' : 'status-dot--offline';
  const ago = _relativeTime(device.last_seen);

  return `<div class="device-card__icon">${icon}</div>
<div class="device-card__info">
  <div class="device-card__name">${device.device_name}</div>
  <div class="device-card__meta">
    <span class="status-dot ${dotClass}"></span>
    ${ago}
  </div>
</div>`;
}

export function initDevices(containerElement) {
  _container = containerElement;
}

export function updateDevices(devicesArray) {
  if (!_container) return;

  for (const device of devicesArray) {
    let card = _container.querySelector(`[data-device-id="${device.device_id}"]`);

    if (!card) {
      card = document.createElement('div');
      card.className = 'device-card';
      card.setAttribute('data-device-id', device.device_id);
      _container.appendChild(card);
    }

    card.innerHTML = _buildCardHTML(device);
  }
}
