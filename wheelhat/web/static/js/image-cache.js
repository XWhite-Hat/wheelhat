/**
 * Shared image loader for the wheel renderer.
 *
 * Canvas drawing is synchronous, so every draw needs an already-decoded image.
 * Requests are cached by URL and a redraw is requested once a load finishes;
 * a failed image is remembered as failed so a broken URL cannot retry on
 * every single frame.
 */

const cache = new Map(); // url -> { image, state: 'loading' | 'ready' | 'failed' }
const listeners = new Set();

/** Called with the URL whenever an image becomes ready (or fails). */
export function onImageSettled(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function settle(url) {
  for (const fn of listeners) {
    try {
      fn(url);
    } catch (err) {
      console.error('[wheelhat] image listener failed', err);
    }
  }
}

/**
 * @returns {HTMLImageElement|null} the decoded image, or null while it loads
 *   or if it will never load.
 */
export function getImage(url) {
  if (!url) return null;
  const hit = cache.get(url);
  if (hit) return hit.state === 'ready' ? hit.image : null;

  const image = new Image();
  const entry = { image, state: 'loading' };
  cache.set(url, entry);

  // Same-origin /assets needs no CORS dance; anything else might, and a
  // tainted canvas would break getImageData in the editor preview.
  if (!url.startsWith('/') && !url.startsWith('data:')) image.crossOrigin = 'anonymous';

  image.onload = () => {
    entry.state = 'ready';
    settle(url);
  };
  image.onerror = () => {
    entry.state = 'failed';
    settle(url);
  };
  image.src = url;
  return null;
}

/** True when the URL was tried and will not load - used to warn in the editor. */
export function imageFailed(url) {
  return cache.get(url)?.state === 'failed';
}

/** Forget one URL (or all), so a replaced asset is picked up again. */
export function forgetImage(url) {
  if (url) cache.delete(url);
  else cache.clear();
}

/**
 * Fit a source image into a box, preserving aspect ratio.
 * @returns {{width: number, height: number}}
 */
export function containSize(image, maxWidth, maxHeight) {
  const natural = (image.naturalWidth || 1) / (image.naturalHeight || 1);
  let width = maxWidth;
  let height = width / natural;
  if (height > maxHeight) {
    height = maxHeight;
    width = height * natural;
  }
  return { width, height };
}

/** Fill a box completely, preserving aspect ratio (edges may overflow). */
export function coverSize(image, minWidth, minHeight) {
  const natural = (image.naturalWidth || 1) / (image.naturalHeight || 1);
  let width = minWidth;
  let height = width / natural;
  if (height < minHeight) {
    height = minHeight;
    width = height * natural;
  }
  return { width, height };
}
