"""Launcher icon for News."""
from PIL import Image, ImageDraw

from zero_news import BG, FG, DIM, BAND, ACCENT, TAG_COLORS


def make_icon(path, size=100):
    """Launcher icon; drawn on a 100-unit grid scaled to size."""
    k = size / 100
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def box(x0, y0, x1, y1, r, fill):
        d.rounded_rectangle(tuple(round(v * k) for v in (x0, y0, x1, y1)), radius=round(r * k), fill=fill)

    box(4, 4, 96, 96, 20, BG + (255,))
    box(18, 20, 44, 30, 3, TAG_COLORS['TECH'])
    for i, w in enumerate((64, 56, 62)):
        box(18, 38 + i * 11, 18 + w, 44 + i * 11, 3, FG)
    d.rectangle(tuple(round(v * k) for v in (4, 72, 96, 84)), fill=BAND)
    box(12, 75, 30, 81, 2, ACCENT)
    box(36, 76, 88, 80, 2, DIM)
    mask = Image.new('L', (size, size), 0)  # keep the band inside the rounded corners
    ImageDraw.Draw(mask).rounded_rectangle(tuple(round(v * k) for v in (4, 4, 96, 96)),
                                           radius=round(20 * k), fill=255)
    out = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    out.paste(img, mask=mask)
    out.save(path)
