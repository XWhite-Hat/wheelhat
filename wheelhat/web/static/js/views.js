/** Wheels list, connections, Twitch, activity and settings views. */

import {
  api,
  clear,
  clockTime,
  confirmDialog,
  copyText,
  guard,
  h,
  modal,
  plural,
  preserveView,
  toast,
} from './core.js';
import { invalidateOptions } from './fields.js';
import { refreshSettings, refreshStatus, refreshWheels, store, subscribe } from './store.js';
import { triggerSpec } from './trigger-schemas.js';

/* ============================================================== wheels list */

export async function renderWheels(main) {
  const grid = h('div.wheel-grid');
  const page = h(
    'div.page',
    h(
      'div.page-head',
      h('div', h('h1', 'Wheels'), h('p', 'Each wheel gets its own browser source URL and its own triggers.')),
      h('div.spacer'),
      h('button.btn', { onclick: () => importWheel() }, 'Import'),
      h('button.btn.primary', { onclick: guard(createWheel) }, '+ New wheel')
    ),
    grid
  );

  clear(main).appendChild(page);

  const draw = () => {
    clear(grid);
    if (!store.wheels.length) {
      grid.appendChild(
        h(
          'div.empty',
          { style: { gridColumn: '1 / -1' } },
          h('h3', 'No wheels yet'),
          h('p', 'Create one, add some slices, then drop its browser source into OBS.'),
          h('button.btn.primary', { onclick: guard(createWheel) }, 'Create your first wheel')
        )
      );
      return;
    }
    for (const wheel of store.wheels) grid.appendChild(wheelCard(wheel));
  };

  await refreshWheels();
  draw();
  const unsubscribe = subscribe((_, reason) => {
    // A spin starting or finishing redraws every card. With several wheels
    // that is a long list to be thrown back to the top of.
    if (['wheels', 'overlays', 'spin_start', 'spin_finished', 'status'].includes(reason)) {
      preserveView(draw);
    }
  });
  return unsubscribe;
}

function wheelCard(wheel) {
  const url = wheel.overlay_url;
  return h(
    'div.wheel-card',
    { class: wheel.spinning ? 'spinning' : '' },
    h(
      'div.row',
      h('h3.grow', wheel.name),
      wheel.enabled ? null : h('span.pill', 'triggers off')
    ),
    wheel.description ? h('div.desc', wheel.description) : null,
    h(
      'div.meta',
      h('span.pill', `${wheel.slice_count} slices`),
      h('span.pill', `${wheel.action_count} actions`),
      wheel.trigger_count ? h('span.pill.good', `${wheel.trigger_count} triggers`) : h('span.pill', 'manual only'),
      h(
        'span.pill',
        { class: wheel.overlay_clients ? 'good' : '' },
        plural(wheel.overlay_clients || 0, 'source')
      )
    ),
    h(
      'div.overlay-url',
      h('code', url),
      h('button.btn.small.icon.ghost', { title: 'Copy the browser source URL', onclick: () => copyText(url) }, '⧉')
    ),
    h(
      'div.card-actions',
      h(
        'button.btn.primary.small',
        {
          // A wheel with nothing spinnable on it cannot spin; the server says
          // so with a 409, which is a worse way to find out than a dead button.
          disabled: wheel.spinning || !wheel.spinnable_count,
          title: wheel.spinnable_count ? '' : 'Add a slice first',
          onclick: guard(async () => {
            await api.post(`/wheels/${wheel.id}/spin`, { source: 'manual', ignore_cooldown: true });
          }),
        },
        wheel.spinning ? 'Spinning…' : 'Spin'
      ),
      h('a.btn.small', { href: `#/wheel/${wheel.id}` }, 'Edit'),
      h(
        'button.btn.small.ghost',
        {
          onclick: guard(async () => {
            await api.post(`/wheels/${wheel.id}/duplicate`);
            await refreshWheels();
            toast('Wheel duplicated', 'ok');
          }),
        },
        'Duplicate'
      ),
      h(
        'button.btn.small.ghost',
        {
          onclick: guard(async () => {
            const data = await api.get(`/export/${wheel.id}`);
            downloadJson(`${slugify(wheel.name)}.wheelhat.json`, data);
          }),
        },
        'Export'
      ),
      h('span.grow'),
      h(
        'button.btn.small.danger',
        {
          onclick: async () => {
            if (!(await confirmDialog(`Delete "${wheel.name}"?`, { detail: 'This cannot be undone.' }))) return;
            await api.del(`/wheels/${wheel.id}`);
            await refreshWheels();
            toast('Wheel deleted', 'ok');
          },
        },
        'Delete'
      )
    )
  );
}

