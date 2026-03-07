const dial = document.getElementById('dial');
const dialMeta = document.getElementById('dialMeta');
const statusEl = document.getElementById('status');
const resultEl = document.getElementById('result');
const commandEl = document.getElementById('command');
const runBtn = document.getElementById('runBtn');
const spotifyBtn = document.getElementById('spotifyBtn');
const spotifyStatus = document.getElementById('spotifyStatus');
const replayBtn = document.getElementById('replayBtn');
const spotifyTrackLink = document.getElementById('spotifyTrackLink');
const audioPlayer = document.getElementById('audioPlayer');
const downloadLinksEl = document.getElementById('downloadLinks');
const llmDecisionRawEl = document.getElementById('llmDecisionRaw');
const llmDecisionAppliedEl = document.getElementById('llmDecisionApplied');
const llmDecisionSourceEl = document.getElementById('llmDecisionSource');
let activeAudio = null;
let currentDialAngle = 0;
let lastPlaybackQueue = [];
let lastPlaybackType = null;
let lastPlan = {};
let lastSpotifyTrackUri = null;
let activeRequestId = 0;
let lastRunClickAtMs = 0;
let lastIssuedCommand = '';
let waitingGlitchRequestId = 0;
let waitingGlitchDialTimer = null;

let spotifyClientId = null;
let spotifyAccessToken = null;
let spotifyRefreshToken = null;
let spotifyExpiresAt = 0;
let spotifyPlayer = null;
let spotifyDeviceId = null;

const SPOTIFY_STORAGE = {
  verifier: 'spotify_pkce_verifier',
  state: 'spotify_auth_state',
  accessToken: 'spotify_access_token',
  refreshToken: 'spotify_refresh_token',
  expiresAt: 'spotify_expires_at'
};

function normalizeAudioUrl(url) {
  if (!url || typeof url !== 'string') {
    return null;
  }
  const trimmed = url.trim();
  if (trimmed.startsWith('http://') || trimmed.startsWith('https://')) {
    return trimmed;
  }
  const withLeadingSlash = trimmed.startsWith('/') ? trimmed : `/${trimmed}`;
  return encodeURI(`${window.location.origin}${withLeadingSlash}`);
}

function clipHasGlitch(item) {
  if (!item) {
    return false;
  }
  const token = String(item?.token || '').trim();
  if (token === '00') {
    return true;
  }
  if (item?.kind === 'glitch') {
    return true;
  }
  const fileName = String(item?.file || item?.audio_file || item?.audio_url || '');
  return /(^|\/)00(?:_|\.)/i.test(fileName);
}

function updateSpotifyStatus(text, connected = false) {
  spotifyStatus.textContent = `Spotify: ${text}`;
  spotifyStatus.classList.toggle('connected', connected);
  spotifyBtn.textContent = connected ? 'Reconnect Spotify' : 'Connect Spotify';
}

function randomString(length) {
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  let result = '';
  for (let index = 0; index < length; index += 1) {
    result += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return result;
}

async function sha256(plain) {
  const encoder = new TextEncoder();
  const data = encoder.encode(plain);
  return window.crypto.subtle.digest('SHA-256', data);
}

function base64UrlEncode(arrayBuffer) {
  const bytes = new Uint8Array(arrayBuffer);
  let binary = '';
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte);
  });
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

async function fetchWebConfig() {
  try {
    const response = await fetch('/api/web-config');
    const data = await response.json();
    spotifyClientId = data?.spotify_web_client_id || null;
    if (!spotifyClientId) {
      updateSpotifyStatus('client ID missing');
    }
  } catch (_err) {
    updateSpotifyStatus('config error');
  }
}

function loadSpotifyTokenFromStorage() {
  spotifyAccessToken = localStorage.getItem(SPOTIFY_STORAGE.accessToken);
  spotifyRefreshToken = localStorage.getItem(SPOTIFY_STORAGE.refreshToken);
  spotifyExpiresAt = Number(localStorage.getItem(SPOTIFY_STORAGE.expiresAt) || 0);
}

