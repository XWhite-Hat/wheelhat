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
// Diameter, in CSS pixels, that the sizes stored on a wheel (font_size,
// rim_width, stroke widths) are expressed in. Everything is scaled from here.
const REFERENCE_SIZE = 600;

// Vertical room the overlay reserves for the title and the winner banner.
// Exported so the overlay's layout and the editor's recommendation cannot
// drift apart and start disagreeing about what fits.
export const TITLE_BAND = 52;
export const RESULT_BAND = 84;
export const EDGE_PADDING = 24;
// Matches Appearance.size in models.py - the wheel size a new wheel starts at.
export const DEFAULT_WHEEL_SIZE = 720;
// Most labels are one or two lines; beyond three the text is too small to
// read on stream, so the last line is trimmed instead.
const MAX_LABEL_LINES = 3;
// Line spacing as a multiple of the font size.
const LINE_HEIGHT = 1.12;

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
  label_wrap: true,
  shadow_enabled: true,
  shadow_color: '#000000',
  shadow_opacity: 0.45,
  shadow_blur: 45,
  shadow_offset_x: 0,
  shadow_offset_y: 18,
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

/**
 * How far past the wheel and its rim the frame image reaches.
 *
 * The frame is fitted to the wheel plus rim, then its own scale and offset
 * are applied on top - so a frame larger than 1 extends beyond the wheel and,
 * without headroom, is cropped by the edge of the browser source.
 */
export function frameHeadroom(appearance = {}) {
  const frame = appearance.frame_image;
  if (!layerActive(frame)) return { scale: 1, offset: 0 };
  return {
    scale: Math.max(1, Number(frame.scale) || 1),
    offset: Math.max(Math.abs(frame.offset_x || 0), Math.abs(frame.offset_y || 0)),
  };
}

/**
 * Wheel radius that leaves room for the rim and any frame drawn around it.
 *
 * The frame is fitted to (radius + rim) and then multiplied by its own scale
 * and shifted by its own offset, so the furthest it reaches from the centre is
 * (radius + rim) * scale + offset * radius. Solving that against the half-size
 * of the canvas is what keeps the frame inside the source instead of cropped.
 */
export function wheelRadius(size, appearance, scale) {
  const headroom = frameHeadroom(appearance);
  const rim = (appearance.rim_width || 0) * scale;
  return (size / 2 - 6 * scale - rim * headroom.scale) / (headroom.scale + headroom.offset);
}

/**
 * The wheel's drop shadow, as a CSS filter value.
 *
 * A CSS filter rather than a canvas shadow on purpose: drop-shadow() works from
 * the rendered alpha, so a wheel with a centre hole or gaps between its wedges
 * casts the shadow of that shape. A canvas shadow would fill the hole in.
 *
 * Sizes are px at REFERENCE_SIZE and scale with the wheel, so the same setting
 * looks the same on a 400px source and a 1080px one.
 */
export function shadowFilter(appearance = {}, size = REFERENCE_SIZE) {
  if (appearance.shadow_enabled === false) return 'none';
  const scale = Math.max(size, 1) / REFERENCE_SIZE;
  const blur = Math.max(0, Number(appearance.shadow_blur ?? 45)) * scale;
  const x = Number(appearance.shadow_offset_x ?? 0) * scale;
  const y = Number(appearance.shadow_offset_y ?? 18) * scale;
  const opacity = clamp(appearance.shadow_opacity ?? 0.45, 0, 1);
  if (blur <= 0 && x === 0 && y === 0) return 'none';
  if (opacity <= 0) return 'none';
  const colour = withAlpha(appearance.shadow_color || '#000000', opacity);
  return `drop-shadow(${x.toFixed(1)}px ${y.toFixed(1)}px ${blur.toFixed(1)}px ${colour})`;
}

/** #rrggbb plus an opacity, as rgba(). Anything else is passed through. */
function withAlpha(colour, opacity) {
  const hex = String(colour).trim();
  const match = /^#?([0-9a-f]{6})$/i.exec(hex);
  if (!match) return hex;
  const value = parseInt(match[1], 16);
  const r = (value >> 16) & 255;
  const g = (value >> 8) & 255;
  const b = value & 255;
  return `rgba(${r}, ${g}, ${b}, ${opacity})`;
}

/** The browser source size that fits this wheel with nothing cropped. */
export function recommendedSource(appearance = {}) {
  const wheel = Math.max(120, Number(appearance.size) || DEFAULT_WHEEL_SIZE);
  const headroom = frameHeadroom(appearance);
  const box = Math.ceil(wheel * (headroom.scale + headroom.offset));
  const title = appearance.show_title === false ? 0 : TITLE_BAND;
  const under =
    appearance.show_result !== false && (appearance.result_position || 'under') !== 'over';
  return {
    width: box + EDGE_PADDING * 2,
    height: box + title + (under ? RESULT_BAND : 0) + EDGE_PADDING * 2,
  };
}