async function createWheel() {
  // Straight to a blank wheel unless there is a saved look to start from, in
  // which case ask which. Nobody with no templates should meet a dialog.
  let templates = [];
  try {
    templates = (await api.get('/templates')).templates;
  } catch {
    // Not being able to list templates is no reason to block a new wheel.
  }
  if (!templates.length) {
    const wheel = await api.post('/wheels', {});
    await refreshWheels();
    location.hash = `#/wheel/${wheel.id}`;
    return;
  }

  let chosen = '';
  const start = async () => {
    const wheel = await api.post('/wheels', chosen ? { template_id: chosen } : {});
    await refreshWheels();
    location.hash = `#/wheel/${wheel.id}`;
    return true;
  };

  const option = (id, name, detail) =>
    h(
      'button.type-option',
      {
        type: 'button',
        onclick: async () => {
          chosen = id;
          dialog.close();
          await guard(start)();
        },
      },
      h('strong', name),
      h('small', detail)
    );

  const dialog = modal({
    title: 'New wheel',
    hideConfirm: true,
    body: h(
      'div.type-options',
      option('', 'Plain wheel', 'The default look.'),
      ...templates.map((t) => option(t.id, t.name, swatchLine(t)))
    ),
  });
}

/** A short description of what a saved look contains. */
function swatchLine(template) {
  const parts = [];
  if (template.has_background_image) parts.push('background');
  if (template.has_frame_image) parts.push('overlay');
  parts.push(plural((template.palette || []).length, 'colour'));
  return parts.join(' · ');
}

function importWheel() {
  const input = h('input', { type: 'file', accept: '.json,application/json', hidden: true });
  input.addEventListener('change', async () => {
    const file = input.files?.[0];
    if (!file) return;
    try {
      const data = JSON.parse(await file.text());
      const result = await api.post('/import', { data, replace: false });
      await refreshWheels();
      toast(`Imported ${plural(result.imported, 'wheel')}`, 'ok');
    } catch (err) {
      toast(`Import failed: ${err.message}`, 'bad', 7000);
    } finally {
      input.remove();
    }
  });
  document.body.appendChild(input);
  input.click();
}

/* =============================================================== connections */

/**
 * Connections, ordered by what the user has rather than what we support.
 *
 * It used to open with seven configuration forms - one per supported app, all
 * disabled, for apps most people do not have - and put the scan, which is the
 * only thing that knows what is actually on the machine, at the bottom. So the
 * page led with a wall of forms and buried the answer.
 *
 * Now: what is running, then what you have connected, then the manual forms
 * behind a disclosure.
 */