function saveSpotifyToken({ accessToken, refreshToken, expiresIn }) {
  spotifyAccessToken = accessToken;
  if (refreshToken) {
    spotifyRefreshToken = refreshToken;
  }
  spotifyExpiresAt = Date.now() + (Number(expiresIn || 0) * 1000);

  localStorage.setItem(SPOTIFY_STORAGE.accessToken, spotifyAccessToken || '');
  localStorage.setItem(SPOTIFY_STORAGE.refreshToken, spotifyRefreshToken || '');
  localStorage.setItem(SPOTIFY_STORAGE.expiresAt, String(spotifyExpiresAt));
}

function spotifyTokenValid() {
  return Boolean(spotifyAccessToken) && Date.now() < (spotifyExpiresAt - 60_000);
}

async function refreshSpotifyTokenIfNeeded() {
  if (spotifyTokenValid()) {
    return spotifyAccessToken;
  }

  if (!spotifyClientId || !spotifyRefreshToken) {
    return null;
  }

  const body = new URLSearchParams({
    grant_type: 'refresh_token',
    refresh_token: spotifyRefreshToken,
    client_id: spotifyClientId
  });

  const response = await fetch('https://accounts.spotify.com/api/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body
  });

  if (!response.ok) {
    return null;
  }

  const data = await response.json();
  saveSpotifyToken({
    accessToken: data.access_token,
    refreshToken: data.refresh_token,
    expiresIn: data.expires_in
  });
  return spotifyAccessToken;
}

async function handleSpotifyAuthCallback() {
  const query = new URLSearchParams(window.location.search);
  const code = query.get('code');
  const state = query.get('state');
  const storedState = localStorage.getItem(SPOTIFY_STORAGE.state);
  const verifier = localStorage.getItem(SPOTIFY_STORAGE.verifier);

  if (!code) {
    return;
  }

  if (!state || state !== storedState || !verifier || !spotifyClientId) {
    updateSpotifyStatus('auth validation failed');
    return;
  }

  const body = new URLSearchParams({
    client_id: spotifyClientId,
    grant_type: 'authorization_code',
    code,
    redirect_uri: `${window.location.origin}/`,
    code_verifier: verifier
  });

  const response = await fetch('https://accounts.spotify.com/api/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body
  });

  if (!response.ok) {
    updateSpotifyStatus('auth exchange failed');
    return;
  }

  const data = await response.json();
  saveSpotifyToken({
    accessToken: data.access_token,
    refreshToken: data.refresh_token,
    expiresIn: data.expires_in
  });

  localStorage.removeItem(SPOTIFY_STORAGE.state);
  localStorage.removeItem(SPOTIFY_STORAGE.verifier);
  window.history.replaceState({}, document.title, `${window.location.origin}/`);
}

async function startSpotifyLogin() {
  if (!spotifyClientId) {
    updateSpotifyStatus('client ID missing');
    return;
  }

  const verifier = randomString(64);
  const challenge = base64UrlEncode(await sha256(verifier));
  const state = randomString(16);

  localStorage.setItem(SPOTIFY_STORAGE.verifier, verifier);
  localStorage.setItem(SPOTIFY_STORAGE.state, state);

  const params = new URLSearchParams({
    response_type: 'code',
    client_id: spotifyClientId,
    scope: 'streaming user-read-email user-read-private user-modify-playback-state user-read-playback-state',
    redirect_uri: `${window.location.origin}/`,
    state,
    code_challenge_method: 'S256',
    code_challenge: challenge
  });

  window.location.href = `https://accounts.spotify.com/authorize?${params.toString()}`;
}

