const dial = document.getElementById('dial');
const dialMeta = document.getElementById('dialMeta');
const statusEl = document.getElementById('status');
const resultEl = document.getElementById('result');
const commandEl = document.getElementById('command');
const runBtn = document.getElementById('runBtn');
const replayBtn = document.getElementById('replayBtn');
const audioPlayer = document.getElementById('audioPlayer');
let activeAudio = null;
let currentDialAngle = 0;
let lastPlaybackQueue = [];
let lastPlaybackType = null;
let lastPlan = {};

function normalizeAudioUrl(url) {
  if (!url || typeof url !== 'string') {
    return null;
  }
  if (url.startsWith('http://') || url.startsWith('https://')) {
    return url;
  }
  return `${window.location.origin}${url}`;
}

function derivePlaybackQueue(data) {
  const playbackQueue = data?.execution?.playback?.audio_queue || [];
  const generatedQueue = (data?.execution?.clips_generated || [])
    .map((clip) => clip?.file)
    .filter(Boolean);
  const chosen = playbackQueue.length ? playbackQueue : generatedQueue;
  return chosen.map(normalizeAudioUrl).filter(Boolean);
}

function updateDial(turnSignal, volume) {
  const clamped = Math.max(0, Math.min(100, Number(volume) || 0));
  const volumeAngle = -130 + (clamped / 100) * 260;
  currentDialAngle = volumeAngle;

  dial.style.transform = `rotate(${volumeAngle}deg)`;
  dialMeta.textContent = `Turn signal: ${turnSignal ? 'ON' : 'OFF'} | Volume: ${clamped}`;
}

function rotateDialBy(turnSignal, volume, degrees) {
  const clamped = Math.max(0, Math.min(100, Number(volume) || 0));
  const delta = Number(degrees) || 0;
  currentDialAngle += delta;
  dial.style.transform = `rotate(${currentDialAngle}deg)`;
  dialMeta.textContent = `Turn signal: ${turnSignal ? 'ON' : 'OFF'} | Volume: ${clamped}`;
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
    updateDial(plan.turn_radio, plan.volume);
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
  rotateDialBy(plan?.turn_radio ?? true, plan?.volume ?? 0, event?.degrees ?? 55);
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
  const clampedVolume = Math.max(0, Math.min(100, Number(plan?.volume) || 0));
  audioPlayer.volume = clampedVolume / 100;
  audioPlayer.muted = false;
  lastPlan = plan;
  lastPlaybackType = playback.type || null;

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

    if (playback.audio_url) {
      try {
        await playAudio(playback.audio_url);
        statusEl.textContent = 'Playing music preview in browser.';
      } catch (_err) {
        statusEl.textContent = 'Autoplay blocked. Click Replay Last Audio.';
      }
    } else {
      statusEl.textContent = playback.message || 'Playing music.';
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
replayBtn.disabled = true;
updateDial(false, 0);