export async function renderConnections(main) {
  const discoverBox = h('div');
  const yours = h('div.conn-grid');
  const yoursSection = h('div', { style: { marginTop: '20px' } });
  const manual = h('div.conn-grid');

  const page = h(
    'div.page',
    h(
      'div.page-head',
      h('div', h('h1', 'Connections'), h('p', 'Apps on this machine that your wheel actions can control.'))
    ),
    h(
      'div.card',
      h(
        'div.row',
        h('div.grow', h('h2', 'Apps on this machine')),
        h('button.btn', { id: 'scanBtn', onclick: guard(runScan) }, 'Scan again')
      ),
      h('div', { style: { marginTop: '14px' } }, discoverBox)
    ),
    yoursSection,
    h(
      'details.card',
      { style: { marginTop: '20px' } },
      h('summary', 'Set one up by hand'),
      h(
        'p.card-hint',
        'For an app on another machine, or one the scan cannot see because its '
          + 'server is switched off.'
      ),
      manual
    )
  );

  clear(main).appendChild(page);

  const draw = () => {
    // "Yours" is what has been turned on. Everything else is a blank form, and
    // blank forms belong behind the disclosure.
    const connected = store.integrations.filter((integration) => integration.enabled);
    const rest = store.integrations.filter((integration) => !integration.enabled);

    clear(yours);
    for (const integration of connected) yours.appendChild(connectionCard(integration));

    clear(yoursSection);
    if (connected.length) {
      yoursSection.append(h('h2', { style: { margin: '0 0 12px' } }, 'Your connections'), yours);
    }

    clear(manual);
    for (const integration of rest) manual.appendChild(connectionCard(integration));
  };

  async function runScan() {
    const button = page.querySelector('#scanBtn');
    button.disabled = true;
    button.textContent = 'Scanning…';
    clear(discoverBox).appendChild(h('div.muted', 'Probing local ports…'));
    try {
      const data = await api.get('/discovery');
      const found = data.results.filter((r) => r.status !== 'not_found');
      const missing = data.results.filter((r) => r.status === 'not_found');

      clear(discoverBox);
      if (!found.length) {
        // Seven "Not running" rows say the same thing once, badly.
        discoverBox.appendChild(
          h('div.muted', 'No supported apps are running. Start OBS or VTube Studio and scan again.')
        );
      }
      for (const result of found) discoverBox.appendChild(discoveryRow(result));
      if (missing.length) {
        discoverBox.appendChild(
          h(
            'details.fold',
            { style: { marginTop: '10px' } },
            h('summary', plural(missing.length, 'app') + ' not running'),
            ...missing.map(discoveryRow)
          )
        );
      }
    } catch (err) {
      clear(discoverBox).appendChild(h('div.test-result.bad', err.message));
    } finally {
      button.disabled = false;
      button.textContent = 'Scan again';
    }
  }

  await refreshStatus();
  draw();
  runScan();

  return subscribe((_, reason) => {
    // Status arrives over the socket unprompted, so this can fire while
    // someone is part-way down the page.
    if (reason === 'integrations' || reason === 'status' || reason === 'hello') {
      preserveView(draw);
    }
  });
}

const PASSWORD_HINTS = {
  streamer_bot:
    'Only needed for sending chat messages.',
  obs: 'From Tools > WebSocket Server Settings > Show Connect Info.',
  sammi: 'Only if you set one under SAMMI’s API settings.',
};

const NO_PASSWORD_HINTS = {
  mix_it_up: 'No password needed. Enable the API in Mix It Up under Services > Developer API.',
  speaker_bot: 'No password needed. Enable the WebSocket server under Servers/Clients.',
  vnyan: 'No password needed. VNyan listens on ws://127.0.0.1:8000/vnyan.',
};

function stateLabel(integration) {
  const map = {
    connected: ['good', 'Connected'],
    connecting: ['warn', 'Connecting…'],
    needs_auth: ['warn', 'Needs approval in VTube Studio'],
    error: ['bad', 'Not connected'],
    disconnected: ['', 'Off'],
  };
  const [kind, label] = map[integration.state] || ['', integration.state];
  return h('span.pill', { class: kind }, h('span.dot', { class: integration.state }), label);
}

function connectionCard(integration) {
  const host = h('input', { type: 'text', value: integration.host });
  const port = h('input', { type: 'number', value: integration.port });
  const password = h('input', {
    type: 'password',
    value: '',
    placeholder: integration.has_password ? '•••••••• (saved)' : 'No password set',
  });
  const enabled = h('input', { type: 'checkbox', checked: integration.enabled });

  const save = guard(async () => {
    await api.post('/integrations', {
      id: integration.id,
      kind: integration.kind,
      name: integration.name,
      enabled: enabled.checked,
      host: host.value.trim() || '127.0.0.1',
      port: Number(port.value),
      // An empty box means "keep the saved password".
      password: password.value === '' ? null : password.value,
    });
    invalidateOptions();
    toast(`${integration.name} saved`, 'ok');
  });

  const isVts = integration.kind === 'vtube_studio';
  const usesPassword = integration.uses_password;

  return h(
    'div.card',
    h('div.row', h('h2.grow', integration.name), stateLabel(integration)),
    integration.version ? h('p.card-hint', integration.version) : null,
    integration.last_error && integration.state !== 'connected'
      ? h('div.test-result.bad', { style: { marginBottom: '12px' } }, integration.last_error)
      : null,
    h(
      'div.grid',
      { style: { gridTemplateColumns: '2fr 1fr' } },
      h('div.field', h('label', 'Host'), host),
      h('div.field', h('label', 'Port'), port)
    ),
    isVts
      ? h(
          'p.card-hint',
          { style: { margin: '12px 0 0' } },
          integration.has_token
            ? 'WheelHat is authorised as a VTube Studio plugin.'
            : 'VTube Studio needs to approve WheelHat once. Press Authorise, then accept the popup inside VTS.'
        )
      : null,
    usesPassword
      ? h(
          'div.field',
          { style: { marginTop: '12px' } },
          h('label', 'Password'),
          password,
          PASSWORD_HINTS[integration.kind] ? h('div.help', PASSWORD_HINTS[integration.kind]) : null
        )
      : null,
    !usesPassword && !isVts
      ? h('p.card-hint', { style: { margin: '12px 0 0' } }, NO_PASSWORD_HINTS[integration.kind] || '')
      : null,
    h(
      'div.row.wrap',
      { style: { marginTop: '14px' } },
      h('label.switch', enabled, h('span', 'Enabled')),
      h('span.grow'),
      isVts
        ? h(
            'button.btn.small',
            {
              onclick: guard(async () => {
                toast('Check VTube Studio for the permission popup…', 'info', 8000);
                await api.post(`/integrations/${integration.id}/authorise`);
                invalidateOptions();
                toast('VTube Studio authorised', 'ok');
              }),
            },
            'Authorise'
          )
        : null,
      h('button.btn.small', { onclick: save }, 'Save'),
      h(
        'button.btn.small.primary',
        {
          onclick: guard(async () => {
            await save();
            await api.post(`/integrations/${integration.id}/connect`);
            invalidateOptions();
            toast(`Connected to ${integration.name}`, 'ok');
          }),
        },
        'Connect'
      )
    )
  );
}

