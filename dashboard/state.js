// state.js — Top-bar state pills for ClaudeHome dashboard

let _pillMode = null;
let _pillMood = null;
let _pillEnergy = null;
let _prevState = {};

/**
 * Creates 3 pill elements (mode, mood, energy) inside the given container.
 * @param {HTMLElement} containerElement
 */
export function initState(containerElement) {
  _pillMode = document.createElement('span');
  _pillMode.className = 'state-pill state-pill--mode';
  _pillMode.textContent = 'MODE: default';

  _pillMood = document.createElement('span');
  _pillMood.className = 'state-pill state-pill--mood';
  _pillMood.textContent = 'MOOD: neutral';

  _pillEnergy = document.createElement('span');
  _pillEnergy.className = 'state-pill state-pill--energy';
  _pillEnergy.textContent = 'ENERGY: 50%';

  containerElement.appendChild(_pillMode);
  containerElement.appendChild(_pillMood);
  containerElement.appendChild(_pillEnergy);

  _prevState = {};
}

/**
 * Updates pill values from /state API data. Triggers a transition animation on change.
 * @param {{ mode: string, mood: string, energy: number }} stateData
 */
export function updateState(stateData) {
  const pills = {
    mode: _pillMode,
    mood: _pillMood,
    energy: _pillEnergy,
  };

  for (const key of ['mode', 'mood', 'energy']) {
    const value = stateData[key];
    const pill = pills[key];
    if (!pill) continue;

    const display =
      key === 'energy' && typeof value === 'number'
        ? Math.round(value * 100) + '%'
        : value;

    pill.textContent = `${key.toUpperCase()}: ${display}`;

    if (_prevState[key] !== value) {
      pill.classList.add('state-pill--changed');
      setTimeout(() => pill.classList.remove('state-pill--changed'), 600);
      _prevState[key] = value;
    }
  }
}
