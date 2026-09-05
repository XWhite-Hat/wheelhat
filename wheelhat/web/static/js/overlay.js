/**
 * Browser-source overlay.
 *
 * Listens on a per-wheel WebSocket and animates whatever the server tells it to.
 * Nothing here decides a winner; that keeps every open source in agreement.
 */

import {
  RESULT_BAND,
  shadowFilter,
  STAGE_GAP,
  TITLE_BAND,
  WheelRenderer,
} from './wheel-canvas.js';

const wheelId = decodeURIComponent(location.pathname.split('/').filter(Boolean).pop() || '');
const params = new URLSearchParams(location.search);

const fitEl = document.getElementById('fit');
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

let lastSize = '';
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


/** True when the winner banner takes its own space below the wheel. */
function resultIsUnder() {
  return (
    appearance.show_result !== false &&
    (params.get('result') || appearance.result_position || 'under') !== 'over'
  );
}

function layout() {
  const configured = Number(params.get('size') || appearance.size || 0);
  const root = document.documentElement;
  const viewWidth = root.clientWidth || window.innerWidth;
  const viewHeight = root.clientHeight || window.innerHeight;
  if (viewWidth <= 0 || viewHeight <= 0) {
    // The source has no size yet. Timers still fire when frame callbacks and
    // resize observers do not, so poll briefly rather than waiting forever.
    scheduleLayoutRetry();
    return;
  }

  // Compose at the size the wheel was designed for, then scale the whole thing
  // to fit whatever the browser source turned out to be.
  //
  // Laying out to the window instead meant the picture depended on the window:
  // a background is fitted to the box it is given, so the same wheel in a
  // 1005x904 source and in a maximised tab got two different crops - the wide
  // one zoomed nearly twice as hard. The editor preview is the configured
  // source's shape, so it agreed with one and not the other. Composed at a
  // fixed size and scaled, every source shows the same view, and resizing one
  // scales the picture instead of recropping it.
  const sourceWidth = Math.max(0, Number(appearance.source_width) || 0);
  const sourceHeight = Math.max(0, Number(appearance.source_height) || 0);
  //: No configured size is not a reason to letterbox: fill the source instead,
  //: which is what the overlay has always done.
  const composed = sourceWidth > 0 && sourceHeight > 0;
  const width = composed ? sourceWidth : viewWidth;
  const height = composed ? sourceHeight : viewHeight;
  // Free to go above 1. A larger browser source should show a larger wheel,
  // the way it always has; what must not change is the framing. The canvas's
  // backing store is measured after this is applied, so scaling up costs
  // resolution rather than sharpness.
  const fit = composed ? Math.min(viewWidth / sourceWidth, viewHeight / sourceHeight) : 1;

  // Reserve the title and banner from the wheel's *settings*, never from what
  // happens to be on screen. Measuring visibility meant the wheel shrank the
  // moment a winner appeared and grew back when it faded - the wheel visibly
  // resizing on every spin. Reserved up front, the wheel is one fixed size.
  const showTitle = appearance.show_title !== false && params.get('title') !== '0';
  const under = resultIsUnder();
  // The stage puts STAGE_GAP between its children, so the bands alone do not
  // account for the height. Without this the canvas is given more room than
  // exists and the column reflows.
  const gaps = STAGE_GAP * ((showTitle ? 1 : 0) + (under ? 1 : 0));
  const chrome = (showTitle ? TITLE_BAND : 0) + (under ? RESULT_BAND : 0) + gaps;

  // Only the height is spent on chrome, so a tall narrow source still gets a
  // wheel as wide as it will go.
  const fits = Math.min(width, height - chrome);
  const size = Math.max(120, configured > 0 ? Math.min(configured, fits) : fits);

  // The wheel's own square, and nothing more. This used to be the whole area
  // left after the title and banner, so that a background could cover the
  // source - but the wheel is drawn at the smaller side of it, which made the
  // wheel grow with the source. `size` was effectively ignored, and enlarging
  // the source to fit artwork scaled the wheel up by the same amount, so the
  // artwork never got any bigger beside it. The canvas is the whole source now,
  // so this only has to reserve the wheel.
  const boxWidth = size;
  const boxHeight = size;
  const signature = `${size}:${boxWidth}:${boxHeight}:${fit}`;
  if (signature === lastSize) return;
  layoutAttempts = 0;
  lastSize = signature;
  // The stage is the source, and the transform fits it to the window. Set as a
  // variable rather than as `transform` so hiding between spins, which scales
  // the stage down, can multiply the two instead of replacing this one.
  stage.style.width = `${width}px`;
  stage.style.height = `${height}px`;
  fitEl.style.setProperty('--wh-fit', String(fit));
  wrap.style.width = `${boxWidth}px`;
  wrap.style.height = `${boxHeight}px`;
  // The canvas is the whole source, not just the band the wheel sits in, so a
  // background or a frame reaches every edge of the browser source. It used to
  // be the band, which meant the background was fitted to a box shorter than
  // the source by the height of the title and the winner banner - a different
  // crop from the one the editor preview showed, and shifted down besides.
  //
  // Sized in pixels rather than left to `height: 100%` in the stylesheet: a
  // canvas has intrinsic dimensions - its drawing buffer - so a percentage
  // against a content-sized box is cyclic, and resize() wrote the measurement
  // straight back into the buffer, growing it on every pass.
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  // Sized here too: the shadow scales with the wheel, so it has to be
  // recomputed whenever the source is resized.
  canvas.style.filter = shadowFilter(appearance, size);
  renderer.resize();

  // Where the wheel goes on that canvas. Measured rather than worked out from
  // the bands: the column is centred, so the offset depends on the title's
  // real height. wheelWrap holds no canvas of its own, but it still takes up
  // the wheel's room in the column, and its room is reserved from the wheel's
  // settings rather than from what happens to be on screen - so this is stable
  // across a spin instead of moving when a winner appears.
  const stageRect = stage.getBoundingClientRect();
  const wrapRect = wrap.getBoundingClientRect();
  if (stageRect.width > 0 && stageRect.height > 0) {
    renderer.setWheelBox({
      x: (wrapRect.left - stageRect.left) / stageRect.width,
      y: (wrapRect.top - stageRect.top) / stageRect.height,
      width: wrapRect.width / stageRect.width,
      height: wrapRect.height / stageRect.height,
    });
  }
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
  // Drives whether the banner sits in the column or floats over the wheel.
  const under = resultIsUnder();
  stage.dataset.result = under ? 'under' : 'over';

  // Floating over the wheel means over the *wheel*, which is not the middle of
  // the browser source: the title and banner bands push the wheel off centre,
  // and a background can move where the middle appears to be. The wheel canvas
  // fills wheelWrap and is drawn at its centre, so parenting the banner there
  // puts it on the wheel's axis whatever else is on screen.
  const parent = under ? stage : wrap;
  if (resultEl.parentElement !== parent) {
    if (under) stage.insertBefore(resultEl, messageEl);
    else wrap.appendChild(resultEl);
  }
  titleEl.textContent = state.name || '';

  if (appearance.background && appearance.background !== 'transparent') {
    document.body.style.background = appearance.background;
  } else {
    document.body.style.background = 'transparent';
  }

  layout();
  // layout() returns early when the size has not changed, so the filter is
  // applied here as well - editing the shadow does not resize anything.
  canvas.style.filter = shadowFilter(appearance, Number(String(lastSize).split(':')[0]) || 0);
  scheduleIdleHide();
}