function discoveryRow(result) {
  const statusPill = {
    ready: () => h('span.pill.good', h('span.dot.ready'), 'Ready to control'),
    listening: () => h('span.pill.warn', h('span.dot.listening'), 'Port open'),
    running_no_server: () => h('span.pill.warn', 'Running, server off'),
    not_found: () => h('span.pill', 'Not running'),
  }[result.status]();

  const detail = [
    result.version,
    result.detail,
    result.process_running ? `process ${result.process_name}` : null,
    `${result.host}:${result.port}`,
  ]
    .filter(Boolean)
    .join('  ·  ');

  return h(
    `div.discover-row.${result.status}`,
    h('div.grow', h('div.name', result.name), h('div.detail', detail)),
    statusPill,
    result.supported && result.status !== 'not_found'
      ? h(
          'button.btn.small.primary',
          {
            onclick: guard(async () => {
              const response = await api.post('/discovery/adopt', {
                app_id: result.id,
                host: result.host,
                port: result.port,
                kind: result.kind,
              });
              if (!response.ok) {
                toast(response.detail, 'info', 8000);
                return;
              }
              await refreshStatus();
              invalidateOptions();
              toast(`${result.name} added to your connections`, 'ok');
            }),
          },
          'Connect'
        )
      : null,
    !result.supported && result.status !== 'not_found'
      ? h(
          'button.btn.small.ghost',
          {
            title: 'How to control this app from WheelHat',
            onclick: () =>
              modal({
                title: result.name,
                hideConfirm: true,
                body: h(
                  'div',
                  h('p', result.notes || 'WheelHat has no dedicated connector for this app yet.'),
                  h(
                    'p.muted',
                    `It is listening on ${result.host}:${result.port}. Add an "HTTP request / webhook" action pointed at it, or use the app's own WebSocket API.`
                  ),
                  result.setup_hint ? h('p.muted', result.setup_hint) : null
                ),
              }),
          },
          'How?'
        )
      : null,
    result.status === 'running_no_server' && result.setup_hint
      ? h('span.detail', { style: { maxWidth: '260px' } }, result.setup_hint)
      : null
  );
}

/* =================================================================== twitch */

