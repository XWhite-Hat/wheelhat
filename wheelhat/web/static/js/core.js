/** Small DOM + fetch helpers shared by every view. No framework, no build step. */

/** Hyperscript. `h('div.card', {onclick}, 'text', child)` */
export function h(spec, props = null, ...children) {
  const [tagPart, ...classes] = String(spec).split('.');
  const el = document.createElement(tagPart || 'div');
  if (classes.length) el.className = classes.join(' ');

  if (props && (props.nodeType || Array.isArray(props) || typeof props !== 'object')) {
    children.unshift(props);
    props = null;
  }

  // `value` is applied after the children, not with the other props. Setting
  // it on a <select> that has no <option>s yet does nothing - the browser has
  // nothing to match - and the select then falls back to its first option. The
  // control shows the wrong thing, saves correctly when changed, and cannot be
  // changed back, because re-picking what it already displays fires no event.
  let deferredValue;

  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'value') deferredValue = value;
    else if (key === 'class') el.className = `${el.className} ${value}`.trim();
    else if (key === 'style' && typeof value === 'object') Object.assign(el.style, value);
    else if (key === 'dataset') Object.assign(el.dataset, value);
    else if (key.startsWith('on') && typeof value === 'function') {
      el.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === 'html') el.innerHTML = value;
    else if (key in el && key !== 'list') el[key] = value;
    else el.setAttribute(key, value === true ? '' : value);
  }

  append(el, children);
  if (deferredValue !== undefined) el.value = deferredValue;
  return el;
}

function append(parent, children) {
  for (const child of children.flat(4)) {
    if (child === null || child === undefined || child === false) continue;
    parent.appendChild(child.nodeType ? child : document.createTextNode(String(child)));
  }
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

/* ------------------------------------------------------------------- format */

export function timeAgo(ts) {
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (seconds < 45) return 'just now';
  if (seconds < 90) return 'a minute ago';
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return `${Math.round(seconds / 86400)} d ago`;
}

export function clockTime(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

export function debounce(fn, ms = 400) {
  let handle;
  const wrapped = (...args) => {
    clearTimeout(handle);
    handle = setTimeout(() => fn(...args), ms);
  };
  wrapped.flush = (...args) => {
    clearTimeout(handle);
    fn(...args);
  };
  wrapped.cancel = () => clearTimeout(handle);
  return wrapped;
}

export function uid(prefix = 'id_') {
  return prefix + Math.random().toString(36).slice(2, 12);
}

/* -------------------------------------------------------------------- toast */

export function toast(message, kind = 'info', ms = 4200) {
  const host = document.getElementById('toasts');
  if (!host) return;
  const node = h(`div.toast.${kind}`, message);
  host.appendChild(node);
  setTimeout(() => {
    node.style.transition = 'opacity 220ms ease, transform 220ms ease';
    node.style.opacity = '0';
    node.style.transform = 'translateX(16px)';
    setTimeout(() => node.remove(), 240);
  }, ms);
}

/* ---------------------------------------------------------------------- api */

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function request(method, path, body) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }

  let response;
  try {
    response = await fetch(`/api${path}`, options);
  } catch (err) {
    throw new ApiError('WheelHat server is not responding. Is it still running?', 0);
  }

  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { detail: text };
    }
  }

  if (!response.ok) {
    const detail = payload?.detail ?? payload?.message ?? `HTTP ${response.status}`;
    throw new ApiError(typeof detail === 'string' ? detail : JSON.stringify(detail), response.status);
  }
  return payload;
}

export const api = {
  get: (path) => request('GET', path),
  post: (path, body) => request('POST', path, body ?? {}),
  put: (path, body) => request('PUT', path, body),
  patch: (path, body) => request('PATCH', path, body),
  del: (path) => request('DELETE', path),
};

/** Wrap a handler so thrown ApiErrors surface as a toast instead of a dead click. */
export function guard(fn) {
  return async (...args) => {
    try {
      return await fn(...args);
    } catch (err) {
      toast(err.message || String(err), 'bad', 6000);
      return undefined;
    }
  };
}

/* -------------------------------------------------------------------- modal */

