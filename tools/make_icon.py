"""Generate the WheelHat application icon.

The mark is `<|WH|>` drawn as geometry rather than typeset. It used to be
rasterised from Consolas, falling back to Courier New or Lucida Console - fonts
licensed with Windows, not redistributable, and the resulting bitmaps are
committed to this repository and shipped inside the executable. Baking six
glyphs from a proprietary face into a distributed binary is at best a grey area,
and swapping in an open font would only move the question rather than settle it.

Drawing the strokes ourselves makes the icon original artwork under this
project's own licence, with no font involved on any machine that builds it.

    python tools/make_icon.py

Needs Pillow (in the dev extra). It is deliberately excluded from the frozen
build - only this script uses it.
"""

from __future__ import annotations

import pathlib

from PIL import Image, ImageDraw

SIZES = [256, 128, 64, 48, 32, 16]
OUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "wheelhat" / "web" / "static" / "img"

BACKGROUND = (0, 0, 0, 255)
INK = (255, 255, 255, 255)

# All geometry is a fraction of the icon's side, so every size is the same
# drawing rather than a resampled copy of one.
TOP, BOTTOM = 0.350, 0.650
STROKE = 0.036
CHEVRON_W, BAR_W, W_W, H_W = 0.072, 0.018, 0.165, 0.125
GAP = 0.040

#: Below this, the full mark is a smudge - six glyphs will not resolve in
#: sixteen pixels. The taskbar and the favicon get the initials alone,
#: drawn larger and heavier, which is what makes them readable at all.
SMALL_PX = 20
SMALL_STROKE = 0.085
SMALL_TOP, SMALL_BOTTOM = 0.285, 0.715


def _draw_mark(draw: ImageDraw.ImageDraw, px: int, *, small: bool = False) -> None:
    """Lay the mark out left to right, centred on the icon."""
    if small:
        glyphs = "WH"
        widths = [0.30, 0.23]
        gap = 0.075
        top, bottom = SMALL_TOP * px, SMALL_BOTTOM * px
        thickness = max(1, round(SMALL_STROKE * px))
    else:
        glyphs = "<|WH|>"
        widths = [CHEVRON_W, BAR_W, W_W, H_W, BAR_W, CHEVRON_W]
        gap = GAP
        top, bottom = TOP * px, BOTTOM * px
        thickness = max(1, round(STROKE * px))

    total = sum(widths) + gap * (len(widths) - 1)
    x = (1.0 - total) / 2
    middle = (top + bottom) / 2

    def line(points: list[tuple[float, float]]) -> None:
        draw.line([(round(a), round(b)) for a, b in points], fill=INK, width=thickness, joint="curve")

    def cap(point: tuple[float, float]) -> None:
        # Pillow does not round the ends of a line, so square joints at the
        # extremities are softened by hand. At 16px this is what stops the
        # chevrons looking chipped.
        radius = thickness / 2
        cx, cy = point
        draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=INK)

    for width, glyph in zip(widths, glyphs, strict=True):
        left, right = x * px, (x + width) * px
        if glyph == "<":
            line([(right, top), (left, middle), (right, bottom)])
            cap((right, top))
            cap((right, bottom))
        elif glyph == ">":
            line([(left, top), (right, middle), (left, bottom)])
            cap((left, top))
            cap((left, bottom))
        elif glyph == "|":
            line([((left + right) / 2, top), ((left + right) / 2, bottom)])
        elif glyph == "W":
            span = right - left
            line(
                [
                    (left, top),
                    (left + span * 0.28, bottom),
                    (left + span * 0.50, top + (bottom - top) * 0.42),
                    (left + span * 0.72, bottom),
                    (right, top),
                ]
            )
            cap((left, top))
            cap((right, top))
        elif glyph == "H":
            line([(left, top), (left, bottom)])
            line([(right, top), (right, bottom)])
            line([(left, middle), (right, middle)])
        x += width + gap


def make_frame(px: int) -> Image.Image:
    # Drawn at 4x and reduced, so the diagonals are smooth at every size.
    scale = 4 if px <= 64 else 2
    big = px * scale
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    radius = max(4, big // 8)
    draw.rounded_rectangle([0, 0, big - 1, big - 1], radius=radius, fill=BACKGROUND)
    _draw_mark(draw, big, small=px <= SMALL_PX)
    return image.resize((px, px), Image.LANCZOS)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    frames = [make_frame(size) for size in SIZES]

    ico = OUT_DIR / "icon.ico"
    # append_images matters: without it Pillow keeps only the first image and
    # resamples every other size from it, throwing away the frames drawn for
    # those sizes - which is how the 16px frame ended up an unreadable smudge.
    frames[0].save(
        ico,
        format="ICO",
        sizes=[(s, s) for s in SIZES],
        append_images=frames[1:],
    )

    png = OUT_DIR / "icon.png"
    frames[0].save(png, format="PNG")

    print(f"Wrote {ico} ({len(SIZES)} frames) and {png}.")


if __name__ == "__main__":
    main()