export async function renderTwitch(main) {
  const body = h('div');
  const page = h(
    'div.page',
    h(
      'div.page-head',
      h(
        'div',
        h('h1', 'Twitch'),
        h('p', 'Sign in so channel point redemptions, chat commands, bits and subs can spin your wheels.')
      )
    ),
    body
  );
  clear(main).appendChild(page);

  const draw = () => {
    clear(body);
    const status = store.twitch || {};

    // A build with no application of its own genuinely needs one first, so
    // there the card is step 1. A released build has one, and asking a user to
    // read three sentences about an override they will never want - second
    // thing on the page - is the wrong order. It goes at the bottom, closed.
    if (!status.client_id_set) {
      body.appendChild(clientIdCard(status));
      return;
    }

    if (!status.signed_in) {
      body.appendChild(signInCard(status));
    } else {
      body.appendChild(signedInCard(status));
      body.appendChild(rewardsCard(status));
      // Returns null when no wheel has a trigger: there is nothing to test.
      const test = testCard(status);
      if (test) body.appendChild(test);
    }

    if (status.using_bundled_client_id) {
      body.appendChild(
        h(
          'details.card',
          h('summary', 'Use your own Twitch application'),
          h(
            'p.card-hint',
            'WheelHat signs in through its own Twitch application. Paste a Client ID '
              + 'here only if you would rather use yours; clear it to go back.'
          ),
          clientIdCard(status, true, true)
        )
      );
    } else {
      // They have saved their own, so it stays visible and clearable.
      body.appendChild(clientIdCard(status, true));
    }
  };

  await refreshStatus();
  draw();
  return subscribe((_, reason) => {
    if (['twitch', 'status', 'hello'].includes(reason)) preserveView(draw);
  });
}

function clientIdCard(status, collapsed = false, bare = false) {
  const input = h('input', { type: 'text', value: status.client_id || '', placeholder: 'your application client id' });
  // A release build ships WheelHat's own application, so registering one is an
  // option rather than a first step. A build from source has nothing bundled
  // and still needs one, which is why the walkthrough stays.
  const bundled = Boolean(status.using_bundled_client_id);
  const heading = bundled
    ? 'Use your own Twitch application (optional)'
    : collapsed
      ? 'Twitch application'
      : 'Step 1: register a Twitch application';
  return h(
    bare ? 'div' : 'div.card',
    bare ? null : h('h2', heading),
    // When bare, the <details> around it already says this.
    bundled && !bare
      ? h(
          'p.card-hint',
          'WheelHat signs in through its own Twitch application. Paste a Client ID '
            + 'here only if you would rather use yours; clear it to go back.'
        )
      : null,
    collapsed || bundled
      ? null
      : h(
          'div',
          h(
            'ol.muted',
            { style: { marginTop: 0, paddingLeft: '20px', lineHeight: '1.9' } },
            h('li', 'Open the ', h('a', { href: 'https://dev.twitch.tv/console/apps/create', target: '_blank', rel: 'noreferrer' }, 'Twitch developer console'), ' and register a new application.'),
            h('li', 'Name it anything. An OAuth Redirect URL is required to create the app but the device code flow never uses it - ', h('code.mono', 'http://localhost'), ' is fine.'),
            h('li', 'Choose category "Application Integration" and client type ', h('strong', 'Public'), '.'),
            h('li', 'Copy the Client ID and paste it below.')
          )
        ),
    h('div.field', h('label', 'Client ID'), input),
    h(
      'div.row',
      { style: { marginTop: '12px' } },
      h(
        'button.btn.primary',
        {
          onclick: guard(async () => {
            const value = input.value.trim();
            if (!value && !bundled) {
              toast('Paste your Client ID first', 'bad');
              return;
            }
            await api.post('/twitch/client-id', { client_id: value });
            toast(value ? 'Client ID saved' : 'Using the built-in application', 'ok');
          }),
        },
        bundled && !input.value.trim() ? 'Save' : 'Save Client ID'
      )
    )
  );
}

function signInCard(status) {
  const flow = status.pending_flow;
  const box = h('div');

  if (flow) {
    box.append(
      h('p.card-hint', 'Open the link below on any device, then enter this code. This page updates itself once Twitch confirms.'),
      h('div.device-code', flow.user_code),
      h(
        'div.row',
        { style: { marginTop: '14px' } },
        h('a.btn.primary', { href: flow.verification_uri, target: '_blank', rel: 'noreferrer' }, 'Open twitch.tv/activate'),
        h('button.btn.ghost', { onclick: () => copyText(flow.user_code) }, 'Copy code'),
        h('span.grow'),
        h('span.faint', `expires in ${Math.round(flow.expires_in / 60)} min`)
      )
    );
  } else {
    // append() renders a null argument as the text "null", so drop empty slots
    // before handing them over - h() filters them, this does not.
    box.append(
      ...[
        h('p.card-hint', 'You will enter a short code on twitch.tv to approve WheelHat.'),
        status.flow_error ? h('div.test-result.bad', { style: { marginBottom: '12px' } }, status.flow_error) : null,
        h(
          'button.btn.primary',
          {
            onclick: guard(async () => {
              await api.post('/twitch/login');
              toast('Enter the code shown on this page at twitch.tv/activate', 'info', 8000);
            }),
          },
          'Sign in with Twitch'
        ),
      ].filter(Boolean)
    );
  }

  // "Step 2" only makes sense when there is a step 1. A released build signs in
  // through its own application, so connecting is the whole process.
  const heading = status.using_bundled_client_id
    ? 'Connect your Twitch account'
    : 'Step 2: connect your account';
  return h('div.card', h('h2', heading), box);
}

