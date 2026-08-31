/**
 * Renders a form from a field schema.
 *
 * The backend describes each action's fields, including which ones should be
 * populated from a live application (`source: "obs.scenes"`). That is what lets
 * the editor offer a dropdown of the streamer's real scenes and hotkeys instead
 * of asking them to hand-write a request.
 */

import { api, clear, debounce, h } from './core.js';

const optionCache = new Map();

function cacheKey(source, params) {
  return `${source}?${new URLSearchParams(params).toString()}`;
}

export async function loadOptions(source, params = {}, { force = false } = {}) {
  const key = cacheKey(source, params);
  if (!force && optionCache.has(key)) return optionCache.get(key);
  const query = new URLSearchParams(params).toString();
  const result = await api.get(`/options/${source}${query ? `?${query}` : ''}`);
  optionCache.set(key, result);
  return result;
}

export function invalidateOptions(prefix = '') {
  if (!prefix) {
    optionCache.clear();
    return;
  }
  for (const key of Array.from(optionCache.keys())) {
    if (key.startsWith(prefix)) optionCache.delete(key);
  }
}

function visible(field, values) {
  const rule = field.when;
  if (!rule) return true;
  const current = values[rule.field];
  if (rule.equals) return rule.equals.some((v) => v === current);
  if (rule.not_equals) return !rule.not_equals.some((v) => v === current);
  return true;
}

/**
 * @param {object} options
 * @param {Array} options.fields  schema fields
 * @param {object} options.values live config object (mutated in place)
 * @param {Function} options.onChange called after any edit
 * @param {object} options.extraParams merged into every options request
 */
export function renderFields({ fields, values, onChange = () => {}, extraParams = {} }) {
  const root = h('div.action-fields');
  const controls = new Map();
  const state = { lastFocused: null };

  const change = () => {
    // Re-evaluate `when` rules and dependent dropdowns on every edit.
    for (const [key, control] of controls) {
      const field = fields.find((f) => f.key === key);
      control.wrapper.hidden = !visible(field, values);
    }
    for (const control of controls.values()) {
      if (control.refreshIfDependsOn) control.refreshIfDependsOn();
    }
    onChange(values);
  };

  for (const field of fields) {
    const control = buildField(field, values, change, extraParams, state);
    controls.set(field.key, control);
    control.wrapper.hidden = !visible(field, values);
    root.appendChild(control.wrapper);
  }

  root.insertVariable = (token) => {
    const target = state.lastFocused;
    if (!target) return false;
    const start = target.selectionStart ?? target.value.length;
    const end = target.selectionEnd ?? start;
    target.value = target.value.slice(0, start) + token + target.value.slice(end);
    target.selectionStart = target.selectionEnd = start + token.length;
    target.dispatchEvent(new Event('input', { bubbles: true }));
    target.focus();
    return true;
  };

  return root;
}

function labelFor(field) {
  return h('label', field.label, field.required ? h('span', { style: { color: 'var(--red)' } }, ' *') : null);
}

function buildField(field, values, change, extraParams, state) {
  const wrapper = h('div.field');
  const control = { wrapper };

  if (values[field.key] === undefined && field.default !== undefined && field.default !== null) {
    values[field.key] = field.default;
  }

  const trackFocus = (el) => {
    if (field.templatable !== false && ['text', 'textarea', 'code'].includes(field.type)) {
      el.addEventListener('focus', () => {
        state.lastFocused = el;
      });
    }
    return el;
  };

  switch (field.type) {
    case 'bool': {
      const input = h('input', {
        type: 'checkbox',
        checked: Boolean(values[field.key]),
        onchange: (e) => {
          values[field.key] = e.target.checked;
          change();
        },
      });
      wrapper.appendChild(h('label.switch', input, h('span', field.label)));
      if (field.help) wrapper.appendChild(h('div.help', field.help));
      return control;
    }

    case 'number': {
      const input = h('input', {
        type: 'number',
        value: values[field.key] ?? '',
        placeholder: field.placeholder || '',
        min: field.min,
        max: field.max,
        step: field.step ?? 'any',
        oninput: (e) => {
          values[field.key] = e.target.value === '' ? null : Number(e.target.value);
          change();
        },
      });
      wrapper.append(labelFor(field), input);
      if (field.help) wrapper.appendChild(h('div.help', field.help));
      return control;
    }

    case 'textarea':
    case 'code': {
      const input = trackFocus(
        h('textarea', {
          class: field.type === 'code' ? 'code' : '',
          rows: field.rows || 4,
          value: values[field.key] ?? '',
          placeholder: field.placeholder || '',
          oninput: (e) => {
            values[field.key] = e.target.value;
            change();
          },
        })
      );
      wrapper.append(labelFor(field), input);
      if (field.help) wrapper.appendChild(h('div.help', field.help));
      return control;
    }

    case 'keyvalue':
      return buildKeyValue(field, values, change, wrapper, control);

    case 'color': {
      const input = h('input', {
        type: 'color',
        value: values[field.key] || '#8b5cf6',
        oninput: (e) => {
          values[field.key] = e.target.value;
          change();
        },
      });
      wrapper.append(labelFor(field), input);
      return control;
    }

    case 'select':
      if (field.source) return buildLiveSelect(field, values, change, extraParams, wrapper, control, state);
      return buildStaticSelect(field, values, change, wrapper, control);

    default: {
      const input = trackFocus(
        h('input', {
          type: 'text',
          value: values[field.key] ?? '',
          placeholder: field.placeholder || '',
          oninput: (e) => {
            values[field.key] = e.target.value;
            change();
          },
        })
      );
      wrapper.append(labelFor(field), input);
      if (field.help) wrapper.appendChild(h('div.help', field.help));
      return control;
    }
  }
}

