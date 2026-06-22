"""Overlay crisp title text on a generated thumbnail image.

Image models garble text, so we render the title/subtitle here instead. Places
text in the upper-right negative space, with a soft shadow for legibility.

    uv run python -m review.make_thumbnail <input.png> [output.png]
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

TITLE = "Provider Directory Pipeline"
SUB = "self-verifying  ·  < $2 per 1,000 records"
TEAL = (45, 212, 191)
WHITE = (245, 248, 250)

_FONTS = [
    "/home/ieqr/.local/share/fonts/static/Inter_28pt-ExtraBold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
]
_FONTS_REG = [
    "/home/ieqr/.local/share/fonts/static/Inter_18pt-SemiBold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
]


def _font(paths, size):
    for p in paths:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: make_thumbnail.py <input.png> [output.png]")
    src = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("assets/thumbnail.png")
    out.parent.mkdir(parents=True, exist_ok=True)

    img = Image.open(src).convert("RGBA")
    W, H = img.size
    draw = ImageDraw.Draw(img)

    title_f = _font(_FONTS, int(W * 0.052))
    sub_f = _font(_FONTS_REG, int(W * 0.026))

    margin = int(W * 0.05)
    # wrap title to two lines at the space before "Directory"
    line1, line2 = "Provider Directory", "Pipeline"

    def draw_shadow(xy, text, font, fill):
        x, y = xy
        for dx, dy in ((2, 3), (1, 1)):
            draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0, 170))
        draw.text((x, y), text, font=font, fill=fill)

    # right-aligned in the upper-right negative space
    def right(text, font, y, fill):
        w = draw.textbbox((0, 0), text, font=font)[2]
        draw_shadow((W - margin - w, y), text, font, fill)

    y = int(H * 0.10)
    right(line1, title_f, y, WHITE)
    y += int(title_f.size * 1.05)
    right(line2, title_f, y, WHITE)
    y += int(title_f.size * 1.25)
    right(SUB, sub_f, y, TEAL)

    img.convert("RGB").save(out, quality=95)
    print(f"wrote {out} ({W}x{H})")


if __name__ == "__main__":
    main()
