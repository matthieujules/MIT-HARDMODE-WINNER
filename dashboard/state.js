// state.js — Top-bar state pills for ClaudeHome dashboard

let _pillMode = null;
let _pillMood = null;
let _pillPeople = null;
let _prevState = {};

/**
 * Creates 3 pill elements (mode, mood, people) inside the given container.
 * @param {HTMLElement} containerElement
 */
export function initState(containerElement) {
  _pillMode = document.createElement('span');
  _pillMode.className = 'state-pill state-pill--mode';
  _pillMode.textContent = 'MODE: idle';

  _pillMood = document.createElement('span');
  _pillMood.className = 'state-pill state-pill--mood';
  _pillMood.textContent = 'MOOD: neutral';

  _pillPeople = document.createElement('span');
  _pillPeople.className = 'state-pill state-pill--people';
  _pillPeople.textContent = 'PEOPLE: 0';

  containerElement.appendChild(_pillMode);
  containerElement.appendChild(_pillMood);
  containerElement.appendChild(_pillPeople);

  _prevState = {};
}

/**
 * Updates pill values from /state API data. Triggers a transition animation on change.
 * @param {{ mode: string, mood: string, people_count: number }} stateData
 */
export function updateState(stateData) {
  const entries = [
    { key: 'mode', pill: _pillMode, value: stateData.mode },
    { key: 'mood', pill: _pillMood, value: stateData.mood },
    { key: 'people_count', pill: _pillPeople, value: stateData.people_count, label: 'PEOPLE' },
  ];

  for (const { key, pill, value, label } of entries) {
    if (!pill) continue;

    const displayLabel = label || key.toUpperCase();
    pill.textContent = `${displayLabel}: ${value}`;

    if (_prevState[key] !== value) {
      pill.classList.add('state-pill--changed');
      setTimeout(() => pill.classList.remove('state-pill--changed'), 600);
      _prevState[key] = value;
    }
  }
}