/**
 * "Redeem it and I will fill this in."
 *
 * WheelHat sees every channel point redemption anyway, but it only remembers
 * one while this is armed, and only the reward - never who redeemed it. That is
 * the difference between a convenience and a log of what viewers spend points
 * on, and it is why this is a button rather than something running in the
 * background.
 */
function attachRewardListen({ button, note, field, values, change, control }) {
  let timer = null;

  const stop = (message) => {
    clearInterval(timer);
    timer = null;
    button.textContent = 'Listen';
    button.disabled = false;
    if (note) {
      note.textContent = message || '';
      note.hidden = !message;
    }
  };

  // A field can be removed from the page while a listen is running.
  const observer = new MutationObserver(() => {
    if (!control.isConnected && timer) stop('');
  });
  observer.observe(document.body, { childList: true, subtree: true });

  const poll = async () => {
    let status;
    try {
      status = (await api.get('/twitch/status')).twitch;
    } catch {
      stop('Could not reach Twitch. Try again.');
      return;
    }
    const capture = status?.reward_capture || {};
    if (capture.reward) {
      values[field.key] = capture.reward.id;
      change();
      invalidateOptions(field.source);
      stop(`Using "${capture.reward.title}". Reload the list to see it by name.`);
      return;
    }
    if (!capture.listening) {
      stop('No redemption came through in time.');
      return;
    }
    button.textContent = `Listening… ${Math.ceil(capture.expires_in_ms / 1000)}s`;
  };

  button.addEventListener('click', async () => {
    if (timer) {
      await api.del('/twitch/rewards/listen').catch(() => {});
      stop('');
      return;
    }
    try {
      await api.post('/twitch/rewards/listen', {});
    } catch (err) {
      stop(err.message || 'Could not start listening.');
      return;
    }
    button.textContent = 'Listening…';
    if (note) {
      note.textContent = 'Go and redeem the reward on your channel.';
      note.hidden = false;
    }
    timer = setInterval(poll, 1500);
    poll();
  });
}

function buildStaticSelect(field, values, change, wrapper, control) {
  const select = h(
    'select',
    {
      onchange: (e) => {
        values[field.key] = e.target.value;
        change();
      },
    },
    (field.options || []).map((option) =>
      h('option', { value: option.value, selected: String(values[field.key]) === String(option.value) }, option.label)
    )
  );
  wrapper.append(labelFor(field), select);
  if (field.help) wrapper.appendChild(h('div.help', field.help));
  return control;
}

