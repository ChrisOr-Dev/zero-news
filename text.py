"""Fonts and line wrapping for mixed Latin / CJK text."""
import os
import re

from PIL import ImageFont

APP_FONTS = '/usr/share/APPLaunch/share/font'
SYS_NOTO = '/usr/share/fonts/opentype/noto'
CJK_REGULAR = [f'{APP_FONTS}/NotoSansCJK-Regular.ttc', f'{SYS_NOTO}/NotoSansCJK-Regular.ttc']
CJK_BOLD = [f'{SYS_NOTO}/NotoSansCJK-Bold.ttc'] + CJK_REGULAR
CJK_INDEX = 4  # Noto Sans CJK HK
_fonts = {}


def _load(paths, size, index=0, **kw):
    for p in paths:
        if os.path.exists(p):
            return ImageFont.truetype(p, size, index=index, **kw)
    return ImageFont.load_default(size)


def cjk(size, bold=False):
    key = ('b' if bold else 'r', size)
    if key not in _fonts:
        _fonts[key] = _load(CJK_BOLD if bold else CJK_REGULAR, size, CJK_INDEX)
    return _fonts[key]


def mono(size, bold=False):
    key = ('mb' if bold else 'm', size)
    if key not in _fonts:
        name = 'JetBrainsMono-Bold.ttf' if bold else 'JetBrainsMono-Regular.ttf'
        # Basic layout: no ligatures, so key hints like '↑↓' and '←→' stay two arrows.
        _fonts[key] = _load([f'{APP_FONTS}/{name}'], size, layout_engine=ImageFont.Layout.BASIC)
    return _fonts[key]


# Latin words (with trailing space) stay whole; CJK and anything else breaks per character.
# CJK punctuation that must not start a line: it stays on the line before, a few px over.
NO_LINE_START = set('，。、；：！？）」』》〉】〕…,.;:!?)')
TOKEN = re.compile(r"[A-Za-z0-9À-ɏ'’\-_.,:;!?%$&@#/+()\"“”]+ *| +|.")


_widths = {}


def _width(font, s):
    """Advance width of s, cached: measuring is slow on the device and articles
    repeat the same characters and words."""
    key = (id(font), s)
    w = _widths.get(key)
    if w is None:
        if len(_widths) > 50_000:
            _widths.clear()
        w = _widths[key] = font.getlength(s)
    return w


def wrap(d, text, font, width):
    """Lines of text that fit width, breaking between words or CJK characters."""
    lines, cur, cur_w = [], '', 0.0
    for tok in TOKEN.findall(text):
        if cur_w + _width(font, tok.rstrip()) <= width or (tok in NO_LINE_START and cur.strip()):
            cur += tok
            cur_w += _width(font, tok)
            continue
        if cur.strip():
            lines.append(cur.rstrip())
        cur = tok.lstrip()
        while cur and d.textlength(cur.rstrip(), font=font) > width:
            cut = len(cur) - 1
            while cut > 1 and d.textlength(cur[:cut], font=font) > width:
                cut -= 1
            lines.append(cur[:cut])
            cur = cur[cut:]
        cur_w = _width(font, cur)
    if cur.strip():
        lines.append(cur.rstrip())
    return lines


def ellipsize(d, line, font, width):
    while line and d.textlength(line + '…', font=font) > width:
        line = line[:-1]
    return line.rstrip() + '…'


def clip_lines(d, text, font, width, max_lines):
    """(lines, truncated) with the last kept line ending in an ellipsis if cut."""
    lines = wrap(d, text, font, width)
    if len(lines) <= max_lines:
        return lines, False
    lines = lines[:max(1, max_lines)]
    lines[-1] = ellipsize(d, lines[-1], font, width)
    return lines, True
