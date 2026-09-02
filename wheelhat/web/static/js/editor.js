/** The wheel editor: slices, actions, triggers, appearance and spin behaviour. */

import { actionSchemas, renderActionList } from './action-editor.js';
import {
  $,
  api,
  clear,
  confirmDialog,
  copyText,
  debounce,
  guard,
  h,
  modal,
  plural,
  toast,
  uid,
} from './core.js';
import { imageLayerControl } from './image-layer.js';
import { onImageSettled } from './image-cache.js';
import { renderFields } from './fields.js';
import { store, subscribe } from './store.js';
import { TRIGGER_TYPES, triggerSpec } from './trigger-schemas.js';
import { contrastColor, recommendedSource, shadowFilter, WheelRenderer } from './wheel-canvas.js';

const DEFAULT_PALETTE = [
  '#e5484d', '#f76b15', '#ffb224', '#46a758',
  '#12a594', '#0091ff', '#8e4ec6', '#e93d82',
];

const TAB_KEYS = ['slices', 'triggers', 'appearance', 'images', 'spin', 'chains'];

const EASINGS = [
  { value: 'easeOutQuint', label: 'Long dramatic slowdown' },
  { value: 'easeOutCubic', label: 'Gentle' },
  { value: 'easeOutExpo', label: 'Snappy stop' },
];

function blankImage() {
  return {
    url: '',
    enabled: true,
    scale: 1,
    offset_x: 0,
    offset_y: 0,
    rotation: 0,
    opacity: 1,
    radial: 0.6,
    size: 0.26,
    rotate_with_wheel: true,
    clip_to_slice: true,
    replace_label: false,
  };
}