async function ensureSpotifyPlayerConnected() {
  const token = await refreshSpotifyTokenIfNeeded();
  if (!token) {
    updateSpotifyStatus('not authenticated');
    return false;
  }

  if (!window.Spotify) {
    updateSpotifyStatus('SDK not loaded');
    return false;
  }

  if (!spotifyPlayer) {
    spotifyPlayer = new window.Spotify.Player({
      name: 'ClaudeHome Radio Web Player',
      getOAuthToken: (callback) => callback(spotifyAccessToken || ''),
      volume: 1.0
    });

    spotifyPlayer.addListener('ready', ({ device_id }) => {
      spotifyDeviceId = device_id;
      updateSpotifyStatus('connected', true);
    });

    spotifyPlayer.addListener('not_ready', () => {
      updateSpotifyStatus('device offline');
    });

    spotifyPlayer.addListener('authentication_error', ({ message }) => {
      updateSpotifyStatus(`auth error (${message})`);
    });

    spotifyPlayer.addListener('account_error', ({ message }) => {
      updateSpotifyStatus(`account error (${message})`);
    });

    spotifyPlayer.addListener('initialization_error', ({ message }) => {
      updateSpotifyStatus(`init error (${message})`);
    });

    const connected = await spotifyPlayer.connect();
    if (!connected) {
      updateSpotifyStatus('connect failed');
      return false;
    }
  }

  const waitStart = Date.now();
  while (!spotifyDeviceId && Date.now() - waitStart < 5000) {
    await sleep(100);
  }

  return Boolean(spotifyDeviceId);
}

async function playSpotifyTrackUri(trackUri, plan) {
  const connected = await ensureSpotifyPlayerConnected();
  if (!connected || !spotifyDeviceId) {
    return false;
  }

  const token = await refreshSpotifyTokenIfNeeded();
  if (!token) {
    return false;
  }

  await fetch('https://api.spotify.com/v1/me/player', {
    method: 'PUT',
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      device_ids: [spotifyDeviceId],
      play: false
    })
  });

  const playResponse = await fetch(`https://api.spotify.com/v1/me/player/play?device_id=${encodeURIComponent(spotifyDeviceId)}`, {
    method: 'PUT',
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      uris: [trackUri]
    })
  });

  if (!playResponse.ok) {
    return false;
  }

  if (spotifyPlayer) {
    await spotifyPlayer.setVolume(1.0);
  }

  return true;
}

async function interruptCurrentPlayback() {
  stopWaitingGlitch();
  stopPlayback();
  if (spotifyPlayer) {
    try {
      await spotifyPlayer.pause();
    } catch (_err) {
    }
  }
}

function waitingGlitchUrl() {
  return normalizeAudioUrl('/Sounds/00_Glitch.mp3');
}

function startWaitingGlitch(requestId) {
  const glitchUrl = waitingGlitchUrl();
  if (!glitchUrl) {
    return;
  }

  stopWaitingGlitch();
  waitingGlitchRequestId = requestId;
  audioPlayer.loop = true;
  audioPlayer.src = glitchUrl;
  audioPlayer.load();
  activeAudio = audioPlayer;

  audioPlayer.play().catch(() => {
  });

  waitingGlitchDialTimer = setInterval(() => {
    if (waitingGlitchRequestId !== requestId || requestId !== activeRequestId) {
      return;
    }
    rotateDialBy(true, 18);
  }, 230);
}

function stopWaitingGlitch(requestId = null) {
  if (requestId !== null && waitingGlitchRequestId !== requestId) {
    return;
  }

  if (waitingGlitchDialTimer) {
    clearInterval(waitingGlitchDialTimer);
    waitingGlitchDialTimer = null;
  }

  if (waitingGlitchRequestId !== 0) {
    audioPlayer.loop = false;
    const currentSrc = String(audioPlayer.src || '');
    if (currentSrc.includes('/Sounds/00_') || currentSrc.includes('/Sounds/00.')) {
      audioPlayer.pause();
      audioPlayer.currentTime = 0;
    }
    waitingGlitchRequestId = 0;
  }
}

function derivePlaybackItems(data) {
  const playbackItems = data?.execution?.playback?.audio_items || [];
  const generatedItems = data?.execution?.clips_generated || [];
  const queueUrls = data?.execution?.playback?.audio_queue || [];
  const sourceItems = playbackItems.length ? playbackItems : generatedItems;

  if (sourceItems.length) {
    return sourceItems
      .map((item) => {
        const raw = item?.audio_url || item?.file || item?.audio_file;
        const audio_url = normalizeAudioUrl(raw);
        if (!audio_url) {
          return null;
        }
        return {
          index: item?.index,
          token: item?.token,
          kind: item?.kind,
          label: item?.label || item?.text || '',
          file: item?.file || item?.audio_file || null,
          audio_url
        };
      })
      .filter(Boolean);
  }

  return queueUrls
    .map((url, index) => {
      const audio_url = normalizeAudioUrl(url);
      if (!audio_url) {
        return null;
      }
      return { index: index + 1, token: null, kind: null, label: '', file: null, audio_url };
    })
    .filter(Boolean);
}

