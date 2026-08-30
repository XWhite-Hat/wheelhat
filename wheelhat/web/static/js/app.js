/** Router and shell for the control panel. */

import { $$, clear, h } from './core.js';
import { renderWheelEditor } from './editor.js';
import { connectControlSocket, refreshSettings, refreshStatus, store, subscribe } from './store.js';
import { renderActivity, renderConnections, renderSettings, renderTwitch, renderWheels } from './views.js';

const main = document.getElementById('main');
let disposeCurrent = null;

const ROUTES = [
  { match: /^#\/wheels?$/, name: 'wheels', render: (m) => renderWheels(m) },
  { match: /^#\/wheel\/(.+)$/, name: 'wheels', render: (m, id) => renderWheelEditor(m, id) },
  { match: /^#\/connections$/, name: 'connections', render: (m) => renderConnections(m) },
  { match: /^#\/twitch$/, name: 'twitch', render: (m) => renderTwitch(m) },
  { match: /^#\/activity$/, name: 'activity', render: (m) => renderActivity(m) },
  { match: /^#\/settings$/, name: 'settings', render: (m) => renderSettings(m) },
];

async function route() {
  const hash = location.hash || '#/wheels';
  const entry = ROUTES.find((r) => r.match.test(hash)) || ROUTES[0];
  const params = hash.match(entry.match)?.slice(1) || [];

  if (disposeCurrent) {
    try {
      disposeCurrent();
    } catch (err) {
      console.error('[wheelhat] view cleanup failed', err);
    }
    disposeCurrent = null;
  }

  for (const link of $$('#nav a')) {
    link.classList.toggle('active', link.dataset.route === entry.name);
  }

  clear(main).appendChild(h('div.page', h('div.muted', 'Loading…')));
  try {
    disposeCurrent = (await entry.render(main, ...params)) || null;
  } catch (err) {
    console.error(err);
    clear(main).appendChild(
      h('div.page', h('div.empty', h('h3', 'Something went wrong'), h('p', err.message)))
    );
  }
  main.scrollTop = 0;
}

/* ---------------------------------------------------------------- sidebar */

function drawStatus() {
  const foot = document.getElementById('statusFoot');
  clear(foot);

  const line = (label, state, title) =>
    h('div.status-line', { title: title || '' }, h('span.dot', { class: state }), h('span.label', label));

  foot.appendChild(
    line(
      store.connected ? 'WheelHat running' : 'Server offline',
      store.connected ? 'connected' : 'error'
    )
  );

  for (const integration of store.integrations) {
    if (!integration.enabled) continue;
    foot.appendChild(
      line(integration.name, integration.state, integration.last_error || integration.state)
    );
  }

  const twitch = store.twitch || {};
  foot.appendChild(
    line(
      twitch.signed_in ? `Twitch: ${twitch.display_name || twitch.login}` : 'Twitch: signed out',
      twitch.signed_in && twitch.eventsub_state === 'connected'
        ? 'connected'
        : twitch.signed_in
          ? 'connecting'
          : 'disconnected',
      twitch.eventsub_error || ''
    )
  );

  const count = document.getElementById('navWheelCount');
  if (count) count.textContent = store.wheels.length ? String(store.wheels.length) : '';

  const version = document.getElementById('version');
  if (version && store.version) version.textContent = `v${store.version}`;
}

/* -------------------------------------------------------------------- boot */

subscribe(drawStatus);
window.addEventListener('hashchange', route);

connectControlSocket();
await Promise.allSettled([refreshStatus(), refreshSettings()]);
drawStatus();
await route();