export async function renderWheelEditor(main, wheelId) {
  let wheel;
  try {
    wheel = await api.get(`/wheels/${wheelId}`);
  } catch (err) {
    clear(main).appendChild(
      h('div.page', h('div.empty', h('h3', 'Wheel not found'), h('p', err.message), h('a.btn', { href: '#/wheels' }, 'Back to wheels')))
    );
    return () => {};
  }

  // Wheels saved by an older version predate image layers.
  for (const slice of wheel.slices) if (!slice.image) slice.image = blankImage();

  const schemas = await actionSchemas();
  let activeTab = sessionStorage.getItem('wheelhat.tab') || 'slices';

  /* ------------------------------------------------------------- persistence */

  const savedFlag = h('span.faint', '');
  let saving = false;

  const persist = async () => {
    saving = true;
    savedFlag.textContent = 'Saving…';
    try {
      const payload = { ...wheel };
      delete payload.overlay_url;
      delete payload.trigger_url;
      delete payload.overlay_clients;
      delete payload.spinning;
      const updated = await api.put(`/wheels/${wheelId}`, payload);
      wheel.updated_at = updated.updated_at;
      dirty = false;
      savedFlag.textContent = 'All changes saved';
    } catch (err) {
      savedFlag.textContent = '';
      toast(`Could not save: ${err.message}`, 'bad', 7000);
    } finally {
      saving = false;
    }
  };

  const save = debounce(persist, 650);

  let dirty = false;

  const changed = ({ preview = true } = {}) => {
    dirty = true;
    savedFlag.textContent = 'Unsaved changes…';
    if (preview) updatePreview();
    save();
  };

  window.addEventListener('beforeunload', () => {
    // Only write if this editor actually has pending edits. Flushing
    // unconditionally would push a stale copy over anything changed elsewhere.
    if (dirty && !saving) save.flush();
  });

  /* ---------------------------------------------------------------- preview */

  const canvas = h('canvas');
  const previewBox = h('div.preview-box', canvas);
  const renderer = new WheelRenderer(canvas);

  function updatePreview() {
    // The preview should look like the overlay: same shape as the configured
    // source, and the same shadow. A square preview cannot show a wide
    // overlay being cropped, which is the thing worth seeing.
    const sourceW = Math.max(160, Number(wheel.appearance.source_width) || 1280);
    const sourceH = Math.max(160, Number(wheel.appearance.source_height) || 720);
    previewBox.style.aspectRatio = `${sourceW} / ${sourceH}`;
    canvas.style.filter = shadowFilter(wheel.appearance, canvas.getBoundingClientRect().height);
    renderer.setState({
      slices: wheel.slices
        .filter((s) => s.enabled && s.weight > 0)
        .map((s) => ({
          id: s.id,
          label: s.label,
          weight: s.weight,
          color: s.color,
          text_color: s.text_color,
          border_color: s.border_color,
          text_stroke_color: s.text_stroke_color,
          image: s.image,
        })),
      appearance: wheel.appearance,
    });
  }

  const resizeObserver = new ResizeObserver(() => renderer.resize());
  resizeObserver.observe(previewBox);

  /* ----------------------------------------------------------------- header */

  const nameInput = h('input', {
    type: 'text',
    value: wheel.name,
    style: { fontSize: '19px', fontWeight: '700' },
    oninput: (e) => {
      wheel.name = e.target.value;
      changed({ preview: false });
    },
  });

  const spinButton = h(
    'button.btn.primary',
    {
      onclick: guard(async () => {
        save.flush();
        await new Promise((resolve) => setTimeout(resolve, 120));
        const result = await api.post(`/wheels/${wheelId}/spin`, {
          source: 'manual',
          skip_actions: !fireActions.checked,
          ignore_cooldown: true,
        });
        localSpin(result);
      }),
    },
    'Spin'
  );

  /** Nothing to land on means nothing to spin. Kept in step with the slices. */
  function refreshSpinButton() {
    const spinnable = wheel.slices.some((slice) => slice.enabled && (slice.weight || 0) > 0);
    spinButton.disabled = !spinnable;
    spinButton.title = spinnable ? '' : 'Add a slice first';
  }

  const fireActions = h('input', { type: 'checkbox', checked: false });

  function localSpin(result) {
    renderer.spin({
      targetIndex: result.target_index,
      durationMs: result.duration_ms,
      turns: 6,
      easing: wheel.spin.easing,
      spinId: result.spin_id,
    });
  }

  const overlayUrl = wheel.overlay_url;

  const head = h(
    'div.page-head',
    h('a.btn.ghost', { href: '#/wheels', title: 'Back to all wheels' }, '←'),
    h(
      'div.grow',
      nameInput,
      h(
        'div.row',
        { style: { marginTop: '8px' } },
        h(
          'label.switch',
          h('input', {
            type: 'checkbox',
            checked: wheel.enabled,
            onchange: (e) => {
              wheel.enabled = e.target.checked;
              changed({ preview: false });
            },
          }),
          h('span', 'Triggers active')
        ),
        savedFlag
      )
    ),
    h('label.switch', fireActions, h('span', 'Run actions on test spin')),
    spinButton
  );

  /* ------------------------------------------------------------------- tabs */

  const tabBody = h('div');
  const tabs = h(
    'div.tabs',
    [
      ['slices', `Slices (${wheel.slices.length})`],
      ['triggers', `Triggers (${wheel.triggers.length})`],
      ['appearance', 'Look'],
      ['images', 'Images'],
      ['spin', 'Spin behaviour'],
      ['chains', 'Before / after'],
    ].map(([key, label]) =>
      h(
        'button',
        {
          class: key === activeTab ? 'active' : '',
          onclick: () => {
            activeTab = key;
            sessionStorage.setItem('wheelhat.tab', key);
            for (const button of tabs.children) button.classList.remove('active');
            tabs.children[TAB_KEYS.indexOf(key)].classList.add('active');
            drawTab();
          },
        },
        label
      )
    )
  );

  function drawTab() {
    clear(tabBody);
    if (activeTab === 'slices') tabBody.appendChild(slicesTab());
    else if (activeTab === 'triggers') tabBody.appendChild(triggersTab());
    else if (activeTab === 'appearance') tabBody.appendChild(appearanceTab());
    else if (activeTab === 'images') tabBody.appendChild(imagesTab());
    else if (activeTab === 'spin') tabBody.appendChild(spinTab());
    else tabBody.appendChild(chainsTab());
  }

  function refreshTabCounts() {
    tabs.children[0].textContent = `Slices (${wheel.slices.length})`;
    tabs.children[1].textContent = `Triggers (${wheel.triggers.length})`;
    refreshSpinButton();
  }

  /* ---------------------------------------------------------------- slices */

  const expandedSlices = new Set();
  const expandedImages = new Set();

  function slicesTab() {
    const wrap = h('div');
    const list = h('div.slice-list');

    const redraw = () => {
      clear(list);
      wheel.slices.forEach((slice, index) => {
        list.appendChild(sliceRow(slice, index, redraw));
      });
      refreshTabCounts();
    };

    wrap.appendChild(
      h(
        'div.row.wrap',
        { style: { marginBottom: '14px' } },
        h(
          'button.btn.primary.small',
          {
            onclick: () => {
              const slice = {
                id: uid('sl_'),
                label: `Option ${wheel.slices.length + 1}`,
                weight: 1,
                color: (wheel.appearance.palette || DEFAULT_PALETTE)[
                  wheel.slices.length % (wheel.appearance.palette || DEFAULT_PALETTE).length
                ],
                enabled: true,
                image: blankImage(),
                cooldown_spins: 0,
                remove_on_win: false,
                actions: [],
                won_count: 0,
                cooldown_remaining: 0,
              };
              wheel.slices.push(slice);
              expandedSlices.add(slice.id);
              changed();
              redraw();
            },
          },
          '+ Add slice'
        ),
        h('button.btn.small', { onclick: () => bulkEdit(redraw) }, 'Bulk edit'),
        h(
          'button.btn.small',
          {
            onclick: () => {
              const palette = wheel.appearance.palette?.length ? wheel.appearance.palette : DEFAULT_PALETTE;
              wheel.slices.forEach((slice, index) => {
                slice.color = palette[index % palette.length];
              });
              changed();
              redraw();
            },
          },
          'Recolour from palette'
        ),
        h('span.grow'),
        // Only when something is actually eliminated or counting down. A button
        // that resets nothing is a question the user has to answer every visit.
        wheel.slices.some(
          (slice) => (slice.cooldown_remaining || 0) > 0 || (slice.remove_on_win && !slice.enabled)
        )
          ? h(
              'button.btn.small.ghost',
              {
                title: 'Re-enable eliminated slices and clear cooldowns',
                onclick: guard(async () => {
                  const updated = await api.post(`/wheels/${wheelId}/reset`);
                  wheel.slices = updated.slices;
                  updatePreview();
                  redraw();
                  toast('Wheel reset', 'ok');
                }),
              },
              'Reset eliminations'
            )
          : null
      )
    );

    redraw();
    wrap.appendChild(list);
    return wrap;
  }

  /**
   * A slice's label outline: the colour, and the width that makes it visible.
   *
   * The width is a wheel-wide setting on the Look tab. Telling someone to go
   * and set it there turns picking a colour into a trip across two tabs, so it
   * is offered here and writes through to the same field.
   */
  function outlineField(slice, redraw) {
    const width = wheel.appearance.text_stroke_width || 0;
    const colour = optionalColorField(
      'Label outline',
      slice.text_stroke_color,
      wheel.appearance.text_stroke_color || '#000000',
      (value) => {
        slice.text_stroke_color = value;
        changed();
      }
    );
    if (width > 0) return colour;
    return h(
      'div.field',
      colour,
      h(
        'div.row',
        { style: { marginTop: '6px' } },
        h('span.help', 'Outlines are off for this wheel.'),
        h(
          'button.btn.small.ghost',
          {
            onclick: () => {
              wheel.appearance.text_stroke_width = 2;
              changed();
              redraw();
            },
          },
          'Turn them on'
        )
      )
    );
  }

  function paletteColor(index) {
    const palette = wheel.appearance.palette?.length ? wheel.appearance.palette : DEFAULT_PALETTE;
    return palette[index % palette.length];
  }

  function sliceRow(slice, index, redraw) {
    const isOpen = expandedSlices.has(slice.id);
    const node = h('div.slice', { class: slice.enabled ? '' : 'disabled' });

    const head = h(
      'div.slice-head',
      h('input.slice-swatch', {
        type: 'color',
        value: slice.color || paletteColor(index),
        title: 'Slice colour',
        oninput: (e) => {
          slice.color = e.target.value;
          changed();
        },
      }),
      h('input.slice-label.grow', {
        type: 'text',
        value: slice.label,
        oninput: (e) => {
          slice.label = e.target.value;
          changed();
        },
      }),
      h(
        'div.slice-meta',
        slice.image?.url
          ? h('img.slice-thumb', { src: slice.image.url, alt: '', title: 'This slice has an image' })
          : null,
        slice.actions.length
          ? h('span.pill', `${slice.actions.length} action${slice.actions.length === 1 ? '' : 's'}`)
          : null,
        slice.won_count
          ? h('span.pill', { title: 'Times this slice has won' }, `won ${slice.won_count}`)
          : null
      ),
      h(
        'div.slice-controls',
        h(
          'label.slice-weight-field',
          { title: 'Weight. A slice at 2 is twice as likely as one at 1.' },
          h('span', '×'),
          h('input.slice-weight', {
            type: 'number',
            min: 0,
            step: 0.5,
            value: slice.weight,
            oninput: (e) => {
              slice.weight = Number(e.target.value) || 0;
              changed();
            },
          })
        ),
        h(
          'label.switch',
          { title: 'On the wheel' },
          h('input', {
            type: 'checkbox',
            checked: slice.enabled,
            onchange: (e) => {
              slice.enabled = e.target.checked;
              node.classList.toggle('disabled', !slice.enabled);
              changed();
            },
          })
        ),
        h(
          'button.btn.small.icon.ghost',
          {
            title: isOpen ? 'Collapse' : 'Edit actions',
            onclick: () => {
              if (isOpen) expandedSlices.delete(slice.id);
              else expandedSlices.add(slice.id);
              redraw();
            },
          },
          isOpen ? '▾' : '▸'
        ),
        h(
          'button.btn.small.icon.ghost',
          {
            title: 'Duplicate',
            onclick: () => {
              const copy = JSON.parse(JSON.stringify(slice));
              copy.id = uid('sl_');
              copy.won_count = 0;
              copy.cooldown_remaining = 0;
              copy.actions.forEach((action) => {
                action.id = uid('act_');
              });
              wheel.slices.splice(index + 1, 0, copy);
              changed();
              redraw();
            },
          },
          '⧉'
        ),
        h(
          'button.btn.small.icon.ghost',
          {
            title: 'Delete slice',
            onclick: async () => {
              if (!(await confirmDialog(`Delete "${slice.label}"?`, { detail: 'Its actions go too.' }))) return;
              wheel.slices.splice(index, 1);
              changed();
              redraw();
            },
          },
          '✕'
        )
      )
    );
    node.appendChild(head);

    if (isOpen) {
      const body = h(
        'div.slice-body',
        h(
          'div.slice-opts',
          h(
            'label.switch',
            h('input', {
              type: 'checkbox',
              checked: slice.remove_on_win,
              onchange: (e) => {
                slice.remove_on_win = e.target.checked;
                changed();
              },
            }),
            h('span', { title: 'Elimination wheels: the slice disappears once it wins' }, 'Remove after it wins')
          ),
          h(
            'div.field',
            { style: { maxWidth: '190px' } },
            h('label', 'Skip for the next N spins after winning'),
            h('input', {
              type: 'number',
              min: 0,
              value: slice.cooldown_spins || 0,
              oninput: (e) => {
                slice.cooldown_spins = Number(e.target.value) || 0;
                changed();
              },
            })
          ),
          slice.cooldown_remaining
            ? h('span.pill.warn', `on cooldown for ${plural(slice.cooldown_remaining, 'more spin')}`)
            : null,
          optionalColorField(
            'Label colour',
            slice.text_color,
            contrastColor(slice.color || paletteColor(index)),
            (value) => {
              slice.text_color = value;
              changed();
            }
          ),
          optionalColorField('Inline colour', slice.border_color, wheel.appearance.slice_border_color, (value) => {
            slice.border_color = value;
            changed();
          }),
          outlineField(slice, redraw)
        ),
        (() => {
          if (!slice.image) slice.image = blankImage();
          const open = expandedImages.has(slice.id);
          const body = h('div');
          const head = h(
            'div.row',
            { style: { marginBottom: open ? '10px' : '0' } },
            h('strong', 'Image'),
            slice.image.url ? h('span.pill.good', 'set') : h('span.pill', 'none'),
            h('span.grow'),
            h(
              'button.btn.small.ghost',
              {
                onclick: () => {
                  if (open) expandedImages.delete(slice.id);
                  else expandedImages.add(slice.id);
                  redraw();
                },
              },
              open ? 'Hide' : (slice.image.url ? 'Edit image' : 'Add an image')
            )
          );
          if (open) {
            body.appendChild(
              imageLayerControl({
                layer: slice.image,
                onChange: () => changed(),
                slice: true,
                hint: 'Shown inside this wedge. A transparent PNG works best.',
              })
            );
          }
          return h('div.slice-image-section', head, body);
        })(),
        h(
          'div',
          h('div.row', { style: { marginBottom: '8px' } },
            h('strong', 'When this slice wins'),
            h('span.grow'),
            h(
              'button.btn.small.ghost',
              {
                onclick: guard(async () => {
                  save.flush();
                  await new Promise((resolve) => setTimeout(resolve, 120));
                  const result = await api.post(`/wheels/${wheelId}/spin`, {
                    source: 'manual',
                    force_slice_id: slice.id,
                    ignore_cooldown: true,
                  });
                  localSpin(result);
                  toast(`Forcing a win on "${slice.label}" - actions will fire`, 'info');
                }),
                title: 'Spin the wheel but make this slice win, so you can watch the actions run',
              },
              'Force a win'
            )
          ),
          renderActionList({
            actions: slice.actions,
            schemas,
            wheelId,
            onChange: () => changed({ preview: false }),
            // Lets the picker hide a refund action on a wheel with no
            // channel point trigger, where it could never do anything.
            context: { triggers: wheel.triggers },
            emptyHint: 'Nothing happens when this slice wins yet.',
          })
        )
      );
      node.appendChild(body);
    }

    return node;
  }

  function bulkEdit(redraw) {
    const textarea = h('textarea', {
      class: 'code',
      rows: 14,
      value: wheel.slices.map((s) => s.label).join('\n'),
    });
    const replace = h('input', { type: 'checkbox', checked: true });
    modal({
      title: 'Bulk edit slices',
      body: h(
        'div',
        h('p.muted', { style: { marginTop: 0 } }, 'One slice per line. Replacing rebuilds the wheel from scratch, which clears any actions on the old slices.'),
        textarea,
        h('label.switch', { style: { marginTop: '12px' } }, replace, h('span', 'Replace existing slices'))
      ),
      confirmLabel: 'Apply',
      onConfirm: guard(async () => {
        const updated = await api.post(`/wheels/${wheelId}/slices/bulk`, {
          text: textarea.value,
          replace: replace.checked,
        });
        wheel.slices = updated.slices;
        updatePreview();
        redraw();
        toast('Slices updated', 'ok');
      }),
    });
  }

  /* -------------------------------------------------------------- triggers */

  function triggersTab() {
    const wrap = h('div');
    const list = h('div.action-list');

    const redraw = () => {
      clear(list);
      if (!wheel.triggers.length) {
        list.appendChild(
          h(
            'div.empty',
            h('h3', 'No automatic triggers'),
            h('p', 'This wheel only spins when you press the button. Add a trigger to let channel points, chat or bits spin it for you.')
          )
        );
      }

      wheel.triggers.forEach((trigger, index) => {
        const spec = triggerSpec(trigger.type);
        const node = h('div.action');
        const body = h('div.action-body');

        node.appendChild(
          h(
            'div.action-head',
            h('span.type-tag', 'Trigger'),
            h('span.action-name.grow', spec.label),
            spec.needsTwitch && !store.twitch?.signed_in
              ? h('span.pill.warn', 'sign in to Twitch')
              : null,
            // Silently never firing is the worst outcome; say why up front.
            spec.needsAffiliate && store.twitch?.signed_in && store.twitch?.has_channel_points === false
              ? h(
                  'span.pill.warn',
                  { title: 'Channel points and bits need affiliate or partner status. A chat command trigger works on any channel.' },
                  'needs affiliate'
                )
              : null,
            h(
              'label.switch',
              h('input', {
                type: 'checkbox',
                checked: trigger.enabled,
                onchange: (e) => {
                  trigger.enabled = e.target.checked;
                  changed({ preview: false });
                },
              })
            ),
            h(
              'button.btn.small.icon.ghost',
              {
                title: 'Remove trigger',
                onclick: async () => {
                  if (!(await confirmDialog(`Remove the ${spec.label} trigger?`, { confirmLabel: 'Remove' }))) return;
                  wheel.triggers.splice(index, 1);
                  changed({ preview: false });
                  redraw();
                },
              },
              '✕'
            )
          )
        );

        body.appendChild(h('p.muted', { style: { margin: '10px 0 0' } }, spec.description));
        trigger.config = trigger.config || {};
        if (spec.fields.length) {
          body.appendChild(
            renderFields({
              fields: spec.fields,
              values: trigger.config,
              onChange: () => changed({ preview: false }),
            })
          );
        }
        node.appendChild(body);
        list.appendChild(node);
      });

      list.appendChild(
        h(
          'button.btn.small',
          {
            style: { alignSelf: 'flex-start' },
            onclick: () => addTrigger(redraw),
          },
          '+ Add trigger'
        )
      );
      refreshTabCounts();
    };

    redraw();
    wrap.appendChild(list);
    return wrap;
  }

  function addTrigger(redraw) {
    // Every trigger this offers needs Twitch: a wheel with no triggers is
    // already manual, which is why `manual` is filtered out below. So being
    // signed out means there is genuinely nothing to choose, and offering six
    // options that cannot work - then explaining that in a pill afterwards - is
    // worse than saying so once, here, with the way to fix it.
    const twitch = store.twitch || {};
    if (!twitch.signed_in) {
      const dialog = modal({
        title: 'Add a trigger',
        hideConfirm: true,
        body: h(
          'div',
          h('p.card-hint', 'Automatic triggers need a Twitch account.'),
          h(
            'a.btn.primary',
            {
              href: '#/twitch',
              onclick: () => dialog.close(),
            },
            'Connect Twitch'
          )
        ),
      });
      return;
    }

    // Channel points and bits do not exist on a channel without affiliate
    // status, so those two are left out rather than added and then flagged.
    const noChannelPoints = twitch.has_channel_points === false;
    const offered = TRIGGER_TYPES.filter(
      (t) => t.type !== 'manual' && !(noChannelPoints && t.needsAffiliate)
    );
    const hidden = TRIGGER_TYPES.filter(
      (t) => t.type !== 'manual' && noChannelPoints && t.needsAffiliate
    );

    const body = h(
      'div',
      h(
        'div.type-options',
        offered.map((spec) =>
          h(
            'button.type-option',
            {
              type: 'button',
              onclick: () => {
                dialog.close();
                const trigger = { id: uid('trg_'), type: spec.type, enabled: true, config: {} };
                for (const field of spec.fields) {
                  if (field.default !== undefined) trigger.config[field.key] = field.default;
                }
                wheel.triggers.push(trigger);
                changed({ preview: false });
                redraw();
              },
            },
            h('strong', spec.label),
            h('small', spec.description)
          )
        )
      ),
      hidden.length
        ? h(
            'div.help',
            { style: { marginTop: '12px' } },
            `Not shown: ${hidden.map((t) => t.label).join(', ')}. Channel points and bits need `,
            h(
              'a',
              {
                href: 'https://help.twitch.tv/s/article/joining-the-affiliate-program',
                target: '_blank',
                rel: 'noreferrer',
              },
              'affiliate status'
            ),
            '.'
          )
        : null
    );
    const dialog = modal({ title: 'Add a trigger', body, hideConfirm: true });
  }

  /* ------------------------------------------------------------ appearance */

  function appearanceTab() {
    const a = wheel.appearance;
    const bind = (key, transform = (v) => v) => (e) => {
      a[key] = transform(e.target.type === 'checkbox' ? e.target.checked : e.target.value);
      changed();
    };

    const paletteBox = h('div.palette-grid');
    const drawPalette = () => {
      clear(paletteBox);
      (a.palette || []).forEach((color, index) => {
        paletteBox.appendChild(
          h(
            'div.palette-chip',
            h('input.palette-swatch', {
              type: 'color',
              value: color,
              title: color,
              oninput: (e) => {
                a.palette[index] = e.target.value;
                changed();
              },
            }),
            h(
              'button.palette-remove',
              {
                type: 'button',
                title: `Remove ${color}`,
                onclick: () => {
                  a.palette.splice(index, 1);
                  drawPalette();
                  changed();
                },
              },
              '✕'
            )
          )
        );
      });
      paletteBox.appendChild(
        h(
          'button.btn.small',
          {
            onclick: () => {
              a.palette = [...(a.palette || []), DEFAULT_PALETTE[(a.palette?.length || 0) % DEFAULT_PALETTE.length]];
              drawPalette();
              changed();
            },
          },
          '+ Colour'
        )
      );
    };
    drawPalette();

    return h(
      'div',
      h(
        'div.card',
        h('h2', 'Palette'),
        h('p.card-hint', 'Used for slices that do not have their own colour set.'),
        paletteBox
      ),
      h(
        'div.card',
        h('h2', 'Colours'),
        h(
          'div.grid.three',
          colorField('Rim', a.rim_color, bind('rim_color')),
          colorField('Pointer', a.pointer_color, bind('pointer_color')),
          colorField('Hub', a.hub_color, bind('hub_color')),
          colorField('Hub label colour', a.text_color, bind('text_color')),
          optionalColorField(
            'Label colour (all slices)',
            a.label_color,
            '#ffffff',
            (value) => {
              a.label_color = value || '';
              changed();
            },
            'Auto picks black or white per wedge for contrast. A slice can still override this.'
          )
        )
      ),
      h(
        'div.card',
        h('h2', 'Type and size'),
        h(
          'div.grid.two',
          field('Hub label', h('input', { type: 'text', value: a.hub_label, maxlength: 8, placeholder: 'SPIN', oninput: bind('hub_label') })),
          field('Font family', h('input', { type: 'text', value: a.font_family, oninput: bind('font_family') })),
          field('Label size', h('input', { type: 'number', min: 8, max: 64, value: a.font_size, oninput: bind('font_size', Number) })),
          field('Label weight', h('input', { type: 'number', min: 300, max: 900, step: 100, value: a.font_weight, oninput: bind('font_weight', Number) })),
          a.label_wrap === false
            ? field(
                'Trim labels after N characters',
                h('input', { type: 'number', min: 6, max: 60, value: a.label_max_chars, oninput: bind('label_max_chars', Number) })
              )
            : null,
          field('Rim thickness', h('input', { type: 'number', min: 0, max: 40, value: a.rim_width, oninput: bind('rim_width', Number) }))
        )
      ),
      h(
        'div.card',
        h('h2', 'Wedge shape'),
        h('p.card-hint', 'Turn a solid pie into separated segments or a ring.'),
        h(
          'div.grid.two',
          field(
            'Gap between wedges (degrees)',
            h('input', { type: 'number', min: 0, max: 20, step: 0.5, value: a.wedge_gap, oninput: bind('wedge_gap', Number) })
          ),
          field(
            'Centre hole (0 = solid pie)',
            h('input', { type: 'number', min: 0, max: 0.9, step: 0.05, value: a.inner_radius, oninput: bind('inner_radius', Number) })
          ),
          field(
            'Shading towards the centre',
            h('input', { type: 'number', min: 0, max: 1, step: 0.05, value: a.wedge_shading, oninput: bind('wedge_shading', Number) })
          ),
          field(
            'Wedge border width',
            h('input', { type: 'number', min: 0, max: 12, step: 0.5, value: a.slice_border_width, oninput: bind('slice_border_width', Number) })
          ),
          colorField('Wedge inline', a.slice_border_color, bind('slice_border_color'))
        )
      ),
      h(
        'div.card',
        h('h2', 'Label style'),
        h(
          'div.grid.two',
          switchField('Wrap long labels onto more lines', a.label_wrap !== false, (e) => {
            a.label_wrap = e.target.checked;
            changed();
            drawTab();
          }),
          switchField('Curve labels around the wheel', a.text_curved, bind('text_curved')),
          switchField('UPPERCASE labels', a.text_uppercase, bind('text_uppercase')),
          switchField('Label shadow', a.text_shadow, bind('text_shadow')),
          field(
            'Label position (1 = at the rim)',
            h('input', { type: 'number', min: 0.1, max: 1, step: 0.02, value: a.text_radial, oninput: bind('text_radial', Number) })
          ),
          field(
            'Outline width',
            h('input', { type: 'number', min: 0, max: 10, step: 0.5, value: a.text_stroke_width, oninput: bind('text_stroke_width', Number) })
          ),
          colorField('Outline colour', a.text_stroke_color || '#000000', bind('text_stroke_color'))
        )
      ),
      h(
        'div.card',
        h('h2', 'Hub and pointer'),
        h(
          'div.grid.two',
          switchField('Show the hub', a.show_hub, bind('show_hub')),
          switchField('Show the pointer', a.show_pointer, bind('show_pointer')),
          field(
            'Hub size',
            h('input', { type: 'number', min: 0.02, max: 0.9, step: 0.02, value: a.hub_radius, oninput: bind('hub_radius', Number) })
          ),
          field(
            'Pointer size',
            h('input', { type: 'number', min: 0.1, max: 4, step: 0.1, value: a.pointer_size, oninput: bind('pointer_size', Number) })
          )
        ),
        h('div.help', { style: { marginTop: '10px' } },
          'Both can be replaced with your own artwork on the Images tab.')
      ),
      h(
        'div.card',
        h('h2', 'Overlay behaviour'),
        h(
          'div.grid.two',
          switchField('Show the wheel name', a.show_title, bind('show_title')),
          switchField('Show the winner banner', a.show_result, bind('show_result')),
          switchField('Hide the wheel between spins', a.hide_when_idle, bind('hide_when_idle')),
          field('Winner banner duration (ms)', h('input', { type: 'number', min: 0, step: 500, value: a.result_duration_ms, oninput: bind('result_duration_ms', Number) })),
          field('Idle rotation speed', h('input', { type: 'number', min: 0, max: 10, step: 0.5, value: a.idle_spin_speed, oninput: bind('idle_spin_speed', Number) })),
          field(
            'Overlay size (px, 0 = fill the source)',
            h('input', { type: 'number', min: 0, max: 4000, step: 20, value: a.size, oninput: bind('size', Number) })
          ),
          field(
            'Winner banner position',
            h(
              'select',
              {
                value: a.result_position || 'under',
                onchange: (e) => {
                  a.result_position = e.target.value;
                  changed();
                  drawTab();
                },
              },
              h('option', { value: 'under' }, 'Underneath the wheel'),
              h('option', { value: 'over' }, 'On top of the wheel')
            ),
            'Underneath keeps the wheel at a fixed size. On top gives the wheel the full height.'
          )
        ),
        shadowCard()
      )
    );
  }

  /** The wheel's drop shadow. Sizes are px at a 600px wheel and scale with it. */
  function shadowCard() {
    const a = wheel.appearance;
    const on = a.shadow_enabled !== false;
    const number = (key, label, min, max, step) =>
      field(
        label,
        h('input', {
          type: 'number', min, max, step, value: a[key], disabled: !on,
          oninput: (e) => { a[key] = Number(e.target.value) || 0; changed(); },
        })
      );

    return h(
      'div',
      { style: { marginTop: '18px' } },
      h('h3', 'Drop shadow'),
      h(
        'div.help',
        { style: { marginBottom: '10px' } },
        'Sizes scale with the wheel, so they hold at any source size.'
      ),
      switchField('Cast a shadow', on, (e) => {
        a.shadow_enabled = e.target.checked;
        changed();
        drawTab();
      }),
      h(
        'div.grid.two',
        { style: { marginTop: '10px' } },
        number('shadow_blur', 'Softness', 0, 200, 1),
        number('shadow_offset_y', 'Vertical offset', -200, 200, 1),
        number('shadow_offset_x', 'Horizontal offset', -200, 200, 1),
        number('shadow_opacity', 'Opacity (0-1)', 0, 1, 0.05),
        colorField('Shadow colour', a.shadow_color, (e) => {
          a.shadow_color = e.target.value;
          changed();
        })
      )
    );
  }

  /* ---------------------------------------------------------------- images */

  function imagesTab() {
    const a = wheel.appearance;
    for (const key of ['background_image', 'hub_image', 'frame_image', 'pointer_image']) {
      if (!a[key]) a[key] = blankImage();
    }

    const card = (title, hint, key) =>
      h(
        'div.card',
        h('h2', title),
        h('p.card-hint', hint),
        imageLayerControl({ layer: a[key], onChange: () => changed(), hint })
      );

    return h(
      'div',
      h(
        'div.card',
        h('h2', 'Per-slice images'),
        h(
          'p.card-hint',
          'Set on the Slices tab. Pick one below to jump there. '
            + 'Good for game covers, emotes or faces.'
        ),
        h(
          'div.row.wrap',
          wheel.slices.length
            ? wheel.slices.map((slice) =>
                h(
                  'button.slice-chip',
                  {
                    type: 'button',
                    title: `Edit ${slice.label}`,
                    onclick: () => {
                      activeTab = 'slices';
                      sessionStorage.setItem('wheelhat.tab', 'slices');
                      expandedSlices.add(slice.id);
                      expandedImages.add(slice.id);
                      for (const button of tabs.children) button.classList.remove('active');
                      tabs.children[0].classList.add('active');
                      drawTab();
                    },
                  },
                  slice.image?.url
                    ? h('img', { src: slice.image.url, alt: '' })
                    : h('span.slice-chip-dot', { style: { background: slice.color || '#555' } }),
                  h('span', slice.label || 'Untitled')
                )
              )
            : h('span.muted', 'Add a slice first.')
        )
      ),
      h(
        'div',
        card(
          'Overlay / frame',
          'Drawn on top of the wheel and does not spin with it. Use a transparent PNG for a bezel, glass, glow or border.',
          'frame_image'
        ),
        h(
          'div',
          { style: { margin: '-6px 0 18px' } },
          switchField('Fit to the whole browser source', a.frame_fills_source === true, (e) => {
            a.frame_fills_source = e.target.checked;
            changed();
          }),
          h(
            'div.help',
            'On for artwork that spans the whole source. Off keeps it square around the wheel.'
          )
        )
      ),
      h(
        'div',
        card('Background', 'Sits behind the wheel.', 'background_image'),
        h(
          'div',
          { style: { margin: '-6px 0 18px' } },
          field(
            'Fit',
            h(
              'select',
              {
                value: a.background_fit || 'cover',
                onchange: (e) => {
                  a.background_fit = e.target.value;
                  changed();
                },
              },
              h('option', { value: 'cover' }, 'Fill the source (crops the edges)'),
              h('option', { value: 'contain' }, 'Show all of it')
            ),
            'Fill suits a photo. Artwork with a shape loses its edges that way; '
              + 'show all of it, and set the source to the size recommended beside '
              + 'the browser source URL so it fits exactly.'
          )
        )
      ),
      card('Centre / hub', 'Clipped to a circle in the middle of the wheel.', 'hub_image'),
      card('Pointer', 'Replaces the drawn triangle at the top.', 'pointer_image')
    );
  }

  /* ------------------------------------------------------------------ spin */

  function spinTab() {
    const s = wheel.spin;
    const bind = (key, transform = Number) => (e) => {
      s[key] = transform(e.target.value);
      changed({ preview: false });
    };

    return h(
      'div',
      h(
        'div.card',
        h('h2', 'Timing'),
        h('p.card-hint', 'Actions fire once the wheel has stopped, plus the delay below.'),
        h(
          'div.grid.two',
          field('Spin length (ms)', h('input', { type: 'number', min: 500, step: 250, value: s.duration_ms, oninput: bind('duration_ms') })),
          field('Delay before actions fire (ms)', h('input', { type: 'number', min: 0, step: 100, value: s.action_delay_ms, oninput: bind('action_delay_ms') })),
          field('Minimum full turns', h('input', { type: 'number', min: 1, max: 30, value: s.min_turns, oninput: bind('min_turns') })),
          field('Maximum full turns', h('input', { type: 'number', min: 1, max: 40, value: s.max_turns, oninput: bind('max_turns') })),
          field(
            'Slowdown curve',
            h(
              'select',
              { onchange: bind('easing', String) },
              EASINGS.map((option) => h('option', { value: option.value, selected: s.easing === option.value }, option.label))
            )
          ),
          field(
            'Cooldown between spins (seconds)',
            h('input', { type: 'number', min: 0, value: s.cooldown_seconds, oninput: bind('cooldown_seconds') }),
            'Applies to triggered spins. Spinning from the control panel always ignores it.'
          )
        )
      )
    );
  }

  /* ---------------------------------------------------------------- chains */

  function chainsTab() {
    return h(
      'div',
      h(
        'div.card',
        h('h2', 'Before every spin'),
        h('p.card-hint', 'Runs the moment the wheel starts turning, whichever slice ends up winning.'),
        renderActionList({
          actions: wheel.pre_actions,
          schemas,
          wheelId,
          onChange: () => changed({ preview: false }),
          context: { triggers: wheel.triggers },
          emptyHint: 'Nothing runs before a spin. A good spot for a drumroll sound or an OBS scene change.',
        })
      ),
      h(
        'div.card',
        h('h2', 'After every spin'),
        h('p.card-hint', 'Runs after the winning slice’s own actions have finished.'),
        renderActionList({
          actions: wheel.post_actions,
          schemas,
          wheelId,
          onChange: () => changed({ preview: false }),
          context: { triggers: wheel.triggers },
          emptyHint: 'Nothing runs after a spin. Useful for announcing the result in chat once per spin.',
        })
      )
    );
  }

  /* ------------------------------------------------------------------ mount */

  /**
   * Width, height and the recommended size, next to the URL they describe.
   *
   * This used to sit inside "Overlay behaviour" on the Look tab, two tabs from
   * the browser source URL: two sections named for the same OBS object, with a
   * round trip in the middle of setting one up. It redraws itself rather than
   * the tab, because it no longer lives on one.
   */
  function sourceSizeBlock() {
    const box = h('div', { style: { marginTop: '14px' } });
    const render = () => {
      const a = wheel.appearance;
      const best = recommendedSource(a);
      const auto = a.source_auto !== false;

      // While automatic, the size follows the content: add a background that
      // reaches past the wheel and the recommendation grows to fit it. Typing a
      // size takes it off automatic, and from then on the numbers are theirs.
      if (auto && (a.source_width !== best.width || a.source_height !== best.height)) {
        a.source_width = best.width;
        a.source_height = best.height;
        changed();
      }
      const matches = a.source_width === best.width && a.source_height === best.height;

      const number = (key, label) =>
        h(
          'div.field',
          h('label', label),
          h('input', {
            type: 'number', min: 160, max: 7680, step: 10, value: a[key],
            oninput: (e) => {
              a[key] = Number(e.target.value) || 0;
              a.source_auto = false;
              changed();
            },
            onchange: render,
          })
        );

      clear(box);
      box.append(
        h('div.grid.two', number('source_width', 'Width'), number('source_height', 'Height')),
        h(
          'div.row',
          { style: { marginTop: '10px' } },
          auto
            ? h('span.help', 'Following the wheel and its artwork.')
            : h(
                'button.btn.small',
                {
                  disabled: matches,
                  title: 'Fits the wheel, the title, the banner and any artwork with nothing cropped',
                  onclick: () => {
                    a.source_auto = true;
                    changed();
                    render();
                  },
                },
                matches ? `Recommended (${best.width} x ${best.height})` : `Use ${best.width} x ${best.height}`
              )
        )
      );
    };
    render();
    // A background's proportions are unknown until it has loaded, so the first
    // render cannot account for it. Redraw when one settles.
    const stopWatching = onImageSettled(() => {
      if (box.isConnected) render();
      else stopWatching();
    });
    return box;
  }

  const overlayCard = h(
    'div.card',
    h('h2', 'Browser source'),
    h('p.card-hint', 'Add this URL as a Browser source in OBS at the size below, and untick "Shutdown source when not visible" so it stays connected.'),
    h(
      'div.overlay-url',
      h('code', overlayUrl),
      h('button.btn.small.icon.ghost', { title: 'Copy', onclick: () => copyText(overlayUrl) }, '⧉')
    ),
    h(
      'div.row',
      { style: { marginTop: '10px' } },
      h('a.btn.small', { href: overlayUrl, target: '_blank', rel: 'noreferrer' }, 'Open in a tab'),
      h(
        'button.btn.small.ghost',
        { onclick: guard(async () => {
          const result = await api.post(`/wheels/${wheelId}/refresh-overlays`);
          toast(`Refreshed ${plural(result.clients, 'connected source')}`, 'ok');
        }) },
        'Push update to sources'
      ),
      h('span.grow'),
      h('span.pill', { id: 'overlayCount' }, `${wheel.overlay_clients || 0} connected`)
    ),
    sourceSizeBlock()
  );

  // An integration hook, shown above the fold to everyone before they have made
  // a slice. Worth keeping, not worth leading with.
  const triggerCard = h(
    'details.card',
    h('summary', 'Spin from another app'),
    h(
      'p.card-hint',
      'Open this URL from anything that can make a web request: a Stream Deck button, Touch Portal, Streamer.bot’s Fetch URL.'
    ),
    h(
      'div.overlay-url',
      h('code', wheel.trigger_url || ''),
      h('button.btn.small.icon.ghost', { title: 'Copy', onclick: () => copyText(wheel.trigger_url || '') }, '⧉')
    ),
    h('div.help', { style: { marginTop: '8px' } }, 'Add ?user=SomeName to fill in {{user}} for your actions.')
  );

  const page = h(
    'div.page.page-wide',
    head,
    h(
      'div.editor',
      h('div', tabs, tabBody),
      h('div.editor-side', previewBox, overlayCard, triggerCard)
    )
  );

  clear(main).appendChild(page);
  drawTab();
  // Size the canvas backing store now rather than on the next frame.
  // getBoundingClientRect forces layout, so this gives real dimensions before
  // the first paint - and unlike requestAnimationFrame or ResizeObserver it
  // still runs when the panel is in a background tab.
  renderer.resize();
  updatePreview();

  const unsubscribe = subscribe((state, reason) => {
    if (reason === 'overlays') {
      const badge = $('#overlayCount', page);
      if (badge) badge.textContent = `${state.overlayCounts[wheelId] || 0} connected`;
    }
  });

  return () => {
    save.flush();
    unsubscribe();
    resizeObserver.disconnect();
    renderer.destroy();
  };
}

