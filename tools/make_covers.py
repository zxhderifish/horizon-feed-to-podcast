"""Generate 1400x1400 podcast covers. One-shot; kept for regeneration."""

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SIZE = 1400
BG = (13, 17, 23)        # #0d1117
FG = (230, 237, 243)     # near-white
ACCENT = (63, 185, 80)   # signal green
NOISE = (68, 76, 86)     # muted noise bars

ZH_FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
]
ZH_FONT = next(p for p in ZH_FONT_CANDIDATES if Path(p).exists())
EN_FONT = "/System/Library/Fonts/Helvetica.ttc"


def _bars(draw: ImageDraw.ImageDraw) -> None:
    # noise floor with one strong signal spike: the show's namesake
    import random

    random.seed(42)
    n, w, gap, base_y = 28, 26, 22, 1120
    x0 = (SIZE - n * (w + gap)) // 2
    for i in range(n):
        h = random.randint(30, 110)
        color = NOISE
        if i == 19:  # the signal
            h, color = 320, ACCENT
        x = x0 + i * (w + gap)
        draw.rounded_rectangle(
            [x, base_y - h, x + w, base_y], radius=8, fill=color
        )


def make(path: Path, title_lines, font_path, title_size, sub="", y0=380):
    img = Image.new("RGB", (SIZE, SIZE), BG)
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(font_path, title_size)
    y = y0
    for line in title_lines:
        bbox = d.textbbox((0, 0), line, font=font)
        d.text(((SIZE - (bbox[2] - bbox[0])) / 2, y), line, font=font, fill=FG)
        y += title_size + 40
    if sub:
        sf = ImageFont.truetype(font_path, 56)
        bbox = d.textbbox((0, 0), sub, font=sf)
        d.text(((SIZE - (bbox[2] - bbox[0])) / 2, y + 20), sub, font=sf, fill=ACCENT)
    _bars(d)
    img.save(path, "PNG")
    print(path, path.stat().st_size, "bytes")


if __name__ == "__main__":
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    # Placeholder covers so the feed validates before you have real art.
    # Replace covers/cover-<lang>.jpg with your own 1400x1400 artwork.
    make(out / "cover-zh.png", ["示例节目"], ZH_FONT, 200)
    # y0=280 keeps the second line clear of the signal spike
    make(out / "cover-en.png", ["SAMPLE", "SHOW"], EN_FONT, 220, y0=280)
