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
let activeAudio = null;
let currentDialAngle = 0;
let lastPlaybackQueue = [];
let lastPlaybackType = null;
let lastPlan = {};
let lastSpotifyTrackUri = null;

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
  if (url.startsWith('http://') || url.startsWith('https://')) {
    return url;
  }
  return `${window.location.origin}${url}`;
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

function derivePlaybackQueue(data) {
  const playbackQueue = data?.execution?.playback?.audio_queue || [];
  const playbackItemsQueue = (data?.execution?.playback?.audio_items || [])
    .map((item) => item?.audio_url)
    .filter(Boolean);
  const generatedQueue = (data?.execution?.clips_generated || [])
    .map((clip) => clip?.audio_url || clip?.file)
    .filter(Boolean);
  const chosen = playbackQueue.length ? playbackQueue : (playbackItemsQueue.length ? playbackItemsQueue : generatedQueue);
  return chosen.map(normalizeAudioUrl).filter(Boolean);
}

function renderDownloadLinks(data) {
  const items = data?.execution?.playback?.audio_items || [];
  if (!items.length) {
    downloadLinksEl.innerHTML = '';
    return;
  }

  const links = items
    .map((item) => {
      const index = item?.index ?? '?';
      const url = normalizeAudioUrl(item?.audio_url);
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

async function runDecision() {
  const command = commandEl.value.trim();
  if (!command) {
    statusEl.textContent = 'Enter a command first.';
    return;
  }

  statusEl.textContent = 'Running LLM + function route...';
  runBtn.disabled = true;

  try {
    const res = await fetch('/api/radio/command', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command })
    });

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
    resultEl.textContent = JSON.stringify(data, null, 2);
    await runPlayback(data);
  } catch (err) {
    statusEl.textContent = `Error: ${err.message}`;
  } finally {
    runBtn.disabled = false;
  }
}

function stopPlayback() {
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

async function playQueue(urls, onBeforeEach, onAfterEach) {
  for (let index = 0; index < urls.length; index += 1) {
    const url = urls[index];

    if (typeof onBeforeEach === 'function') {
      await onBeforeEach(index, urls.length);
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

    if (typeof onAfterEach === 'function') {
      await onAfterEach(index, urls.length);
    }
  }
}

async function runPlayback(data) {
  const playback = data?.execution?.playback || {};
  const plan = data?.plan || {};
  const dialEvents = playback.dial_events || [];
  const trackUri = data?.execution?.track?.uri || null;
  audioPlayer.volume = 1.0;
  audioPlayer.muted = false;
  lastPlan = plan;
  lastPlaybackType = playback.type || null;
  lastSpotifyTrackUri = playback.type === 'music' ? trackUri : null;
  renderDownloadLinks(data);
  renderSpotifyTrackLink(data);

  if (playback.type === 'music' && playback.audio_url) {
    lastPlaybackQueue = [normalizeAudioUrl(playback.audio_url)].filter(Boolean);
  } else if (playback.type === 'podcast') {
    lastPlaybackQueue = derivePlaybackQueue(data);
  } else {
    lastPlaybackQueue = [];
  }

  replayBtn.disabled = lastPlaybackQueue.length === 0;
  if (!replayBtn.disabled) {
    audioPlayer.src = lastPlaybackQueue[0];
    audioPlayer.load();
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
    const queue = derivePlaybackQueue(data);
    if (queue.length > 0) {
      statusEl.textContent = 'Playing podcast clips in browser.';
      await playQueue(
        queue,
        async (index) => {
          statusEl.textContent = 'Turning dial...';
          const event = dialEvents[index] || { degrees: 55 };
          await applyDialEvent(event, plan);
          statusEl.textContent = 'Playing podcast clips in browser.';
        },
        async () => {
          await sleep(100);
        }
      );
      statusEl.textContent = 'Finished podcast clip sequence.';
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
      await playAudio(lastPlaybackQueue[0]);
      statusEl.textContent = 'Replaying music audio.';
    } catch (_err) {
      statusEl.textContent = 'Unable to replay music audio.';
    }
    return;
  }

  statusEl.textContent = 'Replaying podcast clips...';
  await playQueue(lastPlaybackQueue, async (index) => {
    const event = { degrees: 55 };
    await applyDialEvent(event, lastPlan);
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
