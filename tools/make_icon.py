"""Generate the WheelHat application icon.

Matches the lettermark used by MusicHat: a black rounded square with the app's
initials in white bold monospace, wrapped in angle brackets and pipes.

Pillow is needed only to run this, not to run WheelHat. Regenerate with:

    pip install Pillow
    python tools/make_icon.py
"""

from __future__ import annotations

import pathlib

from PIL import Image, ImageDraw, ImageFont

SIZES = [256, 128, 64, 48, 32, 16]
LABEL = "<|WH|>"

OUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "wheelhat" / "web" / "static" / "img"

#: Bold monospace first; each is a Windows built-in, with a fallback for CI.
FONT_CANDIDATES = [
    "consolab.ttf",  # Consolas Bold
    "consola.ttf",   # Consolas
    "cour.ttf",      # Courier New
    "lucon.ttf",     # Lucida Console
    "DejaVuSansMono-Bold.ttf",
    "DejaVuSansMono.ttf",
]


def _font(size: int):
    for name in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def make_frame(px: int) -> Image.Image:
    image = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    radius = max(4, px // 8)
    draw.rounded_rectangle([0, 0, px - 1, px - 1], radius=radius, fill=(0, 0, 0, 255))

    font = _font(int(px * 0.28))
    box = draw.textbbox((0, 0), LABEL, font=font)
    width, height = box[2] - box[0], box[3] - box[1]
    x = (px - width) // 2 - box[0]
    y = (px - height) // 2 - box[1]
    draw.text((x, y), LABEL, font=font, fill=(255, 255, 255, 255))
    return image


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    frames = [make_frame(size) for size in SIZES]

    ico = OUT_DIR / "icon.ico"
    frames[0].save(
        ico, format="ICO", append_images=frames[1:], sizes=[(s, s) for s in SIZES]
    )

    # The PNG is what Qt uses for the window and tray icon.
    png = OUT_DIR / "icon.png"
    frames[0].save(png, format="PNG")

    print(f"wrote {ico} ({ico.stat().st_size // 1024} KB) and {png}")


if __name__ == "__main__":
    main()
