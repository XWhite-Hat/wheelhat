/**
 * Editor control for one image layer.
 *
 * Used for the four wheel-level layers (background, frame, hub, pointer) and
 * for each slice's own image. Every transform is live: the preview redraws as
 * the sliders move, because guessing at numbers is miserable.
 */

import { h } from './core.js';
import { pickAsset } from './assets.js';
import { forgetImage, imageFailed } from './image-cache.js';

const DEFAULTS = {
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

function slider(label, layer, key, { min, max, step, suffix = '', onChange }) {
  const value = h('span.slider-value');
  const paint = () => {
    const raw = layer[key] ?? DEFAULTS[key];
    value.textContent = `${Number(raw).toFixed(step < 0.1 ? 2 : 1)}${suffix}`;
  };
  const input = h('input', {
    type: 'range',
    min,
    max,
    step,
    value: layer[key] ?? DEFAULTS[key],
    oninput: (e) => {
      layer[key] = Number(e.target.value);
      paint();
      onChange();
    },
  });
  paint();
  return h(
    'div.field',
    h('div.row', h('label.grow', label), value),
    input,
    h(
      'button.btn.small.ghost.reset-slider',
      {
        type: 'button',
        title: 'Reset',
        onclick: () => {
          layer[key] = DEFAULTS[key];
          input.value = DEFAULTS[key];
          paint();
          onChange();
        },
      },
      'reset'
    )
  );
}

function toggle(label, layer, key, onChange, { invert = false } = {}) {
  return h(
    'label.switch',
    h('input', {
      type: 'checkbox',
      checked: invert ? !(layer[key] ?? DEFAULTS[key]) : (layer[key] ?? DEFAULTS[key]),
      onchange: (e) => {
        layer[key] = invert ? !e.target.checked : e.target.checked;
        onChange();
      },
    }),
    h('span', label)
  );
}

/**
 * @param {object} options
 * @param {object} options.layer  mutated in place
 * @param {Function} options.onChange
 * @param {boolean} options.slice  show the per-wedge placement controls
 * @param {string} options.hint
 */
export function imageLayerControl({ layer, onChange, slice = false, hint = '' }) {
  const root = h('div.image-layer');
  const preview = h('div.image-preview');
  const controls = h('div.image-controls');

  const redraw = () => {
    // Rebuild the whole control so empty/filled states stay in step.
    render();
    onChange();
  };

  function render() {
    const has = Boolean(layer.url);

    const thumb = has
      ? h('img', { src: layer.url, alt: '', onerror: () => thumb.classList.add('broken') })
      : h('div.image-empty', 'No image');

    const chooseButton = h(
      'button.btn.small',
      {
        type: 'button',
        onclick: async () => {
          const chosen = await pickAsset({ kind: 'image', current: layer.url });
          if (chosen === null) return; // cancelled
          layer.url = chosen;
          if (chosen) forgetImage(chosen);
          redraw();
        },
      },
      has ? 'Change' : 'Choose image'
    );

    const clearButton = has
      ? h(
          'button.btn.small.ghost',
          {
            type: 'button',
            onclick: () => {
              layer.url = '';
              redraw();
            },
          },
          'Remove'
        )
      : null;

    // replaceChildren renders a null argument as the text "null", so drop the
    // empty slots before handing them over.
    preview.replaceChildren(
      ...[
        thumb,
        h('div.image-actions', chooseButton, clearButton),
        has && imageFailed(layer.url)
          ? h('div.image-warning', 'That image could not be loaded.')
          : null,
        has ? h('code.image-url', layer.url) : null,
      ].filter(Boolean)
    );

    controls.replaceChildren();
    if (!has) {
      if (hint) controls.appendChild(h('div.help', hint));
      return;
    }

    controls.appendChild(
      h(
        'div.row.wrap',
        { style: { marginBottom: '6px' } },
        toggle('Visible', layer, 'enabled', onChange),
        slice ? toggle('Turn with the wheel', layer, 'rotate_with_wheel', onChange) : null,
        slice ? toggle('Keep inside the slice', layer, 'clip_to_slice', onChange) : null,
        slice ? toggle('Hide the label', layer, 'replace_label', onChange) : null
      )
    );

    const grid = h('div.grid.two');
    if (slice) {
      grid.append(
        slider('Size', layer, 'size', { min: 0.04, max: 1.2, step: 0.01, onChange }),
        slider('Distance from centre', layer, 'radial', { min: 0, max: 1.1, step: 0.01, onChange })
      );
    }
    grid.append(
      slider('Scale', layer, 'scale', { min: 0.1, max: 3, step: 0.01, onChange }),
      slider('Opacity', layer, 'opacity', { min: 0, max: 1, step: 0.01, onChange }),
      slider('Nudge across', layer, 'offset_x', { min: -1, max: 1, step: 0.01, onChange }),
      slider('Nudge down', layer, 'offset_y', { min: -1, max: 1, step: 0.01, onChange }),
      slider('Rotate', layer, 'rotation', { min: -180, max: 180, step: 1, suffix: '°', onChange })
    );
    controls.appendChild(grid);
  }

  render();
  root.append(preview, controls);
  return root;
}
