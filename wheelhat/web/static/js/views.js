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
  toast,
} from './core.js';
import { invalidateOptions } from './fields.js';
import { refreshSettings, refreshStatus, refreshWheels, store, subscribe } from './store.js';

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
    if (['wheels', 'overlays', 'spin_start', 'spin_finished', 'status'].includes(reason)) draw();
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
        `${wheel.overlay_clients || 0} source${wheel.overlay_clients === 1 ? '' : 's'}`
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
          disabled: wheel.spinning,
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
            if (!(await confirmDialog(`Delete "${wheel.name}"? This cannot be undone.`))) return;
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
  const wheel = await api.post('/wheels', {});
  await refreshWheels();
  location.hash = `#/wheel/${wheel.id}`;
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
      toast(`Imported ${result.imported} wheel(s)`, 'ok');
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

export async function renderConnections(main) {
  const grid = h('div.conn-grid');
  const discoverBox = h('div');

  const page = h(
    'div.page',
    h(
      'div.page-head',
      h(
        'div',
        h('h1', 'Connections'),
        h('p', 'WheelHat talks to OBS and VTube Studio directly, so your actions can pick real scenes and hotkeys from a list instead of you writing requests by hand.')
      )
    ),
    grid,
    h(
      'div.card',
      { style: { marginTop: '20px' } },
      h(
        'div.row',
        h('div.grow', h('h2', 'Find apps on this machine'), h('p.card-hint', { style: { margin: '4px 0 0' } }, 'Checks for the streaming tools WheelHat knows about and tells you which ones are ready to control.')),
        h('button.btn', { id: 'scanBtn', onclick: guard(runScan) }, 'Scan now')
      ),
      h('div', { style: { marginTop: '14px' } }, discoverBox)
    )
  );

  clear(main).appendChild(page);

  const draw = () => {
    clear(grid);
    for (const integration of store.integrations) grid.appendChild(connectionCard(integration));
  };

  async function runScan() {
    const button = page.querySelector('#scanBtn');
    button.disabled = true;
    button.textContent = 'Scanning…';
    clear(discoverBox).appendChild(h('div.muted', 'Probing local ports…'));
    try {
      const data = await api.get('/discovery');
      clear(discoverBox);
      for (const result of data.results) discoverBox.appendChild(discoveryRow(result));
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
    if (reason === 'integrations' || reason === 'status' || reason === 'hello') draw();
  });
}

const PASSWORD_HINTS = {
  streamer_bot:
    'Optional. Streamer.bot only needs a password for sending chat messages — everything else works without one.',
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
    needs_auth: ['warn', 'Needs authorisation'],
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
          'Use it'
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

    if (!status.client_id_set) {
      body.appendChild(clientIdCard(status));
      return;
    }
    if (!status.signed_in) {
      body.appendChild(signInCard(status));
      // Nothing to configure when the build brings its own application - the
      // only thing to do is connect. The card comes back for anyone who has
      // saved their own id, so they can still see and clear it.
      if (!status.using_bundled_client_id) body.appendChild(clientIdCard(status, true));
      return;
    }
    body.appendChild(signedInCard(status));
    body.appendChild(rewardsCard(status));
    body.appendChild(subscriptionsCard(status));
    body.appendChild(testCard());
  };

  await refreshStatus();
  draw();
  return subscribe((_, reason) => {
    if (['twitch', 'status', 'hello'].includes(reason)) draw();
  });
}