//: EventSub is our plumbing. What a streamer wants to know is whether Twitch
//: events are reaching WheelHat.
const LISTENING_STATE = {
  connected: 'Listening for Twitch events',
  connecting: 'Connecting…',
  disconnected: 'Not listening',
  error: 'Not listening',
};

function signedInCard(status) {
  return h(
    'div.card',
    h(
      'div.row',
      h(
        'div.grow',
        h('h2', `Signed in as ${status.display_name || status.login}`),
        h('p.card-hint', { style: { margin: '4px 0 0' } }, LISTENING_STATE[status.eventsub_state] || 'Not listening')
      ),
      h(
        'span.pill',
        { class: status.eventsub_state === 'connected' ? 'good' : 'warn' },
        h('span.dot', { class: status.eventsub_state }),
        status.eventsub_state === 'connected' ? 'listening' : status.eventsub_state
      )
    ),
    status.eventsub_error
      ? h('div.test-result.bad', { style: { marginTop: '12px' } }, status.eventsub_error)
      : null,
    // Moved here from the subscriptions card: a failed subscription is the one
    // actionable thing that card held, and errors belong with the connection.
    status.subscription_errors?.length
      ? h(
          'div',
          { style: { marginTop: '12px' } },
          status.subscription_errors.map((error) =>
            h('div.test-result.bad', { style: { marginTop: '6px' } }, error)
          )
        )
      : null,
    status.missing_scopes?.length
      ? h(
          'div.test-result.bad',
          { style: { marginTop: '12px' } },
          `Missing permissions: ${status.missing_scopes.join(', ')}. Sign out and back in to grant them.`
        )
      : null,
    h(
      'div.row.wrap',
      { style: { marginTop: '14px' } },
      h(
        'button.btn.small',
        {
          onclick: guard(async () => {
            await api.post('/twitch/resubscribe');
            toast('Re-synced EventSub subscriptions', 'ok');
          }),
        },
        'Reconnect to Twitch'
      ),
      h(
        'button.btn.small',
        {
          onclick: guard(async () => {
            const data = await api.get('/twitch/rewards');
            invalidateOptions('twitch.rewards');
            modal({
              title: 'Your channel point rewards',
              hideConfirm: true,
              body: data.rewards.length
                ? h(
                    'div.log',
                    data.rewards.map((reward) =>
                      h(
                        'div.log-row',
                        h('span.what', h('strong', reward.title), ' ', h('span.faint', `${reward.cost} points`)),
                        h('button.btn.small.ghost', { onclick: () => copyText(reward.id) }, 'Copy id')
                      )
                    )
                  )
                : h('p.muted', 'No custom rewards found on this channel.'),
            });
          }),
        },
        'View rewards'
      ),
      h('span.grow'),
      h(
        'button.btn.small.danger',
        {
          onclick: async () => {
            if (!(await confirmDialog('Sign out of Twitch?', {
              confirmLabel: 'Sign out',
              detail: 'Triggers will stop firing.',
            }))) return;
            await api.post('/twitch/logout');
            toast('Signed out', 'ok');
          },
        },
        'Sign out'
      )
    )
  );
}

/**
 * The rewards WheelHat owns, for tidying up.
 *
 * Creating one lives on a wheel's channel point trigger, not here. That is
 * where the need arises and where the new reward is immediately used, and one
 * job wants one home: the same form in two places is two implementations that
 * will drift. What is left here is the list and a way to delete.
 */