function renderDownloadLinks(data) {
  const items = derivePlaybackItems(data);
  if (!items.length) {
    downloadLinksEl.innerHTML = '';
    return;
  }

  const links = items
    .map((item) => {
      const index = item?.index ?? '?';
      const url = normalizeAudioUrl(item?.audio_url || item?.file || item?.audio_file);
      if (!url) {
        return '';
      }
      return `<a href="${url}" download="clip_${index}.mp3" target="_blank" rel="noopener noreferrer">Download Clip ${index}</a>`;
    })
    .filter(Boolean)
    .join(' ');

  downloadLinksEl.innerHTML = links;
}

function renderSpotifyTrackLink(data) {
  const spotifyUrl = data?.execution?.track?.external_urls?.spotify || null;
  if (!spotifyUrl) {
    spotifyTrackLink.style.display = 'none';
    spotifyTrackLink.href = '#';
    return;
  }

  spotifyTrackLink.href = spotifyUrl;
  spotifyTrackLink.style.display = 'inline-block';
}

function updateDial(turnSignal) {
  currentDialAngle = 0;
  dial.style.transform = 'rotate(0deg)';
  dialMeta.textContent = `Turn signal: ${turnSignal ? 'ON' : 'OFF'}`;
}

function rotateDialBy(turnSignal, degrees) {
  const delta = Number(degrees) || 0;
  currentDialAngle += delta;
  dial.style.transform = `rotate(${currentDialAngle}deg)`;
  dialMeta.textContent = `Turn signal: ${turnSignal ? 'ON' : 'OFF'}`;
}

function renderLlmDecision(data) {
  if (!llmDecisionRawEl || !llmDecisionAppliedEl || !llmDecisionSourceEl) {
    return;
  }

  const raw = data?.execution?.llm_decision;
  const llmToken = data?.execution?.llm_token;
  const applied = String(data?.execution?.final_selection || data?.selection || data?.plan?.selection || '').trim();
  const source = String(data?.execution?.selection_source || 'unknown');

  llmDecisionRawEl.textContent = raw ? String(raw) : 'No output returned';
  llmDecisionAppliedEl.textContent = applied || 'No decision yet.';
  llmDecisionSourceEl.textContent = llmToken ? `${source} (parsed token: ${llmToken})` : source;
}

async function runDecision() {
  const command = commandEl.value.trim();
  if (!command) {
    statusEl.textContent = 'Enter a command first.';
    return;
  }

  const now = Date.now();
  if (now - lastRunClickAtMs < 350) {
    return;
  }
  lastRunClickAtMs = now;

  const normalizedCommand = command.toLowerCase();
  const isStopCommand = normalizedCommand.includes('stop');
  const isHtmlAudioPlaying = !audioPlayer.paused && !audioPlayer.ended;
  if (!isStopCommand && isHtmlAudioPlaying && normalizedCommand === lastIssuedCommand) {
    statusEl.textContent = 'Already playing this request. Use stop or change the command.';
    return;
  }

  lastIssuedCommand = normalizedCommand;
  const requestId = ++activeRequestId;

  await interruptCurrentPlayback();
  startWaitingGlitch(requestId);
  statusEl.textContent = 'Running LLM + function route...';

  try {
    const res = await fetch('/api/radio/command', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command })
    });

    if (requestId !== activeRequestId) {
      stopWaitingGlitch(requestId);
      return;
    }

    const raw = await res.text();
    let data = null;
    try {
      data = raw ? JSON.parse(raw) : {};
    } catch (_err) {
      data = { detail: raw || 'Unknown server error' };
    }

    if (!res.ok) {
      throw new Error(data.detail || 'Request failed');
    }

    const plan = data.plan || {};
    updateDial(plan.turn_radio);
    renderLlmDecision(data);
    resultEl.textContent = JSON.stringify(data, null, 2);
    stopWaitingGlitch(requestId);
    await runPlayback(data, requestId);
  } catch (err) {
    stopWaitingGlitch(requestId);
    if (requestId === activeRequestId) {
      statusEl.textContent = `Error: ${err.message}`;
    }
  }
}