function clientIdCard(status, collapsed = false) {
  const input = h('input', { type: 'text', value: status.client_id || '', placeholder: 'your application client id' });
  // A release build ships WheelHat's own application, so registering one is an
  // option rather than a first step. A build from source has nothing bundled
  // and still needs one, which is why the walkthrough stays.
  const bundled = Boolean(status.using_bundled_client_id);
  const heading = bundled
    ? 'Use your own Twitch application (optional)'
    : collapsed
      ? 'Twitch application'
      : 'Step 1 — register a Twitch application';
  return h(
    'div.card',
    h('h2', heading),
    bundled
      ? h(
          'p.card-hint',
          'WheelHat is signing in through its own Twitch application, so there is '
            + 'nothing to register. Paste a Client ID here only if you would rather '
            + 'use your own; clear it to go back to the built-in one.'
        )
      : null,
    collapsed || bundled
      ? null
      : h(
          'div',
          h(
            'p.card-hint',
            'WheelHat signs in with your own Twitch app, so your account is never shared with anyone else. It takes about a minute:'
          ),
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
        h('p.card-hint', 'WheelHat will show you a short code to enter on twitch.tv. Nothing is typed into this app.'),
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
    : 'Step 2 — connect your account';
  return h('div.card', h('h2', heading), box);
}

function signedInCard(status) {
  return h(
    'div.card',
    h(
      'div.row',
      h(
        'div.grow',
        h('h2', `Signed in as ${status.display_name || status.login}`),
        h('p.card-hint', { style: { margin: '4px 0 0' } }, `EventSub: ${status.eventsub_state}${status.eventsub_error ? ` — ${status.eventsub_error}` : ''}`)
      ),
      h('span.pill', { class: status.eventsub_state === 'connected' ? 'good' : 'warn' }, h('span.dot', { class: status.eventsub_state }), status.eventsub_state)
    ),
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
        'Re-sync subscriptions'
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
            if (!(await confirmDialog('Sign out of Twitch? Triggers will stop firing.', { confirmLabel: 'Sign out' }))) return;
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
 * Create and manage the channel point rewards WheelHat owns.
 *
 * Two reasons this exists rather than sending people to Twitch: it saves anyone
 * having to go and find a reward id, and a reward created here belongs to
 * WheelHat - which is the only way Twitch will let it close the redemption once
 * the wheel has spun. Rewards made on Twitch itself can trigger a wheel, but
 * their redemptions can never be marked fulfilled from here.
 */
function rewardsCard(status) {
  const list = h('div.muted', 'Loading…');
  const title = h('input', { type: 'text', placeholder: 'Spin the wheel', maxlength: 45 });
  const cost = h('input', { type: 'number', min: 1, step: 50, value: 500 });
  const prompt = h('input', { type: 'text', placeholder: 'Shown to viewers when they redeem' });
  const cooldown = h('input', { type: 'number', min: 0, step: 5, value: 0 });

  const refresh = async () => {
    try {
      const { rewards } = await api.get('/twitch/rewards?manageable=1');
      clear(list);
      if (!rewards.length) {
        list.appendChild(h('div.muted', 'None yet. Create one below and pick it on a wheel’s trigger.'));
        return;
      }
      for (const reward of rewards) {
        list.appendChild(
          h(
            'div.log-row',
            h('span.what', h('strong', reward.title), ` — ${reward.cost} points`),
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
  // Offering a create form that Twitch will refuse is worse than saying so and
  // pointing at the thing that does work everywhere.
  if (status && status.has_channel_points === false) {
    return h(
      'div.card',
      h('h2', 'Channel point rewards'),
      h(
        'p.card-hint',
        'Channel points and bits are only available on affiliate and partner channels, '
          + 'so there are no rewards to create here yet. Nothing is broken — WheelHat '
          + 'is connected and working.'
      ),
      h(
        'p.card-hint',
        'Until then, drive your wheels from chat: add a '
          + 'Chat command trigger to a wheel, set it to something like !spin, and choose '
          + 'who is allowed to use it. It works on any channel, and you can restrict it '
          + 'to subscribers, VIPs or moderators.'
      ),
      h(
        'div.row',
        h(
          'a.btn.ghost',
          { href: 'https://help.twitch.tv/s/article/joining-the-affiliate-program', target: '_blank', rel: 'noreferrer' },
          'About the Affiliate Programme'
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
      'Rewards created here belong to WheelHat, so it can mark redemptions fulfilled '
        + 'once the wheel has spun. A reward you made on Twitch can still trigger a wheel, '
        + 'but Twitch will not let WheelHat close its redemptions.'
    ),
    list,
    h('div', { style: { marginTop: '16px' } }),
    h(
      'div.grid.two',
      h('div.field', h('label', 'Name'), title),
      h('div.field', h('label', 'Cost in points'), cost),
      h('div.field', h('label', 'Prompt (optional)'), prompt),
      h('div.field', h('label', 'Cooldown in seconds (0 = none)'), cooldown)
    ),
    h(
      'div.row',
      { style: { marginTop: '12px' } },
      h(
        'button.btn.primary',
        {
          onclick: guard(async () => {
            if (!title.value.trim()) {
              toast('Give the reward a name', 'bad');
              return;
            }
            await api.post('/twitch/rewards', {
              title: title.value.trim(),
              cost: Number(cost.value) || 1,
              prompt: prompt.value.trim(),
              cooldown_seconds: Number(cooldown.value) || 0,
            });
            toast('Reward created on your channel', 'ok');
            title.value = '';
            prompt.value = '';
            await refresh();
          }),
        },
        'Create the reward'
      )
    )
  );
}

const SUBSCRIPTION_LABELS = {
  'channel.channel_points_custom_reward_redemption.add': 'Channel point redemptions',
  'channel.chat.message': 'Chat messages',
  'channel.cheer': 'Bits cheered',
  'channel.subscribe': 'New subscriptions',
  'channel.subscription.gift': 'Gifted subscriptions',
  'channel.subscription.message': 'Resubscriptions',
  'channel.follow': 'New followers',
  'channel.raid': 'Raids',
  'stream.online': 'Going live',
};

function subscriptionsCard(status) {
  const subs = status.subscriptions || [];
  const always = subs.filter((sub) => sub.baseline);
  const fromTriggers = subs.filter((sub) => !sub.baseline);
  const pill = (sub) => h('span.pill.good', SUBSCRIPTION_LABELS[sub.type] || sub.type);

  return h(
    'div.card',
    h('h2', 'Event subscriptions'),
    h(
      'p.card-hint',
      'Channel point redemptions are picked up as soon as you sign in, so every '
        + 'reward on your channel is seen whether or not a wheel is using it yet. '
        + 'Everything else is added as your wheels need it.'
    ),
    always.length
      ? h('div.field', h('label', 'Always on'), h('div.row.wrap', always.map(pill)))
      : null,
    fromTriggers.length
      ? h(
          'div.field',
          { style: { marginTop: '12px' } },
          h('label', 'Added by your wheels'),
          h('div.row.wrap', fromTriggers.map(pill))
        )
      : null,
    !subs.length ? h('p.muted', 'Nothing subscribed yet.') : null,
    status.subscription_errors?.length
      ? h(
          'div',
          { style: { marginTop: '12px' } },
          status.subscription_errors.map((error) => h('div.test-result.bad', { style: { marginTop: '6px' } }, error))
        )
      : null
  );
}

function testCard() {
  const rewardTitle = h('input', { type: 'text', value: 'Spin the wheel', placeholder: 'Reward title' });
  const rewardId = h('input', { type: 'text', placeholder: 'Reward id (optional, matches first)' });
  const user = h('input', { type: 'text', value: 'TestViewer' });

  return h(
    'div.card',
    h('h2', 'Test a trigger without going live'),
    h('p.card-hint', 'Sends a fake redemption through the same path a real one takes, so you can check your wheel fires and your actions run.'),
    h(
      'div.grid.three',
      h('div.field', h('label', 'Reward title'), rewardTitle),
      h('div.field', h('label', 'Reward id'), rewardId),
      h('div.field', h('label', 'Viewer name'), user)
    ),
    h(
      'div.row',
      { style: { marginTop: '12px' } },
      h(
        'button.btn.primary',
        {
          onclick: guard(async () => {
            await api.post('/twitch/simulate', {
              event_type: 'channel.channel_points_custom_reward_redemption.add',
              event: {
                id: `test-${Date.now()}`,
                user_name: user.value,
                user_login: user.value.toLowerCase(),
                user_id: '000000',
                user_input: '',
                reward: { id: rewardId.value, title: rewardTitle.value, cost: 100 },
              },
            });
            toast('Simulated redemption sent', 'ok');
          }),
        },
        'Simulate a redemption'
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
    h('div.page-head', h('div', h('h1', 'Activity'), h('p', 'Live view of what WheelHat is doing. Handy for checking a webhook actually fired.'))),
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
          h('span.what', h('strong', entry.name || entry.action_type), ' — ', entry.detail)
        )
      );
    }
  };

  await refreshStatus();
  draw();
  return subscribe(() => draw());
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
        'Off by default. When on, a wheel slice can launch a program on this machine — only enable it if you are comfortable with that.'
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
      h('p.card-hint', 'Everything lives in a single SQLite file. Export gives you a portable JSON copy of all your wheels.'),
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
          h('div.help', 'Drop sound files here and reference them as /assets/yourfile.mp3 in an overlay sound action.')
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