function rewardsCard(status) {
  const list = h('div.muted', 'Loading…');

  const refresh = async () => {
    try {
      const { rewards } = await api.get('/twitch/rewards?manageable=1');
      clear(list);
      if (!rewards.length) {
        list.appendChild(
          h('div.muted', 'None yet. Add a channel point trigger to a wheel and create one there.')
        );
        return;
      }
      for (const reward of rewards) {
        list.appendChild(
          h(
            'div.log-row',
            h('span.what', h('strong', reward.title), ` · ${reward.cost} points`),
            h('span.grow'),
            h(
              'button.btn.small.ghost',
              {
                onclick: guard(async () => {
                  if (!(await confirmDialog(`Delete the reward "${reward.title}" on Twitch?`))) return;
                  await api.del(`/twitch/rewards/${reward.id}`);
                  toast('Reward deleted', 'ok');
                  await refresh();
                }),
              },
              'Delete'
            )
          )
        );
      }
    } catch (err) {
      clear(list);
      list.appendChild(h('div.test-result.bad', err.message));
    }
  };

  // Channel points and bits only exist on affiliate and partner channels.
  if (status && status.has_channel_points === false) {
    return h(
      'div.card',
      h('h2', 'Channel point rewards'),
      h(
        'p.card-hint',
        'Channel points need affiliate or partner status, so there are no rewards yet. '
          + 'A chat command trigger spins a wheel on any channel.'
      ),
      h(
        'div.row',
        h(
          'a.btn.ghost',
          { href: 'https://help.twitch.tv/s/article/joining-the-affiliate-program', target: '_blank', rel: 'noreferrer' },
          'About the Affiliate Program'
        )
      )
    );
  }

  refresh();

  return h(
    'div.card',
    h('h2', 'Channel point rewards'),
    h(
      'p.card-hint',
      'Rewards WheelHat created. Only these can be marked fulfilled after a spin. '
        + 'Create one on a wheel’s channel point trigger.'
    ),
    list
  );
}

/**
 * Fire a trigger as though the event came from Twitch.
 *
 * It used to be hard-wired to a channel point redemption, which on a channel
 * without affiliate status tests an event that can never happen. It now offers
 * only the trigger types some wheel actually listens for, so the test matches
 * the wheels in front of you.
 */
function buildTestEvent(kind, who) {
  const login = who.toLowerCase().replace(/[^a-z0-9_]/g, '') || 'testviewer';
  const stamp = `test-${Date.now()}`;
  switch (kind) {
    case 'chat_command':
      return {
        event_type: 'channel.chat.message',
        event: {
          message_id: stamp,
          chatter_user_name: who,
          chatter_user_login: login,
          chatter_user_id: '000000',
          message: { text: '!spin' },
          badges: [{ set_id: 'broadcaster' }],
        },
      };
    case 'cheer':
      return {
        event_type: 'channel.cheer',
        event: { user_name: who, user_login: login, user_id: '000000', bits: 100, message: 'test cheer' },
      };
    case 'subscription':
      return {
        event_type: 'channel.subscribe',
        event: { user_name: who, user_login: login, user_id: '000000', tier: '1000' },
      };
    case 'follow':
      return {
        event_type: 'channel.follow',
        event: { user_name: who, user_login: login, user_id: '000000' },
      };
    case 'raid':
      return {
        event_type: 'channel.raid',
        event: {
          from_broadcaster_user_name: who,
          from_broadcaster_user_login: login,
          from_broadcaster_user_id: '000000',
          viewers: 25,
        },
      };
    default:
      return {
        event_type: 'channel.channel_points_custom_reward_redemption.add',
        event: {
          id: stamp,
          user_name: who,
          user_login: login,
          user_id: '000000',
          user_input: '',
          reward: { id: '', title: 'Spin the wheel', cost: 100 },
        },
      };
  }
}

function testCard(status) {
  // Only what some wheel listens for. Testing a trigger nothing uses proves
  // nothing, and on a channel without channel points it cannot even happen.
  const configured = new Set();
  for (const wheel of store.wheels || []) {
    for (const trigger of wheel.triggers || []) {
      if (trigger.enabled !== false && trigger.type !== 'manual') configured.add(trigger.type);
    }
  }
  if (status && status.has_channel_points === false) configured.delete('channel_points');
  if (!configured.size) return null;

  const kinds = [...configured];
  const kind = h(
    'select',
    kinds.map((type) =>
      h('option', { value: type }, (triggerSpec(type) || {}).label || type)
    )
  );
  const who = h('input', { type: 'text', value: 'TestViewer' });

  return h(
    'div.card',
    h('h2', 'Simulate an event'),
    h('p.card-hint', 'Fires your triggers as though the event came from Twitch.'),
    h(
      'div.grid.two',
      h('div.field', h('label', 'Event'), kind),
      h('div.field', h('label', 'Viewer name'), who)
    ),
    h(
      'div.row',
      { style: { marginTop: '12px' } },
      h(
        'button.btn.primary',
        {
          onclick: guard(async () => {
            await api.post('/twitch/simulate', buildTestEvent(kind.value, who.value || 'TestViewer'));
            toast('Event sent', 'ok');
          }),
        },
        'Send it'
      )
    )
  );
}

