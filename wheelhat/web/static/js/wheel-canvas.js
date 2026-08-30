/**
 * Canvas wheel renderer, shared by the overlay and the editor preview.
 *
 * The server decides which slice wins; this class only animates towards the
 * index it is given. Landing jitter is derived from the spin id rather than
 * Math.random so two browser sources showing the same wheel stay pixel-identical.
 */

import { containSize, coverSize, getImage, onImageSettled } from './image-cache.js';

const TAU = Math.PI * 2;
const POINTER_ANGLE = -Math.PI / 2; // 12 o'clock

const EASINGS = {
  easeOutQuint: (t) => 1 - Math.pow(1 - t, 5),
  easeOutCubic: (t) => 1 - Math.pow(1 - t, 3),
  easeOutExpo: (t) => (t >= 1 ? 1 : 1 - Math.pow(2, -10 * t)),
};

const DEFAULT_PALETTE = [
  '#e5484d', '#f76b15', '#ffb224', '#46a758',
  '#12a594', '#0091ff', '#8e4ec6', '#e93d82',
];

const EMPTY_LAYER = { url: '', enabled: true, scale: 1, offset_x: 0, offset_y: 0, rotation: 0, opacity: 1 };

const DEFAULT_APPEARANCE = {
  palette: DEFAULT_PALETTE,
  text_color: '#ffffff',
  rim_color: '#111318',
  rim_width: 10,
  pointer_color: '#ffffff',
  hub_color: '#16181d',
  hub_label: '',
  font_family: 'Inter, Segoe UI, system-ui, sans-serif',
  font_size: 20,
  font_weight: 700,
  label_max_chars: 22,
  idle_spin_speed: 0,

  wedge_gap: 0,
  inner_radius: 0,
  slice_border_color: '#00000030',
  slice_border_width: 1,
  wedge_shading: 0,

  text_radial: 0.94,
  text_stroke_color: '',
  text_stroke_width: 0,
  text_shadow: true,
  text_uppercase: false,
  text_curved: false,

  show_hub: true,
  hub_radius: 0.14,
  show_pointer: true,
  pointer_size: 1,

  background_image: EMPTY_LAYER,
  hub_image: EMPTY_LAYER,
  frame_image: EMPTY_LAYER,
  pointer_image: EMPTY_LAYER,
};

/** A layer is worth drawing only when it has a URL, is on, and is visible. */
function layerActive(layer) {
  return Boolean(layer && layer.url && layer.enabled !== false && (layer.opacity ?? 1) > 0);
}

/** Clamp into a range; the editor lets people type anything. */
function clamp(value, min, max) {
  const number = Number(value);
  if (!Number.isFinite(number)) return min;
  return Math.min(max, Math.max(min, number));
}

/** Lighten (amount > 0) or darken (amount < 0) a hex colour. */
function shade(hex, amount) {
  const value = String(hex || '').replace('#', '');
  if (value.length < 6) return hex;
  const mix = (channel) => {
    const base = parseInt(value.slice(channel * 2, channel * 2 + 2), 16);
    const target = amount < 0 ? 0 : 255;
    const shifted = Math.round(base + (target - base) * Math.abs(amount));
    return Math.min(255, Math.max(0, shifted)).toString(16).padStart(2, '0');
  };
  return `#${mix(0)}${mix(1)}${mix(2)}`;
}

/** Stable [0, 1) value from a string, so every client jitters identically. */
export function hashUnit(text) {
  let hash = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return ((hash >>> 0) % 100000) / 100000;
}

/** Readable text colour for a given wedge fill. */
export function contrastColor(hex) {
  const value = String(hex || '').replace('#', '');
  if (value.length < 6) return '#ffffff';
  const r = parseInt(value.slice(0, 2), 16);
  const g = parseInt(value.slice(2, 4), 16);
  const b = parseInt(value.slice(4, 6), 16);
  // Rec. 709 luma; the 0.62 cut-off keeps light yellows on dark text.
  const luma = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
  return luma > 0.62 ? '#14161a' : '#ffffff';
}