function stopPlayback() {
  audioPlayer.loop = false;
  audioPlayer.pause();
  audioPlayer.currentTime = 0;
  activeAudio = null;
}

async function playAudio(url) {
  stopPlayback();
  audioPlayer.src = normalizeAudioUrl(url);
  audioPlayer.load();
  activeAudio = audioPlayer;
  await audioPlayer.play();
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function applyDialEvent(event, plan) {
  rotateDialBy(plan?.turn_radio ?? true, event?.degrees ?? 55);
  await sleep(180);
}

async function playQueue(items, onBeforeEach, onAfterEach, isActive = () => true) {
  for (let index = 0; index < items.length; index += 1) {
    if (!isActive()) {
      return;
    }

    const item = items[index] || {};
    const url = normalizeAudioUrl(item?.audio_url || item?.file || item?.audio_file);
    if (!url) {
      continue;
    }

    if (typeof onBeforeEach === 'function') {
      await onBeforeEach(item, index, items.length);
      if (!isActive()) {
        return;
      }
    }

    await new Promise(async (resolve) => {
      try {
        stopPlayback();
        audioPlayer.src = normalizeAudioUrl(url);
        audioPlayer.load();
        activeAudio = audioPlayer;
        audioPlayer.addEventListener('ended', resolve, { once: true });
        audioPlayer.addEventListener('error', resolve, { once: true });
        await audioPlayer.play();
      } catch (_err) {
        resolve();
      }
    });

    if (!isActive()) {
      return;
    }

    if (typeof onAfterEach === 'function') {
      await onAfterEach(item, index, items.length);
    }
  }
}

async function runPlayback(data, requestId) {
  const isActive = () => requestId === activeRequestId;
  if (!isActive()) {
    return;
  }

  const playback = data?.execution?.playback || {};
  const plan = data?.plan || {};
  const dialEvents = playback.dial_events || [];
  const trackUri = data?.execution?.track?.uri || null;
  const queueItems = derivePlaybackItems(data);
  audioPlayer.volume = 1.0;
  audioPlayer.muted = false;
  lastPlan = plan;
  lastPlaybackType = playback.type || null;
  lastSpotifyTrackUri = playback.type === 'music' ? trackUri : null;
  renderDownloadLinks(data);
  renderSpotifyTrackLink(data);

  lastPlaybackQueue = queueItems;

  replayBtn.disabled = lastPlaybackQueue.length === 0;
  if (!replayBtn.disabled) {
    audioPlayer.src = lastPlaybackQueue[0].audio_url;
    audioPlayer.load();
  }

  if (playback.type === 'stop') {
    stopPlayback();
    if (queueItems.length > 0) {
      statusEl.textContent = 'Stopping audio...';
      await playQueue(queueItems, async (item) => {
        if (clipHasGlitch(item)) {
          await applyDialEvent({ degrees: 22 }, plan);
        }
      }, undefined, isActive);
      if (isActive()) {
        statusEl.textContent = 'Audio stopped.';
      }
    } else {
      statusEl.textContent = 'Audio stopped.';
    }
    return;
  }

  if (queueItems.length > 0) {
    statusEl.textContent = 'Playing selected clips in browser.';
    await playQueue(
      queueItems,
      async (item, index) => {
        if (clipHasGlitch(item)) {
          statusEl.textContent = 'Glitch cue...';
          await applyDialEvent({ degrees: 22 }, plan);
        } else {
          const event = dialEvents[index] || { degrees: 55 };
          await applyDialEvent(event, plan);
          statusEl.textContent = 'Playing selected clips in browser.';
        }
      },
      async () => {
        await sleep(80);
      },
      isActive
    );
    if (isActive()) {
      statusEl.textContent = 'Finished clip sequence.';
    }
    return;
  }

  if (playback.type === 'music') {
    if (dialEvents.length > 0) {
      await applyDialEvent(dialEvents[0], plan);
    }

    if (trackUri) {
      const sdkPlayed = await playSpotifyTrackUri(trackUri, plan);
      if (sdkPlayed) {
        statusEl.textContent = 'Playing music via Spotify Web Playback SDK.';
        return;
      }
    }

    if (playback.audio_url) {
      try {
        await playAudio(playback.audio_url);
        statusEl.textContent = 'Playing music preview in browser.';
      } catch (_err) {
        statusEl.textContent = 'Autoplay blocked. Click Replay Last Audio.';
      }
    } else {
      statusEl.textContent = 'No preview clip available. Connect Spotify to play full track.';
    }
    return;
  }

  if (playback.type === 'podcast') {
    const queue = derivePlaybackItems(data);
    if (queue.length > 0) {
      statusEl.textContent = 'Playing podcast clips in browser.';
      await playQueue(
        queue,
        async (_item, index) => {
          statusEl.textContent = 'Turning dial...';
          const event = dialEvents[index] || { degrees: 55 };
          await applyDialEvent(event, plan);
          statusEl.textContent = 'Playing podcast clips in browser.';
        },
        async () => {
          await sleep(100);
        },
        isActive
      );
      if (isActive()) {
        statusEl.textContent = 'Finished podcast clip sequence.';
      }
    } else {
      if (dialEvents.length > 0) {
        for (const event of dialEvents) {
          await applyDialEvent(event, plan);
        }
      }
      statusEl.textContent = playback.message || 'Playing podcast.';
    }
    return;
  }

  statusEl.textContent = 'Complete.';
}

async function replayLastAudio() {
  if (lastPlaybackType === 'music' && lastSpotifyTrackUri) {
    const sdkPlayed = await playSpotifyTrackUri(lastSpotifyTrackUri, lastPlan);
    if (sdkPlayed) {
      statusEl.textContent = 'Replaying via Spotify Web Playback SDK.';
      return;
    }
  }

  if (!lastPlaybackQueue.length) {
    statusEl.textContent = 'No audio available to replay yet.';
    return;
  }

  if (lastPlaybackType === 'music') {
    try {
      await playAudio(lastPlaybackQueue[0].audio_url);
      statusEl.textContent = 'Replaying music audio.';
    } catch (_err) {
      statusEl.textContent = 'Unable to replay music audio.';
    }
    return;
  }

  statusEl.textContent = 'Replaying podcast clips...';
  await playQueue(lastPlaybackQueue, async (item) => {
    const degrees = clipHasGlitch(item) ? 22 : 55;
    await applyDialEvent({ degrees }, lastPlan);
  });
  statusEl.textContent = 'Replay complete.';
}

audioPlayer.addEventListener('loadedmetadata', () => {
  if (audioPlayer.duration > 0 && statusEl.textContent.includes('No audio')) {
    statusEl.textContent = 'Audio ready. Click Replay Last Audio.';
  }
});

audioPlayer.addEventListener('error', () => {
  statusEl.textContent = 'Audio failed to load in player. Try Run again or Replay.';
});

runBtn.addEventListener('click', runDecision);
replayBtn.addEventListener('click', replayLastAudio);
spotifyBtn.addEventListener('click', startSpotifyLogin);
replayBtn.disabled = true;
updateDial(false);

window.onSpotifyWebPlaybackSDKReady = async () => {
  const token = await refreshSpotifyTokenIfNeeded();
  if (token) {
    await ensureSpotifyPlayerConnected();
  }
};

async function init() {
  updateSpotifyStatus('initializing');
  await fetchWebConfig();
  loadSpotifyTokenFromStorage();
  await handleSpotifyAuthCallback();
  const token = await refreshSpotifyTokenIfNeeded();
  if (token) {
    await ensureSpotifyPlayerConnected();
  } else {
    updateSpotifyStatus(spotifyClientId ? 'disconnected' : 'client ID missing');
  }
}

init();