/** Greedy word wrap at the context's current font. */
function wrapLines(ctx, words, room) {
  const lines = [];
  let current = '';
  for (const word of words) {
    const candidate = current ? `${current} ${word}` : word;
    if (current && ctx.measureText(candidate).width > room) {
      lines.push(current);
      current = word;
    } else {
      current = candidate;
    }
  }
  if (current) lines.push(current);
  return lines;
}

/** Widest of a set of lines at the current font. */
function widestLine(ctx, lines) {
  return lines.reduce((widest, line) => Math.max(widest, ctx.measureText(line).width), 0);
}

/**
 * Trim a label with an ellipsis until it fits the width available.
 *
 * label_max_chars trims by character count, which says nothing about how
 * wide the result actually is - twenty-two wide characters still overrun.
 * The font is measured first; this only runs when shrinking was not enough.
 */
function fitText(ctx, label, room) {
  if (ctx.measureText(label).width <= room) return label;
  let text = label;
  while (text.length > 1 && ctx.measureText(`${text}…`).width > room) {
    text = text.slice(0, -1);
  }
  return `${text}…`;
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

  /** The inline colour for a wedge: its own, or the wheel's default. */
  borderColorFor(slice) {
    return slice.border_color || this.appearance.slice_border_color || 'rgba(0,0,0,0.18)';
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
    // Scale decorations with the wheel, not with the display density. Using the
    // device pixel ratio alone pinned text to a fixed CSS size however big the
    // wheel was: a label that sat comfortably in an OBS source overran the hub
    // in the small editor preview, and looked lost on a large source.
    const scale = size / REFERENCE_SIZE;
    const look = this.appearance;
    // Leave room for a frame that reaches past the wheel. Without this the
    // frame is drawn to the canvas edge and its outer edges are simply cropped,
    // because the canvas only ever had a few pixels of slack around the rim.
    const radius = wheelRadius(size, look, scale);
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
    geo.forEach((entry, index) => this._drawLabel(ctx, entry, index, radius, inner, scale));

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
      // An inline, not an outline: clip to this wedge and stroke at double
      // width so only the inner half survives. A centred stroke puts half its
      // width in the neighbouring wedge, so two touching wedges both paint the
      // shared edge - it ends up thicker than the outer edges, and twice as
      // dark with a semi-transparent colour. Clipped, each wedge owns its own
      // border and neighbouring inlines simply meet.
      ctx.save();
      ctx.clip();
      ctx.lineWidth = look.slice_border_width * scale * 2;
      ctx.strokeStyle = this.borderColorFor(entry.slice);
      ctx.stroke();
      ctx.restore();
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

  _drawLabel(ctx, entry, index, radius, inner, scale) {
    const look = this.appearance;
    if (entry.slice.image?.replace_label && layerActive(entry.slice.image)) return;

    let label = String(entry.slice.label ?? '');
    if (look.text_uppercase) label = label.toUpperCase();
    // The character cap is a blunt instrument - it counts letters, which says
    // nothing about how wide they are. It still applies when wrapping is off,
    // where a single line has to be cut somewhere; with wrapping on, fitting is
    // decided by measurement instead and the cap would only throw away words
    // that would have fitted.
    if (look.label_wrap === false) {
      const maxChars = look.label_max_chars || 22;
      if (label.length > maxChars) label = `${label.slice(0, maxChars - 1)}…`;
    }
    if (!label) return;

    // Shrink the font when wedges get thin so labels stay inside their slice.
    const arcHeight = entry.span * radius;
    const base = look.font_size * scale;
    let fontSize = Math.max(9 * scale, Math.min(base, arcHeight * 0.62));
    const color = entry.slice.text_color || contrastColor(this.colorFor(entry.slice, index));

    ctx.save();
    const applyFont = () => {
      ctx.font = `${look.font_weight} ${fontSize}px ${look.font_family}`;
    };
    applyFont();
    ctx.fillStyle = color;
    if (look.text_shadow) {
      ctx.shadowColor = 'rgba(0,0,0,0.35)';
      ctx.shadowBlur = 3 * scale;
    }
    const strokeWidth = (look.text_stroke_width || 0) * scale;
    // A slice may override the outline colour; the width stays a wheel-wide
    // setting so labels keep a consistent weight. Resolve before deciding
    // whether to stroke, or a slice colour would be ignored when the wheel
    // itself has no outline colour set.
    const strokeColor = entry.slice.text_stroke_color || look.text_stroke_color;
    const stroking = strokeWidth > 0 && Boolean(strokeColor);
    if (stroking) {
      ctx.lineWidth = strokeWidth;
      ctx.strokeStyle = strokeColor;
      ctx.lineJoin = 'round';
    }

    if (look.text_curved) {
      this._drawCurvedLabel(ctx, label, entry, radius, stroking);
    } else {
      const x = radius * clamp(look.text_radial ?? 0.94, 0.1, 1) - 6 * scale;

      // Labels are right-aligned at the rim and grow inward, so the room they
      // have is what is left before whatever occupies the middle - the hub, or
      // a donut hole. Only the wedge's arc height was checked before, which
      // says nothing about length, so a long label ran straight over the hub.
      const blocked = Math.max(inner, this._hubRadius(radius, scale));
      const room = Math.max(24 * scale, x - blocked - 4 * scale);

      const floor = 8 * scale;
      let lines = [label];

      if (look.label_wrap === false) {
        // Single line: shrink, then trim what still will not fit.
        const measured = ctx.measureText(label).width;
        if (measured > room) {
          fontSize = Math.max(floor, (fontSize * room) / measured);
          applyFont();
          lines = [fitText(ctx, label, room)];
        }
      } else {
        // Wrap instead of throwing words away, at the largest size that fits
        // both directions: each line inside `room` radially, and the whole
        // block inside the wedge tangentially. The wedge narrows towards the
        // hub, so the height is checked where the text is innermost - its
        // widest line - which is the tightest point.
        const words = label.split(/\s+/).filter(Boolean);
        for (let attempt = 0; attempt < 14; attempt += 1) {
          applyFont();
          lines = wrapLines(ctx, words, room).slice(0, MAX_LABEL_LINES);
          const innerRadius = Math.max(blocked, x - widestLine(ctx, lines));
          const available = entry.span * innerRadius * 0.84;
          const fits =
            lines.length * fontSize * LINE_HEIGHT <= available &&
            widestLine(ctx, lines) <= room;
          if (fits || fontSize <= floor) break;
          fontSize = Math.max(floor, fontSize * 0.92);
        }
        // A single long word cannot be wrapped, and three lines may still be
        // too many at the smallest size. Trim whatever is left over.
        applyFont();
        lines = lines.map((line) => fitText(ctx, line, room));
      }

      ctx.rotate(entry.center);
      ctx.textAlign = 'right';
      ctx.textBaseline = 'middle';
      const step = fontSize * LINE_HEIGHT;
      const top = -((lines.length - 1) / 2) * step;
      lines.forEach((line, lineIndex) => {
        const y = top + lineIndex * step;
        if (stroking) ctx.strokeText(line, x, y);
        ctx.fillText(line, x, y);
      });
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

  /** Radius of whatever sits in the middle, or 0 when nothing is drawn there. */
  _hubRadius(radius, scale) {
    const look = this.appearance;
    if (look.show_hub === false && !layerActive(look.hub_image)) return 0;
    return Math.max(10 * scale, radius * clamp(look.hub_radius ?? 0.14, 0.02, 0.9));
  }

  _drawHub(ctx, cx, cy, radius, scale) {
    const look = this.appearance;
    const hasImage = layerActive(look.hub_image);
    if (look.show_hub === false && !hasImage) return;
    const hubRadius = this._hubRadius(radius, scale);

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
    const index = this._resolveIndex(targetIndex, geo);
    if (index < 0) return Promise.resolve();
    const target = geo[index];

    // Land off-centre by a deterministic amount so it does not look robotic.
    const jitter = (hashUnit(spinId || String(index)) - 0.5) * target.span * 0.7;
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
        this.highlightIndex = index;
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

  /** Resolve a slice id or an index against the current geometry. -1 if unknown.
   *
   * A slice id is accepted as well as an index because on a reconnect the
   * overlay builds its own slice list, which need not be the one the spin
   * indexed - matching by id is the only way to be sure of the winning wedge.
   */
  _resolveIndex(target, geo) {
    if (!geo.length) return -1;
    if (typeof target === 'number') return Math.max(0, Math.min(target, geo.length - 1));
    return geo.findIndex((entry) => entry.slice.id === target);
  }

  /** Jump straight to a result, without animating. */
  settleOn(target) {
    const geo = this.geometry();
    const targetIndex = this._resolveIndex(target, geo);
    if (targetIndex < 0) return;
    const entry = geo[targetIndex];
    this.rotation = POINTER_ANGLE - entry.center;
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