/* ================================================================= activity */

export async function renderActivity(main) {
  const feed = h('div.log');
  const actions = h('div.log');

  const page = h(
    'div.page',
    h('div.page-head', h('div', h('h1', 'Activity'), h('p', 'What WheelHat is doing right now.'))),
    h('div.card', h('h2', 'Events'), h('p.card-hint', 'Spins, triggers and skipped triggers.'), feed),
    h(
      'div.card',
      h(
        'div.row',
        h('div.grow', h('h2', 'Action results')),
        h('button.btn.small.ghost', { onclick: guard(async () => { await refreshStatus(); }) }, 'Refresh')
      ),
      actions
    )
  );

  clear(main).appendChild(page);

  const draw = () => {
    clear(feed);
    if (!store.activity.length) feed.appendChild(h('div.muted', 'Nothing yet. Spin a wheel and it will show up here.'));
    for (const entry of store.activity.slice(0, 60)) {
      feed.appendChild(
        h('div.log-row', { class: entry.bad ? 'bad' : '' }, h('span.when', clockTime(entry.at)), h('span.what', entry.text))
      );
    }

    clear(actions);
    if (!store.actionLog.length) actions.appendChild(h('div.muted', 'No actions have run yet.'));
    for (const entry of store.actionLog.slice(0, 80)) {
      actions.appendChild(
        h(
          'div.log-row',
          { class: entry.ok ? '' : 'bad' },
          h('span.when', clockTime(entry.created_at)),
          h('span.what', h('strong', entry.name || entry.action_type), ': ', entry.detail)
        )
      );
    }
  };

  await refreshStatus();
  draw();
  // Settings redraws on any store change, including ones the user did not cause.
  return subscribe(() => preserveView(draw));
}

/* ================================================================= settings */

export async function renderSettings(main) {
  await refreshSettings();
  const shell = h('input', { type: 'checkbox', checked: Boolean(store.settings.allow_shell_actions) });

  const page = h(
    'div.page',
    h('div.page-head', h('div', h('h1', 'Settings'))),
    h(
      'div.card',
      h('h2', 'Actions'),
      h(
        'label.switch',
        shell,
        h('span', 'Allow "Run a program" actions')
      ),
      h(
        'p.help',
        { style: { marginTop: '8px' } },
        'Lets a wheel slice launch programs on this machine.'
      ),
      h(
        'div.row',
        { style: { marginTop: '14px' } },
        h(
          'button.btn.primary',
          {
            onclick: guard(async () => {
              await api.put('/settings', { values: { allow_shell_actions: shell.checked } });
              await refreshSettings();
              toast('Settings saved', 'ok');
            }),
          },
          'Save'
        )
      )
    ),
    h(
      'div.card',
      h('h2', 'Backup'),
      h('p.card-hint', 'Exports every wheel as a JSON file you can import again.'),
      h(
        'div.row.wrap',
        h(
          'button.btn',
          {
            onclick: guard(async () => {
              const data = await api.get('/export');
              downloadJson('wheelhat-backup.json', data);
            }),
          },
          'Export everything'
        ),
        h('button.btn', { onclick: () => importWheel() }, 'Import a backup')
      )
    ),
    h(
      'div.card',
      h('h2', 'Where things live'),
      h(
        'div.grid',
        h('div', h('div.faint', 'Data folder'), h('div.mono', store.paths.data_dir || '')),
        h('div', h('div.faint', 'Database'), h('div.mono', store.paths.database || '')),
        h(
          'div',
          h('div.faint', 'Overlay assets'),
          h('div.mono', store.paths.assets_dir || ''),
          h('div.help', 'Files here are served as /assets/<name>.')
        ),
        h('div', h('div.faint', 'Server'), h('div.mono', `${store.server.host}:${store.server.port} · WheelHat ${store.server.version}`))
      )
    )
  );

  clear(main).appendChild(page);
  return () => {};
}

/* ------------------------------------------------------------------ helpers */

function downloadJson(filename, data) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const link = h('a', { href: url, download: filename });
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

function slugify(text) {
  return String(text).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'wheel';
}
