/**
 * Browser-source overlay.
 *
 * Listens on a per-wheel WebSocket and animates whatever the server tells it to.
 * Nothing here decides a winner; that keeps every open source in agreement.
 */

import { WheelRenderer } from './wheel-canvas.js';

const wheelId = decodeURIComponent(location.pathname.split('/').filter(Boolean).pop() || '');
const params = new URLSearchParams(location.search);

const stage = document.getElementById('stage');
const titleEl = document.getElementById('title');
const wrap = document.getElementById('wheelWrap');
const canvas = document.getElementById('wheel');
const resultEl = document.getElementById('result');
const resultValue = document.getElementById('resultValue');
const messageEl = document.getElementById('message');
const connectionEl = document.getElementById('connection');

const renderer = new WheelRenderer(canvas);

let appearance = {};
let resultTimer = null;
let messageTimer = null;
let idleTimer = null;
let socket = null;
let retryDelay = 800;

/* --------------------------------------------------------------------- layout */

let lastSize = 0;
let layoutRetry = null;
let layoutAttempts = 0;

function scheduleLayoutRetry() {
  if (layoutRetry || layoutAttempts > 40) return;
  layoutAttempts += 1;
  layoutRetry = setTimeout(() => {
    layoutRetry = null;
    layout();
  }, 250);
}


function layout() {
  const configured = Number(params.get('size') || appearance.size || 0);
  const root = document.documentElement;
  const available = Math.min(
    root.clientWidth || window.innerWidth,
    root.clientHeight || window.innerHeight
  );
  if (available <= 0) {
    // The source has no size yet. Timers still fire when frame callbacks and
    // resize observers do not, so poll briefly rather than waiting forever.
    scheduleLayoutRetry();
    return;
  }

  // Leave room for the title and result pill without ever exceeding the source.
  const chrome = (titleEl.hidden ? 0 : 52) + (resultEl.hidden ? 0 : 84);
  const size = Math.max(120, Math.min(configured || available, available - chrome));
  if (size === lastSize) return;
  layoutAttempts = 0;
  lastSize = size;
  wrap.style.width = `${size}px`;
  wrap.style.height = `${size}px`;
  renderer.resize();
}

window.addEventListener('resize', layout);

// An OBS browser source can be created before its dimensions are known, and a
// hidden source reports zero until it is shown. Watching the element itself
// catches both, where a load-time measurement would leave a 120px wheel.
new ResizeObserver(() => layout()).observe(document.body);

/* ---------------------------------------------------------------------- state */

function applyState(state) {
  appearance = state.appearance || {};
  renderer.setState({ slices: state.slices || [], appearance });

  const showTitle = appearance.show_title !== false && params.get('title') !== '0';
  titleEl.hidden = !showTitle;
  titleEl.textContent = state.name || '';

  if (appearance.background && appearance.background !== 'transparent') {
    document.body.style.background = appearance.background;
  } else {
    document.body.style.background = 'transparent';
  }

  layout();
  scheduleIdleHide();
}

function scheduleIdleHide() {
  clearTimeout(idleTimer);
  if (!appearance.hide_when_idle) {
    stage.dataset.hidden = 'false';
    return;
  }
  idleTimer = setTimeout(() => {
    stage.dataset.hidden = 'true';
  }, Math.max(500, Number(appearance.result_duration_ms || 5000)));
}

function wake() {
  clearTimeout(idleTimer);
  stage.dataset.hidden = 'false';
}

/* ---------------------------------------------------------------------- spins */

async function runSpin(payload) {
  wake();
  hideResult();
  renderer.setState({ slices: payload.slices || [], appearance: payload.appearance || appearance });
  stage.dataset.state = 'spinning';

  await renderer.spin({
    targetIndex: payload.target_index,
    durationMs: payload.duration_ms,
    turns: payload.turns,
    easing: payload.easing,
    spinId: payload.spin_id,
  });

  stage.dataset.state = 'result';
  showResult(payload.winner, payload.slices?.[payload.target_index]);
}

function showResult(winner, slice) {
  if (appearance.show_result === false) {
    scheduleIdleHide();
    return;
  }
  resultValue.textContent = winner || '';
  if (slice?.color) resultEl.style.setProperty('--result-accent', slice.color);
  resultEl.hidden = false;
  layout();

  clearTimeout(resultTimer);
  const duration = Number(appearance.result_duration_ms || 5000);
  if (duration > 0) {
    resultTimer = setTimeout(() => {
      hideResult();
      renderer.clearHighlight();
      stage.dataset.state = 'idle';
      scheduleIdleHide();
    }, duration);
  }
}

function hideResult() {
  clearTimeout(resultTimer);
  resultEl.hidden = true;
  layout();
}

function showMessage(payload) {
  wake();
  messageEl.dataset.style = payload.style || 'banner';
  messageEl.textContent = payload.text || '';
  messageEl.hidden = false;
  clearTimeout(messageTimer);
  messageTimer = setTimeout(() => {
    messageEl.hidden = true;
    scheduleIdleHide();
  }, Math.max(250, Number(payload.duration || 4000)));
}

function playSound(payload) {
  const audio = new Audio(payload.url);
  audio.volume = Math.max(0, Math.min(1, Number(payload.volume ?? 0.8)));
  audio.play().catch((err) => {
    // OBS browser sources autoplay fine; a normal tab may block until interaction.
    console.warn('[wheelhat] could not play sound', err);
  });
}

/* ----------------------------------------------------------------- connection */

function connect() {
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  socket = new WebSocket(`${scheme}://${location.host}/ws/overlay/${encodeURIComponent(wheelId)}`);

  socket.addEventListener('open', () => {
    retryDelay = 800;
    connectionEl.hidden = true;
  });

  socket.addEventListener('message', (event) => {
    let payload;
    try {
      payload = JSON.parse(event.data);
    } catch {
      return;
    }

    switch (payload.type) {
      case 'wheel_state':
        applyState(payload);
        break;
      case 'spin_start':
        runSpin(payload);
        break;
      case 'spin_resync':
        // Reloaded mid-spin: skip the animation and show where it ended up.
        stage.dataset.state = 'result';
        showResult(payload.winner);
        break;
      case 'spin_cancelled':
        stage.dataset.state = 'idle';
        hideResult();
        renderer.clearHighlight();
        break;
      case 'overlay_message':
        showMessage(payload);
        break;
      case 'overlay_sound':
        playSound(payload);
        break;
      case 'error':
        connectionEl.hidden = false;
        connectionEl.textContent = payload.message || 'Overlay error';
        break;
      default:
        break;
    }
  });

  const reconnect = () => {
    connectionEl.hidden = false;
    connectionEl.textContent = 'Reconnecting to WheelHat…';
    setTimeout(connect, retryDelay);
    retryDelay = Math.min(retryDelay * 1.6, 10000);
  };

  socket.addEventListener('close', reconnect);
  socket.addEventListener('error', () => socket.close());
}

if (!wheelId) {
  connectionEl.textContent = 'No wheel id in the URL';
} else {
  layout();
  connect();
}