function scheduleIdleHide() {
  clearTimeout(idleTimer);
  if (!appearance.hide_when_idle) {
    stage.dataset.hidden = 'false';
    return;
  }
  // Its own setting. It used to reuse the winner banner's duration, so a short
  // banner also meant a wheel that disappeared before anyone had looked at it.
  const seconds = Number(appearance.hide_after_seconds);
  const wait = Number.isFinite(seconds) && seconds >= 0 ? seconds * 1000 : 5000;
  idleTimer = setTimeout(() => {
    stage.dataset.hidden = 'true';
  }, Math.max(500, wait));
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

/**
 * Catch up with a spin that is already running.
 *
 * A source can connect part-way through a spin - OBS reconnects, or the scene
 * is switched back mid-spin. Cutting straight to the winner gives the result
 * away early and leaves the wheel disagreeing with the banner, so animate
 * whatever is left instead. The spin id is passed through, so this lands on the
 * same off-centre spot as the sources that watched the whole thing.
 */
async function resumeSpin(payload) {
  const remaining = Number(payload.stops_in_ms || 0);
  if (remaining < 120) {
    // Practically over already: place the wheel rather than animate a blur.
    renderer.settleOn(payload.winner_id);
    stage.dataset.state = 'result';
    showResult(payload.winner);
    return;
  }

  wake();
  hideResult();
  stage.dataset.state = 'spinning';
  await renderer.spin({
    targetIndex: payload.winner_id,
    durationMs: remaining,
    // The tail of a spin, not a fresh one - keep the turns proportionate.
    turns: Math.max(1, Math.round(remaining / 1200)),
    easing: 'easeOutQuint',
    spinId: payload.spin_id,
  });
  stage.dataset.state = 'result';
  showResult(payload.winner);
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
        resumeSpin(payload);
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
