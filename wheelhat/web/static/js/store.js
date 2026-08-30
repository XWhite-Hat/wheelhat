/** Shared client state, kept live by the control WebSocket. */

import { api, toast } from './core.js';

const listeners = new Set();

export const store = {
  connected: false,
  version: '',
  wheels: [],
  integrations: [],
  twitch: {},
  overlayCounts: {},
  actionLog: [],
  activity: [],
  settings: {},
  paths: {},
  server: {},
};

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function emit(reason = '') {
  for (const fn of listeners) {
    try {
      fn(store, reason);
    } catch (err) {
      console.error('[wheelhat] subscriber failed', err);
    }
  }
}

function pushActivity(entry) {
  store.activity.unshift({ at: Date.now() / 1000, ...entry });
  if (store.activity.length > 200) store.activity.length = 200;
}

/* ------------------------------------------------------------------ loading */

export async function refreshWheels() {
  const data = await api.get('/wheels');
  store.wheels = data.wheels;
  emit('wheels');
  return store.wheels;
}

export async function refreshStatus() {
  const data = await api.get('/status');
  store.version = data.version;
  store.wheels = data.wheels.map((w) => ({
    ...(store.wheels.find((existing) => existing.id === w.id) || {}),
    ...w,
  }));
  store.integrations = data.integrations;
  store.twitch = data.twitch;
  store.actionLog = data.action_log || [];
  emit('status');
}

export async function refreshSettings() {
  const data = await api.get('/settings');
  store.settings = data.settings;
  store.paths = data.paths;
  store.server = data.server;
  emit('settings');
}

/* --------------------------------------------------------------- websocket */

let socket = null;
let retry = 700;

export function connectControlSocket() {
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  socket = new WebSocket(`${scheme}://${location.host}/ws/control`);

  socket.addEventListener('open', () => {
    retry = 700;
    store.connected = true;
    emit('connection');
  });

  socket.addEventListener('close', () => {
    store.connected = false;
    emit('connection');
    setTimeout(connectControlSocket, retry);
    retry = Math.min(retry * 1.7, 8000);
  });

  socket.addEventListener('error', () => socket.close());

  socket.addEventListener('message', (event) => {
    let message;
    try {
      message = JSON.parse(event.data);
    } catch {
      return;
    }
    handle(message);
  });
}

function handle(message) {
  switch (message.type) {
    case 'hello':
      store.integrations = message.integrations || [];
      store.twitch = message.twitch || {};
      store.overlayCounts = message.overlay_counts || {};
      emit('hello');
      break;

    case 'integrations':
      store.integrations = message.integrations || [];
      emit('integrations');
      break;

    case 'twitch_status':
      store.twitch = message.twitch || {};
      emit('twitch');
      break;

    case 'overlay_count':
      store.overlayCounts[message.wheel_id] = message.count;
      {
        const wheel = store.wheels.find((w) => w.id === message.wheel_id);
        if (wheel) wheel.overlay_clients = message.count;
      }
      emit('overlays');
      break;

    case 'wheels_changed':
      refreshWheels().catch(() => {});
      break;

    case 'spin_start': {
      const wheel = store.wheels.find((w) => w.id === message.wheel_id);
      if (wheel) wheel.spinning = true;
      pushActivity({
        kind: 'spin_start',
        text: `${message.wheel_name} is spinning${message.actor ? ` for ${message.actor}` : ''}…`,
      });
      emit('spin_start', message);
      break;
    }

    case 'spin_result':
      pushActivity({
        kind: 'spin_result',
        text: `${message.winner} won on ${
          store.wheels.find((w) => w.id === message.wheel_id)?.name || 'a wheel'
        }`,
      });
      emit('spin_result', message);
      break;

    case 'spin_finished': {
      const wheel = store.wheels.find((w) => w.id === message.wheel_id);
      if (wheel) wheel.spinning = false;
      emit('spin_finished', message);
      break;
    }

    case 'action_result':
      store.actionLog.unshift({
        created_at: message.created_at,
        name: message.name,
        action_type: message.type,
        ok: message.ok ? 1 : 0,
        detail: message.detail,
        wheel_id: message.wheel_id,
      });
      if (store.actionLog.length > 200) store.actionLog.length = 200;
      pushActivity({
        kind: message.ok ? 'action' : 'action_failed',
        text: `${message.name}: ${message.detail}`,
        bad: !message.ok,
      });
      if (!message.ok) toast(`${message.name} failed: ${message.detail}`, 'bad', 7000);
      emit('action_result', message);
      break;

    case 'twitch_event':
      pushActivity({ kind: 'twitch', text: message.summary });
      emit('twitch_event', message);
      break;

    case 'trigger_skipped':
      pushActivity({
        kind: 'skipped',
        text: `${message.wheel_name}: skipped (${message.reason})`,
      });
      emit('trigger_skipped', message);
      break;

    default:
      break;
  }
}

/** Fire-and-forget spin used from list views. */
export async function spinWheel(wheelId, options = {}) {
  return api.post(`/wheels/${wheelId}/spin`, { source: 'manual', ...options });
}