/* ------------------------------------------------------------------ helpers */

function field(label, control, help) {
  return h('div.field', h('label', label), control, help ? h('div.help', help) : null);
}

/**
 * A colour that is allowed to be unset.
 *
 * Slice text falls back to automatic contrast against the wedge, and the slice
 * inline falls back to the wheel's own colour, so the control has to express
 * "not set" as well as a colour - which a bare <input type="color"> cannot do.
 */
function optionalColorField(label, value, fallback, onChange, help) {
  const swatch = h('input', {
    type: 'color',
    value: value || fallback || '#ffffff',
    disabled: !value,
    oninput: (e) => onChange(e.target.value),
  });
  const auto = h('input', {
    type: 'checkbox',
    checked: !value,
    onchange: (e) => {
      swatch.disabled = e.target.checked;
      onChange(e.target.checked ? null : swatch.value);
    },
  });
  return h(
    'div.field',
    h('label', label),
    h('div.row', h('label.switch', auto, h('span', 'Auto')), swatch),
    help ? h('div.help', help) : null
  );
}

function colorField(label, value, onInput) {
  return h(
    'div.field',
    h('label', label),
    h('div.row', h('input', { type: 'color', value: value || '#ffffff', oninput: onInput }), h('span.mono.faint', value))
  );
}

function switchField(label, checked, onChange) {
  return h('label.switch', h('input', { type: 'checkbox', checked, onchange: onChange }), h('span', label));
}