export function modal({ title, body, confirmLabel = 'Save', onConfirm, wide = false, hideConfirm = false }) {
  let backdrop;
  const close = () => backdrop.remove();

  const confirm = h(
    'button.btn.primary',
    {
      onclick: async () => {
        const result = onConfirm ? await onConfirm() : true;
        if (result !== false) close();
      },
    },
    confirmLabel
  );

  backdrop = h(
    'div.modal-backdrop',
    {
      onclick: (event) => {
        if (event.target === backdrop) close();
      },
    },
    h(
      'div.modal',
      { style: wide ? { width: 'min(880px, 100%)' } : null },
      h('header', title),
      h('div.modal-body', body),
      h(
        'footer',
        h('button.btn.ghost', { onclick: close }, hideConfirm ? 'Close' : 'Cancel'),
        hideConfirm ? null : confirm
      )
    )
  );

  const onKey = (event) => {
    if (event.key === 'Escape') {
      close();
      document.removeEventListener('keydown', onKey);
    }
  };
  document.addEventListener('keydown', onKey);
  document.body.appendChild(backdrop);
  return { close, element: backdrop };
}

/**
 * "1 wheel", "2 wheels". Three places used to bracket a trailing s and one did
 * it properly, which is three too many ways to say the same thing.
 */
/** Where an element sits under `root`, as child indices. */
function positionOf(root, node) {
  const path = [];
  let current = node;
  while (current && current !== root) {
    const parent = current.parentElement;
    if (!parent) return null;
    path.unshift([...parent.children].indexOf(current));
    current = parent;
  }
  return current === root ? path : null;
}

function nodeAt(root, path) {
  let current = root;
  for (const index of path) {
    current = current && current.children ? current.children[index] : null;
    if (!current) return null;
  }
  return current;
}

/**
 * Rebuild part of the page without throwing away where the user was.
 *
 * `.main` is the scroll container. Emptying and refilling it collapses its
 * height, which clamps scrollTop to zero, so any redraw - changing a dropdown,
 * or a status arriving over the socket while someone is reading - snapped them
 * back to the top of whatever they were part-way through.
 *
 * It also destroys whatever had focus. On a page that redraws when the socket
 * says something, that can happen mid-sentence: the field is rebuilt from
 * stored state, so the caret and anything typed but not yet saved go with it.
 *
 * A redraw usually rebuilds the same shape with different values, so the
 * focused element is found again by its position in the tree. If the shape did
 * change, or something else now sits there, nothing is focused rather than the
 * wrong thing.
 */
export function preserveView(rebuild) {
  const scroller = document.querySelector('.main');
  const top = scroller ? scroller.scrollTop : 0;

  const active = document.activeElement;
  const tracking = Boolean(scroller && active && active !== document.body && scroller.contains(active));
  const path = tracking ? positionOf(scroller, active) : null;
  const tag = tracking ? active.tagName : '';
  const type = tracking ? active.getAttribute('type') : '';
  let caret = null;
  if (path) {
    // Only text-ish inputs have a selection; asking a number or colour input
    // for one throws.
    try {
      caret = [active.selectionStart, active.selectionEnd];
    } catch {
      caret = null;
    }
  }

  rebuild();

  if (scroller && top) scroller.scrollTop = top;
  if (!path) return;

  const restored = nodeAt(scroller, path);
  if (!restored || restored.tagName !== tag || restored.getAttribute('type') !== type) return;
  if (typeof restored.focus !== 'function') return;

  // preventScroll, or focusing undoes the scroll position just restored.
  restored.focus({ preventScroll: true });
  if (caret && caret[0] !== null && typeof restored.setSelectionRange === 'function') {
    try {
      restored.setSelectionRange(caret[0], caret[1]);
    } catch {
      // Some input types refuse a selection. Focus alone is enough.
    }
  }
}

export function plural(count, one, many = `${one}s`) {
  return `${count} ${count === 1 ? one : many}`;
}

export function confirmDialog(message, { confirmLabel = 'Delete', danger = true, detail = '' } = {}) {
  return new Promise((resolve) => {
    const dialog = modal({
      // The question itself, rather than "Are you sure?" over the top of it.
      title: message,
      body: detail ? h('p', { style: { margin: 0 } }, detail) : null,
      confirmLabel,
      onConfirm: () => {
        resolve(true);
        return true;
      },
    });
    if (danger) {
      const button = dialog.element.querySelector('footer .btn.primary');
      button.classList.remove('primary');
      button.classList.add('danger');
    }
    dialog.element.addEventListener('remove', () => resolve(false));
    // Cancel / backdrop / Escape all fall through to false.
    const observer = new MutationObserver(() => {
      if (!document.body.contains(dialog.element)) {
        observer.disconnect();
        resolve(false);
      }
    });
    observer.observe(document.body, { childList: true });
  });
}

/** Copy text and confirm it, falling back for non-secure origins. */
export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const field = h('textarea', { value: text, style: { position: 'fixed', opacity: '0' } });
    document.body.appendChild(field);
    field.select();
    document.execCommand('copy');
    field.remove();
  }
  toast('Copied to clipboard', 'ok', 1800);
}