export class WheelRenderer {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.slices = [];
    this.appearance = { ...DEFAULT_APPEARANCE };
    this.rotation = 0;
    this.spinning = false;
    this.highlightIndex = -1;
    this._frame = null;
    this._lastTick = 0;
    this._resolveSpin = null;
    this._dpr = window.devicePixelRatio || 1;
    // An image that finishes loading after a draw needs the frame repainting.
    this._stopWatchingImages = onImageSettled(() => this.draw());
    this.resize();
  }

  setState({ slices, appearance }) {
    if (Array.isArray(slices)) this.slices = slices;
    if (appearance) this.appearance = { ...DEFAULT_APPEARANCE, ...appearance };
    if (!this.spinning) this.highlightIndex = -1;
    this.draw();
    this._ensureLoop();
  }

  resize() {
    const rect = this.canvas.getBoundingClientRect();
    const size = Math.max(1, Math.min(rect.width, rect.height));
    this._dpr = window.devicePixelRatio || 1;
    this.canvas.width = Math.round(size * this._dpr);
    this.canvas.height = Math.round(size * this._dpr);
    this.size = size;
    this.draw();
  }

  /** Cumulative wedge geometry, proportional to slice weight. */
  geometry() {
    const total = this.slices.reduce((sum, s) => sum + Math.max(s.weight || 1, 0.0001), 0) || 1;
    let cursor = POINTER_ANGLE;
    return this.slices.map((slice) => {
      const span = (Math.max(slice.weight || 1, 0.0001) / total) * TAU;
      const entry = { slice, start: cursor, end: cursor + span, span, center: cursor + span / 2 };
      cursor += span;
      return entry;
    });
  }

  colorFor(slice, index) {
    if (slice.color) return slice.color;
    const palette = this.appearance.palette?.length ? this.appearance.palette : DEFAULT_PALETTE;
    return palette[index % palette.length];
  }

  draw() {
    // Self-heal the backing store. requestAnimationFrame and ResizeObserver are
    // both throttled in a background tab and in a hidden OBS source, so a draw
    // is the only moment guaranteed to happen - check the real size here rather
    // than trusting whoever last called resize().
    if (!this._resizing) {
      const rect = this.canvas.getBoundingClientRect();
      const measured = Math.min(rect.width, rect.height);
      if (measured > 0 && Math.abs(measured - (this.size || 0)) > 0.5) {
        this._resizing = true;
        try {
          this.resize();
        } finally {
          this._resizing = false;
        }
        return; // resize() drew at the corrected size
      }
    }

    const ctx = this.ctx;
    const size = this.canvas.width;
    if (!size) return;
    const scale = this._dpr;
    const look = this.appearance;
    const radius = size / 2 - look.rim_width * scale - 6 * scale;
    const cx = size / 2;
    const cy = size / 2;

    // The element can be measured at zero width before layout settles.
    if (radius <= 4) return;

    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, size, size);

    // 1. Background image, behind everything, covering the whole source.
    this._drawBackground(ctx, size);

    if (!this.slices.length) {
      this._drawEmpty(ctx, cx, cy, radius, scale);
      this._drawFrame(ctx, cx, cy, radius, scale);
      return;
    }

    const geo = this.geometry();
    const inner = radius * clamp(look.inner_radius || 0, 0, 0.9);

    // 2-4. Wedges, wedge images and labels all live in the rotated frame.
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(this.rotation);

    geo.forEach((entry, index) => this._drawWedge(ctx, entry, index, radius, inner, scale));
    geo.forEach((entry) => this._drawSliceImage(ctx, entry, radius, inner, scale));
    geo.forEach((entry, index) => this._drawLabel(ctx, entry, index, radius, scale));

    ctx.restore();

    // 5-8. Everything from here stays put while the wheel turns.
    this._drawRim(ctx, cx, cy, radius, scale);
    this._drawHub(ctx, cx, cy, radius, scale);
    this._drawFrame(ctx, cx, cy, radius, scale);
    this._drawPointer(ctx, cx, cy, radius, scale);
  }

  /** Draw one image layer centred on (x, y) inside a box, honouring transforms. */
  _drawLayer(ctx, layer, { x, y, boxWidth, boxHeight, mode = 'contain', radius }) {
    if (!layerActive(layer)) return false;
    const image = getImage(layer.url);
    if (!image) return false; // still loading, or it will never load

    const fit = mode === 'cover' ? coverSize : containSize;
    const { width, height } = fit(image, boxWidth, boxHeight);
    const scaleFactor = layer.scale ?? 1;
    // Offsets are fractions of the wheel radius so a layout survives resizing.
    const unit = radius ?? Math.max(boxWidth, boxHeight) / 2;

    ctx.save();
    ctx.globalAlpha = clamp(layer.opacity ?? 1, 0, 1);
    ctx.translate(x + (layer.offset_x || 0) * unit, y + (layer.offset_y || 0) * unit);
    if (layer.rotation) ctx.rotate((layer.rotation * Math.PI) / 180);
    ctx.drawImage(
      image,
      (-width * scaleFactor) / 2,
      (-height * scaleFactor) / 2,
      width * scaleFactor,
      height * scaleFactor,
    );
    ctx.restore();
    return true;
  }

  _drawBackground(ctx, size) {
    this._drawLayer(ctx, this.appearance.background_image, {
      x: size / 2,
      y: size / 2,
      boxWidth: size,
      boxHeight: size,
      mode: 'cover',
      radius: size / 2,
    });
  }

  /** One wedge, with optional gap, donut hole, shading and border. */
  _drawWedge(ctx, entry, index, radius, inner, scale) {
    const look = this.appearance;
    const halfGap = (((look.wedge_gap || 0) * Math.PI) / 180) / 2;
    const start = entry.start + halfGap;
    const end = entry.end - halfGap;
    if (end <= start) return; // the gap swallowed a very thin wedge

    ctx.beginPath();
    if (inner > 0) {
      ctx.arc(0, 0, radius, start, end);
      ctx.arc(0, 0, inner, end, start, true);
    } else {
      ctx.moveTo(0, 0);
      ctx.arc(0, 0, radius, start, end);
    }
    ctx.closePath();

    const base = this.colorFor(entry.slice, index);
    const shading = clamp(look.wedge_shading || 0, 0, 1);
    if (shading > 0) {
      const gradient = ctx.createRadialGradient(0, 0, Math.max(inner, 1), 0, 0, radius);
      gradient.addColorStop(0, shade(base, -shading));
      gradient.addColorStop(1, base);
      ctx.fillStyle = gradient;
    } else {
      ctx.fillStyle = base;
    }
    ctx.fill();

    if ((look.slice_border_width || 0) > 0) {
      ctx.lineWidth = look.slice_border_width * scale;
      ctx.strokeStyle = look.slice_border_color || 'rgba(0,0,0,0.18)';
      ctx.stroke();
    }

    if (index === this.highlightIndex) {
      ctx.save();
      ctx.globalCompositeOperation = 'lighter';
      ctx.fillStyle = 'rgba(255, 255, 255, 0.22)';
      ctx.fill();
      ctx.restore();
    }
  }

  /** The per-slice image, optionally clipped to its wedge and kept upright. */
  _drawSliceImage(ctx, entry, radius, inner, scale) {
    const image = entry.slice.image;
    if (!layerActive(image)) return;

    const halfGap = (((this.appearance.wedge_gap || 0) * Math.PI) / 180) / 2;
    const start = entry.start + halfGap;
    const end = entry.end - halfGap;
    if (end <= start) return;

    ctx.save();
    if (image.clip_to_slice !== false) {
      ctx.beginPath();
      if (inner > 0) {
        ctx.arc(0, 0, radius, start, end);
        ctx.arc(0, 0, inner, end, start, true);
      } else {
        ctx.moveTo(0, 0);
        ctx.arc(0, 0, radius, start, end);
      }
      ctx.closePath();
      ctx.clip();
    }

    // Move out along the wedge's centre line to the configured radius.
    ctx.rotate(entry.center);
    const distance = radius * clamp(image.radial ?? 0.6, 0, 1.2);
    ctx.translate(distance, 0);
    // Undo the wedge and wheel rotation when the image should stay upright.
    if (image.rotate_with_wheel === false) ctx.rotate(-entry.center - this.rotation);

    const box = radius * clamp(image.size ?? 0.26, 0.02, 1.5);
    this._drawLayer(ctx, image, {
      x: 0,
      y: 0,
      boxWidth: box,
      boxHeight: box,
      mode: 'contain',
      radius,
    });
    ctx.restore();
  }

  _drawEmpty(ctx, cx, cy, radius, scale) {
    ctx.beginPath();
    ctx.arc(cx, cy, radius, 0, TAU);
    ctx.fillStyle = 'rgba(255,255,255,0.05)';
    ctx.fill();
    ctx.strokeStyle = 'rgba(255,255,255,0.18)';
    ctx.lineWidth = 2 * scale;
    ctx.setLineDash([8 * scale, 8 * scale]);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = 'rgba(255,255,255,0.55)';
    ctx.font = `${14 * scale}px ${this.appearance.font_family}`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('No slices yet', cx, cy);
  }

  _drawLabel(ctx, entry, index, radius, scale) {
    const look = this.appearance;
    if (entry.slice.image?.replace_label && layerActive(entry.slice.image)) return;

    const maxChars = look.label_max_chars || 22;
    let label = String(entry.slice.label ?? '');
    if (look.text_uppercase) label = label.toUpperCase();
    if (label.length > maxChars) label = `${label.slice(0, maxChars - 1)}…`;
    if (!label) return;

    // Shrink the font when wedges get thin so labels stay inside their slice.
    const arcHeight = entry.span * radius;
    const base = look.font_size * scale;
    const fontSize = Math.max(9 * scale, Math.min(base, arcHeight * 0.62));
    const color = entry.slice.text_color || contrastColor(this.colorFor(entry.slice, index));

    ctx.save();
    ctx.font = `${look.font_weight} ${fontSize}px ${look.font_family}`;
    ctx.fillStyle = color;
    if (look.text_shadow) {
      ctx.shadowColor = 'rgba(0,0,0,0.35)';
      ctx.shadowBlur = 3 * scale;
    }
    const strokeWidth = (look.text_stroke_width || 0) * scale;
    const stroking = strokeWidth > 0 && Boolean(look.text_stroke_color);
    if (stroking) {
      ctx.lineWidth = strokeWidth;
      ctx.strokeStyle = look.text_stroke_color;
      ctx.lineJoin = 'round';
    }

    if (look.text_curved) {
      this._drawCurvedLabel(ctx, label, entry, radius, stroking);
    } else {
      ctx.rotate(entry.center);
      ctx.textAlign = 'right';
      ctx.textBaseline = 'middle';
      const x = radius * clamp(look.text_radial ?? 0.94, 0.1, 1) - 6 * scale;
      if (stroking) ctx.strokeText(label, x, 0);
      ctx.fillText(label, x, 0);
    }
    ctx.restore();
  }

  /** Bend a label around its wedge, one character at a time. */
  _drawCurvedLabel(ctx, label, entry, radius, stroking) {
    const look = this.appearance;
    const textRadius = radius * clamp(look.text_radial ?? 0.94, 0.1, 1) * 0.92;
    if (textRadius <= 0) return;

    const characters = [...label];
    const widths = characters.map((c) => ctx.measureText(c).width);
    const totalWidth = widths.reduce((sum, w) => sum + w, 0);

    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.rotate(entry.center);

    // Centre the run of characters on the wedge's middle line.
    let angle = -totalWidth / (2 * textRadius);
    for (let i = 0; i < characters.length; i += 1) {
      const step = widths[i] / textRadius;
      ctx.save();
      ctx.rotate(angle + step / 2);
      ctx.translate(textRadius, 0);
      ctx.rotate(Math.PI / 2);
      if (stroking) ctx.strokeText(characters[i], 0, 0);
      ctx.fillText(characters[i], 0, 0);
      ctx.restore();
      angle += step;
    }
  }

  _drawRim(ctx, cx, cy, radius, scale) {
    const width = (this.appearance.rim_width || 0) * scale;
    if (width <= 0) return;
    ctx.beginPath();
    ctx.arc(cx, cy, radius + width / 2, 0, TAU);
    ctx.lineWidth = width;
    ctx.strokeStyle = this.appearance.rim_color;
    ctx.stroke();
  }

  _drawHub(ctx, cx, cy, radius, scale) {
    const look = this.appearance;
    const hasImage = layerActive(look.hub_image);
    if (look.show_hub === false && !hasImage) return;
    const hubRadius = Math.max(10 * scale, radius * clamp(look.hub_radius ?? 0.14, 0.02, 0.9));

    if (look.show_hub !== false) {
      ctx.beginPath();
      ctx.arc(cx, cy, hubRadius, 0, TAU);
      ctx.fillStyle = look.hub_color;
      ctx.fill();
      ctx.lineWidth = 2 * scale;
      ctx.strokeStyle = 'rgba(255,255,255,0.16)';
      ctx.stroke();
    }

    if (hasImage) {
      ctx.save();
      ctx.beginPath();
      ctx.arc(cx, cy, hubRadius, 0, TAU);
      ctx.clip();
      this._drawLayer(ctx, look.hub_image, {
        x: cx,
        y: cy,
        boxWidth: hubRadius * 2,
        boxHeight: hubRadius * 2,
        mode: 'cover',
        radius: hubRadius,
      });
      ctx.restore();
    }

    if (look.hub_label && look.show_hub !== false && !hasImage) {
      ctx.fillStyle = look.text_color;
      ctx.font = `${look.font_weight} ${hubRadius * 0.42}px ${look.font_family}`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(look.hub_label.slice(0, 8), cx, cy);
    }
  }

  /** A static overlay drawn on top of the spinning wheel - frames, glass, glow. */
  _drawFrame(ctx, cx, cy, radius, scale) {
    const rim = (this.appearance.rim_width || 0) * scale;
    const span = (radius + rim) * 2;
    this._drawLayer(ctx, this.appearance.frame_image, {
      x: cx,
      y: cy,
      boxWidth: span,
      boxHeight: span,
      mode: 'contain',
      radius,
    });
  }

  _drawPointer(ctx, cx, cy, radius, scale) {
    const look = this.appearance;
    const sizeFactor = clamp(look.pointer_size ?? 1, 0.1, 4);

    if (layerActive(look.pointer_image)) {
      const box = radius * 0.28 * sizeFactor;
      this._drawLayer(ctx, look.pointer_image, {
        x: cx,
        y: cy - radius + box / 2,
        boxWidth: box,
        boxHeight: box,
        mode: 'contain',
        radius,
      });
      return;
    }
    if (look.show_pointer === false) return;

    const width = 18 * scale * sizeFactor;
    const height = 30 * scale * sizeFactor;
    const tipY = cy - radius + 6 * scale;
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(cx, tipY);
    ctx.lineTo(cx - width / 2, tipY - height);
    ctx.lineTo(cx + width / 2, tipY - height);
    ctx.closePath();
    ctx.fillStyle = look.pointer_color;
    ctx.shadowColor = 'rgba(0,0,0,0.45)';
    ctx.shadowBlur = 6 * scale;
    ctx.fill();
    ctx.restore();
  }

  /**
   * Animate to `targetIndex`.
   * @returns {Promise<void>} resolves when the wheel stops.
   */
  spin({ targetIndex, durationMs = 6000, turns = 6, easing = 'easeOutQuint', spinId = '' }) {
    const geo = this.geometry();
    if (!geo.length) return Promise.resolve();
    const target = geo[Math.max(0, Math.min(targetIndex, geo.length - 1))];

    // Land off-centre by a deterministic amount so it does not look robotic.
    const jitter = (hashUnit(spinId || String(targetIndex)) - 0.5) * target.span * 0.7;
    const desired = POINTER_ANGLE - (target.center + jitter);
    const current = this.rotation;
    let delta = (desired - current) % TAU;
    if (delta < 0) delta += TAU;
    const final = current + delta + TAU * Math.max(1, turns);

    this.spinning = true;
    this.highlightIndex = -1;
    const ease = EASINGS[easing] || EASINGS.easeOutQuint;
    const start = performance.now();

    return new Promise((resolve) => {
      let settled = false;

      const finish = () => {
        if (settled) return;
        settled = true;
        clearTimeout(this._settleTimer);
        this._settleTimer = null;
        if (this._frame) cancelAnimationFrame(this._frame);
        this._frame = null;
        this.rotation = final % TAU;
        this.spinning = false;
        this.highlightIndex = targetIndex;
        this.draw();
        this._ensureLoop();
        this._resolveSpin = null;
        resolve();
      };

      this._resolveSpin = finish;

      const step = (now) => {
        const t = Math.min(1, (now - start) / durationMs);
        this.rotation = current + (final - current) * ease(t);
        this.draw();
        if (t < 1) {
          this._frame = requestAnimationFrame(step);
          return;
        }
        finish();
      };

      if (this._frame) cancelAnimationFrame(this._frame);
      clearTimeout(this._settleTimer);
      // Browsers pause requestAnimationFrame while a tab or an OBS source is
      // hidden. Without this timer the wheel would stay mid-spin forever and the
      // winner would never be announced, so wall-clock time has the final say.
      this._settleTimer = setTimeout(finish, durationMs + 250);
      this._frame = requestAnimationFrame(step);
    });
  }

  /** Jump straight to a result, for a browser source that reloaded mid-spin. */
  settleOn(targetIndex) {
    const geo = this.geometry();
    if (!geo.length) return;
    const target = geo[Math.max(0, Math.min(targetIndex, geo.length - 1))];
    this.rotation = POINTER_ANGLE - target.center;
    this.spinning = false;
    this.highlightIndex = targetIndex;
    this.draw();
  }

  clearHighlight() {
    this.highlightIndex = -1;
    this.draw();
  }

  /** Slow idle rotation, when the appearance asks for one. */
  _ensureLoop() {
    const speed = Number(this.appearance.idle_spin_speed || 0);
    if (this.spinning || speed <= 0) {
      if (this._idleFrame) {
        cancelAnimationFrame(this._idleFrame);
        this._idleFrame = null;
      }
      return;
    }
    if (this._idleFrame) return;
    this._lastTick = performance.now();
    const tick = (now) => {
      const dt = (now - this._lastTick) / 1000;
      this._lastTick = now;
      if (this.spinning || Number(this.appearance.idle_spin_speed || 0) <= 0) {
        this._idleFrame = null;
        return;
      }
      this.rotation = (this.rotation + dt * Number(this.appearance.idle_spin_speed) * 0.2) % TAU;
      this.draw();
      this._idleFrame = requestAnimationFrame(tick);
    };
    this._idleFrame = requestAnimationFrame(tick);
  }

  destroy() {
    if (this._stopWatchingImages) this._stopWatchingImages();
    if (this._frame) cancelAnimationFrame(this._frame);
    if (this._idleFrame) cancelAnimationFrame(this._idleFrame);
    clearTimeout(this._settleTimer);
    this._frame = this._idleFrame = this._settleTimer = null;
  }
}