function buildLiveSelect(field, values, change, extraParams, wrapper, control, state) {
  const select = h('select');
  const error = h('div.error', { hidden: true });
  const help = field.help ? h('div.help', field.help) : null;
  let manual = false;

  const refreshButton = h(
    'button.btn.small.icon',
    { type: 'button', title: 'Reload this list from the app' },
    '⟳'
  );
  const manualButton = h(
    'button.btn.small.icon',
    { type: 'button', title: 'Type a value manually' },
    '✎'
  );

  const manualInput = h('input', {
    type: 'text',
    hidden: true,
    value: values[field.key] ?? '',
    placeholder: field.placeholder || '',
    oninput: (e) => {
      values[field.key] = e.target.value;
      change();
    },
  });
  if (field.templatable !== false) {
    manualInput.addEventListener('focus', () => {
      state.lastFocused = manualInput;
    });
  }

  // Some values are easier to demonstrate than to look up. A channel point
  // reward is one: rather than hunting for its id, arm a listen and redeem it.
  const listenButton = field.capture === 'twitch.reward'
    ? h('button.btn.small', { type: 'button', title: 'Redeem the reward on your channel and WheelHat will fill this in' }, 'Listen')
    : null;
  const listenNote = listenButton ? h('div.help', { hidden: true }) : null;

  const live = h(
    'div.select-live',
    select,
    refreshButton,
    field.allow_custom === false ? null : manualButton,
    listenButton
  );
  wrapper.append(labelFor(field), live, manualInput, error);
  if (listenNote) wrapper.appendChild(listenNote);
  if (help) wrapper.appendChild(help);

  if (listenButton) {
    attachRewardListen({ button: listenButton, note: listenNote, field, values, change, control });
  }

  select.addEventListener('change', () => {
    values[field.key] = select.value;
    change();
  });

  const setManual = (on) => {
    manual = on;
    live.hidden = on;
    manualInput.hidden = !on;
    if (on) manualInput.value = values[field.key] ?? '';
  };

  manualButton.addEventListener('click', () => setManual(true));

  const params = () => {
    const merged = { ...extraParams };
    for (const key of field.depends_on || []) {
      merged[key] = values[key] ?? '';
    }
    if (values.integration) merged.integration = values.integration;
    return merged;
  };

  let lastParams = '';

  const load = async ({ force = false } = {}) => {
    const currentParams = params();
    lastParams = JSON.stringify(currentParams);
    clear(select);
    select.appendChild(h('option', { value: '' }, 'Loading…'));
    select.disabled = true;
    error.hidden = true;

    let result;
    try {
      result = await loadOptions(field.source, currentParams, { force });
    } catch (err) {
      result = { options: [], error: err.message };
    }

    clear(select);
    select.disabled = false;

    if (result.error) {
      error.textContent = result.error;
      error.hidden = false;
      // Never trap the user: fall back to free text when the app is unreachable.
      if (field.allow_custom !== false) {
        setManual(true);
        return;
      }
      select.appendChild(h('option', { value: values[field.key] ?? '' }, values[field.key] || '—'));
      return;
    }

    const options = result.options || [];
    const hasBlank = options.some((option) => option.value === '');
    if (!hasBlank) select.appendChild(h('option', { value: '' }, '— choose —'));

    const groups = new Map();
    for (const option of options) {
      const key = option.group || '';
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(option);
    }
    for (const [groupName, groupOptions] of groups) {
      const target = groupName ? h('optgroup', { label: groupName }) : select;
      for (const option of groupOptions) {
        target.appendChild(h('option', { value: option.value }, option.label));
      }
      if (groupName) select.appendChild(target);
    }

    const current = values[field.key] ?? '';
    const known = options.some((option) => String(option.value) === String(current));
    if (current && !known) {
      // Keep a value that was configured while the app was closed.
      select.appendChild(h('option', { value: current }, `${current} (not in the app right now)`));
    }
    select.value = current;
    if (manual && !result.error) setManual(false);
  };

  refreshButton.addEventListener('click', () => {
    invalidateOptions(field.source);
    load({ force: true });
  });

  const debouncedLoad = debounce(() => load(), 250);
  control.refreshIfDependsOn = () => {
    if (!(field.depends_on || []).length && !values.integration) return;
    if (JSON.stringify(params()) !== lastParams) debouncedLoad();
  };

  load();
  return control;
}

function buildKeyValue(field, values, change, wrapper, control) {
  const list = h('div', { style: { display: 'grid', gap: '6px' } });
  const current = Array.isArray(values[field.key])
    ? values[field.key]
    : Object.entries(values[field.key] || {}).map(([key, value]) => ({ key, value }));
  values[field.key] = current;

  const redraw = () => {
    clear(list);
    current.forEach((pair, index) => {
      list.appendChild(
        h(
          'div.kv-row',
          h('input', {
            type: 'text',
            value: pair.key,
            placeholder: 'Header',
            oninput: (e) => {
              pair.key = e.target.value;
              change();
            },
          }),
          h('input', {
            type: 'text',
            value: pair.value,
            placeholder: 'Value',
            oninput: (e) => {
              pair.value = e.target.value;
              change();
            },
          }),
          h(
            'button.btn.small.icon.ghost',
            {
              type: 'button',
              title: 'Remove',
              onclick: () => {
                current.splice(index, 1);
                redraw();
                change();
              },
            },
            '✕'
          )
        )
      );
    });
    list.appendChild(
      h(
        'button.btn.small.ghost',
        {
          type: 'button',
          style: { justifySelf: 'start' },
          onclick: () => {
            current.push({ key: '', value: '' });
            redraw();
            change();
          },
        },
        '+ Add header'
      )
    );
  };

  redraw();
  wrapper.append(labelFor(field), list);
  if (field.help) wrapper.appendChild(h('div.help', field.help));
  return control;
}

/** Clickable {{variable}} chips that insert into the last focused text field. */
export function variableBar(variables, form) {
  return h(
    'div.varbar',
    variables.map((variable) =>
      h(
        'button.varchip',
        {
          type: 'button',
          title: variable.description,
          onclick: () => {
            const token = `{{${variable.name}}}`;
            if (!form.insertVariable(token)) {
              navigator.clipboard?.writeText(token);
            }
          },
        },
        `{{${variable.name}}}`
      )
    )
  );
}
