/** Reusable editor for a list of actions (used by slices and by wheel-level chains). */

import { api, clear, confirmDialog, copyText, h, modal, uid } from './core.js';
import { store } from './store.js';
import { renderFields, variableBar } from './fields.js';

let schemaCache = null;

export async function actionSchemas() {
  if (!schemaCache) schemaCache = await api.get('/actions/schemas');
  return schemaCache;
}

export function specFor(schemas, type) {
  return schemas.types.find((t) => t.type === type);
}

/** One-line preview of what an action will do, shown on the collapsed row. */
function summarise(action, spec) {
  if (!spec) return action.type;
  const config = action.config || {};
  const interesting = ['url', 'scene', 'source', 'hotkey', 'model', 'expression', 'text', 'message', 'command', 'request_type', 'message_type', 'seconds', 'target'];
  for (const key of interesting) {
    const value = config[key];
    if (value !== undefined && value !== null && String(value).trim() !== '') {
      return String(value).slice(0, 70);
    }
  }
  return spec.description?.slice(0, 70) || '';
}

/**
 * Which action types can actually do something right now, and why not.
 *
 * Every action already declares what it needs. Offering an OBS action to
 * someone with no OBS connection makes the app look full of things that fail:
 * the failure only appears when they expand the action or run it. Hiding them
 * silently would make it look feature-poor instead, so whatever is hidden is
 * named underneath with the way to get it back.
 */
function availability(schemas, context = {}) {
  const connected = new Set(
    (store.integrations || [])
      .filter((integration) => integration.state === 'connected')
      .map((integration) => integration.kind)
  );
  const signedIn = Boolean(store.twitch?.signed_in);
  const shellAllowed = Boolean(store.settings?.allow_shell_actions);
  const hasRedeemTrigger = (context.triggers || []).some(
    (trigger) => trigger.enabled !== false && trigger.type === 'channel_points'
  );

  const reasons = new Map();
  const usable = (type) => {
    if (type.type === 'shell_command') {
      if (!shellAllowed) {
        reasons.set('shell', 'Run a program is switched off in Settings.');
        return false;
      }
      return true;
    }
    // A refund needs a redemption to refund, which only a channel point
    // trigger on this wheel can produce.
    if (type.type === 'twitch_refund' && !hasRedeemTrigger) {
      reasons.set('refund', 'Refunding needs a channel point trigger on this wheel.');
      return false;
    }
    if (type.requires === 'twitch') {
      if (!signedIn) {
        reasons.set('twitch', 'Twitch is not connected.');
        return false;
      }
      return true;
    }
    if (type.requires && !connected.has(type.requires)) {
      reasons.set(type.requires, type.group);
      return false;
    }
    return true;
  };

  const types = schemas.types.filter(usable);
  const missingApps = [...reasons]
    .filter(([key]) => !['shell', 'twitch', 'refund'].includes(key))
    .map(([, group]) => group);
  return {
    types,
    missingApps: [...new Set(missingApps)],
    notes: ['twitch', 'shell', 'refund'].filter((k) => reasons.has(k)).map((k) => reasons.get(k)),
  };
}

function pickActionType(schemas, onPick, context = {}) {
  const body = h('div');
  const { types: allowed, missingApps, notes } = availability(schemas, context);
  for (const group of schemas.groups) {
    const types = allowed.filter((t) => t.group === group);
    if (!types.length) continue;
    body.appendChild(
      h(
        'div.type-group',
        h('h4', group),
        h(
          'div.type-options',
          types.map((type) =>
            h(
              'button.type-option',
              {
                type: 'button',
                onclick: () => {
                  dialog.close();
                  onPick(type);
                },
              },
              h('strong', type.label),
              type.description ? h('small', type.description) : null
            )
          )
        )
      )
    );
  }
  // Naming what is missing is what keeps this from reading as a small app.
  if (missingApps.length || notes.length) {
    const parts = [];
    if (missingApps.length) {
      parts.push(
        h('span', `Not shown: ${missingApps.join(', ')}. `),
        h('a', { href: '#/connections', onclick: () => dialog.close() }, 'Set them up on Connections'),
        h('span', '. ')
      );
    }
    for (const note of notes) parts.push(h('span', `${note} `));
    body.appendChild(h('div.help', { style: { marginTop: '14px' } }, ...parts));
  }

  const dialog = modal({ title: 'Add an action', body, hideConfirm: true, wide: true });
}

/**
 * @param {object} options
 * @param {Array} options.actions live array, mutated in place
 * @param {object} options.schemas from /api/actions/schemas
 * @param {string} options.wheelId used when testing an action
 * @param {Function} options.onChange
 * @param {string} options.emptyHint
 */
