/**
 * The image and sound library: browse, upload, pick.
 *
 * Anything a wheel points at is just a URL, so the picker also accepts a plain
 * address for people who host their overlays' art elsewhere.
 */

import { api, clear, confirmDialog, h, modal, toast } from './core.js';
import { forgetImage } from './image-cache.js';

let cached = null;

export async function listAssets({ force = false } = {}) {
  if (!cached || force) cached = await api.get('/assets');
  return cached;
}

export function invalidateAssets() {
  cached = null;
}

async function uploadFiles(files, onDone) {
  let uploaded = 0;
  for (const file of files) {
    const form = new FormData();
    form.append('file', file);
    try {
      const response = await fetch('/api/assets', { method: 'POST', body: form });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        toast(payload.detail || `Could not upload ${file.name}`, 'bad', 7000);
        continue;
      }
      uploaded += 1;
    } catch (err) {
      toast(`Could not upload ${file.name}: ${err.message}`, 'bad', 7000);
    }
  }
  if (uploaded) {
    invalidateAssets();
    toast(`Added ${uploaded} file${uploaded === 1 ? '' : 's'}`, 'ok');
    await onDone();
  }
}

/**
 * Open the library and resolve with the chosen URL, or null if cancelled.
 * @param {object} options
 * @param {'image'|'sound'} options.kind
 * @param {string} options.current
 */
export function pickAsset({ kind = 'image', current = '' } = {}) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };

    const grid = h('div.asset-grid');
    const urlInput = h('input', {
      type: 'text',
      value: current && !current.startsWith('/assets/') ? current : '',
      placeholder: 'https://example.com/image.png',
    });

    const fileInput = h('input', {
      type: 'file',
      accept: kind === 'sound' ? 'audio/*' : 'image/*',
      multiple: true,
      hidden: true,
      onchange: async () => {
        await uploadFiles([...fileInput.files], draw);
        fileInput.value = '';
      },
    });

    async function draw() {
      clear(grid).appendChild(h('div.muted', 'Loading…'));
      let data;
      try {
        data = await listAssets({ force: true });
      } catch (err) {
        clear(grid).appendChild(h('div.test-result.bad', err.message));
        return;
      }

      const items = data.assets.filter((a) => a.kind === kind);
      clear(grid);

      if (kind === 'image') {
        grid.appendChild(
          h(
            'button.asset-tile.asset-none',
            {
              type: 'button',
              title: 'Use no image',
              onclick: () => {
                dialog.close();
                finish('');
              },
            },
            h('span', '⃠'),
            h('small', 'None')
          )
        );
      }

      if (!items.length) {
        grid.appendChild(
          h('div.muted', { style: { gridColumn: '1 / -1', padding: '10px 0' } },
            `No ${kind}s yet. Upload one, or drop files onto this window.`)
        );
      }

      for (const asset of items) {
        const tile = h(
          'button.asset-tile',
          {
            type: 'button',
            class: asset.url === current ? 'selected' : '',
            title: `${asset.name} · ${Math.round(asset.bytes / 1024)} KB`,
            onclick: () => {
              dialog.close();
              finish(asset.url);
            },
          },
          kind === 'image'
            ? h('img', { src: asset.url, alt: '', loading: 'lazy' })
            : h('span', '♪'),
          h('small', asset.name)
        );
        tile.appendChild(
          h(
            'span.asset-delete',
            {
              title: 'Delete this file',
              onclick: async (event) => {
                event.stopPropagation();
                if (!(await confirmDialog(`Delete "${asset.name}"?`, {
                  detail: 'Any wheel using it will show nothing.',
                }))) return;
                await api.del(`/assets/${encodeURIComponent(asset.name)}`);
                forgetImage(asset.url);
                invalidateAssets();
                toast('Deleted', 'ok');
                draw();
              },
            },
            '✕'
          )
        );
        grid.appendChild(tile);
      }
    }

    const dropZone = h(
      'div.asset-drop',
      {
        ondragover: (e) => {
          e.preventDefault();
          dropZone.classList.add('over');
        },
        ondragleave: () => dropZone.classList.remove('over'),
        ondrop: async (e) => {
          e.preventDefault();
          dropZone.classList.remove('over');
          await uploadFiles([...e.dataTransfer.files], draw);
        },
      },
      h('button.btn.small', { type: 'button', onclick: () => fileInput.click() }, 'Choose files'),
      h('span.muted', ' or drop them here')
    );

    const body = h(
      'div',
      dropZone,
      fileInput,
      grid,
      h(
        'div.field',
        { style: { marginTop: '16px' } },
        h('label', '…or use a URL'),
        h(
          'div.row',
          urlInput,
          h(
            'button.btn.small',
            {
              type: 'button',
              onclick: () => {
                const value = urlInput.value.trim();
                if (!value) return;
                dialog.close();
                finish(value);
              },
            },
            'Use'
          )
        )
      )
    );

    const dialog = modal({
      title: kind === 'sound' ? 'Choose a sound' : 'Choose an image',
      body,
      hideConfirm: true,
      wide: true,
    });

    // Cancelling the dialog resolves with null so callers can leave things alone.
    const observer = new MutationObserver(() => {
      if (!document.body.contains(dialog.element)) {
        observer.disconnect();
        finish(null);
      }
    });
    observer.observe(document.body, { childList: true });

    draw();
  });
}