export function renderActionList({ actions, schemas, wheelId, onChange, emptyHint, context = {} }) {
  const list = h('div.action-list');
  const expanded = new Set();

  const redraw = () => {
    clear(list);

    if (!actions.length) {
      list.appendChild(
        h('div.empty', { style: { padding: '26px 18px' } }, h('div.muted', emptyHint || 'No actions yet.'))
      );
    }

    actions.forEach((action, index) => {
      const spec = specFor(schemas, action.type);
      const isOpen = expanded.has(action.id);
      const node = h('div.action');

      const head = h(
        'div.action-head',
        {
          onclick: (event) => {
            if (event.target.closest('input, button, select')) return;
            if (isOpen) expanded.delete(action.id);
            else expanded.add(action.id);
            redraw();
          },
        },
        h('span.faint', isOpen ? '▾' : '▸'),
        h('span.type-tag', spec ? spec.group.split(' ')[0] : '??'),
        h('span.action-name.grow', action.name || spec?.label || action.type),
        h('span.summary', summarise(action, spec)),
        h(
          'label.switch',
          { title: action.enabled ? 'Enabled' : 'Disabled' },
          h('input', {
            type: 'checkbox',
            checked: action.enabled !== false,
            onchange: (e) => {
              action.enabled = e.target.checked;
              onChange();
            },
          })
        ),
        h(
          'button.btn.small.icon.ghost',
          {
            title: 'Remove this action',
            onclick: async () => {
              if (!(await confirmDialog(`Remove "${action.name || spec?.label || action.type}"?`, { confirmLabel: 'Remove' }))) return;
              actions.splice(index, 1);
              onChange();
              redraw();
            },
          },
          '✕'
        )
      );
      node.appendChild(head);

      if (isOpen) {
        node.appendChild(buildBody(action, spec, schemas, wheelId, onChange, head));
      }
      list.appendChild(node);
    });

    list.appendChild(
      h(
        'button.btn.small',
        {
          style: { alignSelf: 'flex-start' },
          onclick: () =>
            pickActionType(schemas, (type) => {
              const action = {
                id: uid('act_'),
                type: type.type,
                name: type.label,
                enabled: true,
                config: {},
              };
              for (const field of type.fields) {
                if (field.default !== undefined && field.default !== null) {
                  action.config[field.key] = field.default;
                }
              }
              actions.push(action);
              expanded.add(action.id);
              onChange();
              redraw();
            }, context),
        },
        '+ Add action'
      )
    );
  };

  redraw();
  return list;
}

function buildBody(action, spec, schemas, wheelId, onChange, head) {
  const body = h('div.action-body');

  if (!spec) {
    body.appendChild(
      h('div.test-result.bad', `Unknown action type "${action.type}". It may come from a newer version of WheelHat.`)
    );
    return body;
  }

  const nameField = h(
    'div.field',
    h('label', 'Label'),
    h('input', {
      type: 'text',
      value: action.name || '',
      placeholder: spec.label,
      oninput: (e) => {
        action.name = e.target.value;
        head.querySelector('.action-name').textContent = e.target.value || spec.label;
        onChange();
      },
    })
  );

  action.config = action.config || {};
  const form = renderFields({
    fields: spec.fields,
    values: action.config,
    onChange: () => {
      head.querySelector('.summary').textContent = summarise(action, spec);
      onChange();
    },
  });

  const result = h('div.test-result', { hidden: true });

  const testButton = h(
    'button.btn.small',
    {
      onclick: async () => {
        testButton.disabled = true;
        testButton.textContent = 'Running…';
        try {
          const response = await api.post('/actions/test', {
            action,
            wheel_id: wheelId || '',
            winner: 'Test slice',
          });
          result.hidden = false;
          result.className = `test-result ${response.ok ? 'ok' : 'bad'}`;
          result.textContent = response.detail;
        } catch (err) {
          result.hidden = false;
          result.className = 'test-result bad';
          result.textContent = err.message;
        } finally {
          testButton.disabled = false;
          testButton.textContent = 'Test now';
        }
      },
    },
    'Test now'
  );

  const foot = h(
    'div.action-foot',
    testButton,
    h(
      'button.btn.small.ghost',
      {
        onclick: () => copyText(JSON.stringify(action, null, 2)),
        title: 'Copy this action as JSON',
      },
      'Copy JSON'
    ),
    spec.requires
      ? h('span.pill', `needs ${spec.requires.replace('_', ' ')}`)
      : null,
    h('span.grow'),
    h('span.faint.mono', spec.type)
  );

  body.append(h('div.action-fields', nameField), form);
  if (spec.fields.some((f) => f.templatable !== false && ['text', 'textarea', 'code'].includes(f.type))) {
    body.appendChild(
      h(
        'div',
        h('div.help', { style: { marginBottom: '4px' } }, 'Click to insert into the last text box you touched:'),
        variableBar(schemas.variables, form)
      )
    );
  }
  body.append(foot, result);
  return body;
}
