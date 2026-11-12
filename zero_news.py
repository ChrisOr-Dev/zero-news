#!/usr/bin/python3
"""News for the M5Stack CardputerZero (320x170 LCD): one headline at a time in
large type, rotating every few seconds, with a ticker of the next headlines.

Sources depend on the languages (English / Chinese) and categories (tech /
life / general) chosen in the in-app settings (S), saved in
~/.config/zero-news.json; the feed list is CATALOG in feeds.py.

Usage:
  zero_news.py                    run on the LCD
  zero_news.py --fetch            fetch once and print the headlines
  zero_news.py --preview out.png  render preview frames to PNG (from cache, or --fetch)
  zero_news.py --make-icon out.png [size]
Keys: UP/DOWN previous/next, ENTER details, S settings, P pause, R refresh, ESC exit.
"""
import os
import struct
import sys
import threading
import time
from urllib.parse import urlparse

from PIL import Image, ImageDraw

import feeds
from text import cjk, mono, wrap, clip_lines

W, H = 320, 170
CARD_H = 146          # rows 0..145: headline card, redrawn only on change
BAND_H = H - CARD_H   # rows 146..169: 2px progress bar + 22px ticker
LABEL_W = 50
TICKER_SPEED = 45     # px per second
TICKER_DELAY = 1.0    # seconds before the ticker starts moving
POLL = 15 * 60
FRAME = 0.04
FB = '/dev/fb0'
KBD = '/dev/input/cardputer-zero-internal'
K_ESC, K_BACKSPACE, K_Q, K_R, K_P, K_ENTER, K_S, K_SPACE = 1, 14, 16, 19, 25, 28, 31, 57
K_UP, K_LEFT, K_RIGHT, K_DOWN = 103, 105, 106, 108

BG = (12, 15, 22)
FG = (240, 242, 246)
DIM = (140, 150, 166)
LINE = (38, 46, 60)
BAND = (24, 30, 42)
ACCENT = (110, 180, 255)
WARN = (232, 178, 70)
TAG_COLORS = {'TECH': (64, 200, 190), 'LIFE': (236, 112, 170), 'NEWS': (240, 150, 70)}

def fmt_ago(seconds):
    s = max(0, int(seconds))
    if s < 60:
        return 'now'
    if s < 3600:
        return f'{s // 60}m ago'
    if s < 86400:
        return f'{s // 3600}h ago'
    return f'{s // 86400}d ago'


def tag_color(item):
    return TAG_COLORS.get(item.get('tag'), ACCENT)


def draw_source(d, item, now, x=12, y=5):
    """Tag pill, source name and age; returns the x after it."""
    tag = item.get('tag', 'NEWS')
    tw = d.textlength(tag, font=mono(10, True))
    d.rounded_rectangle((x, y, x + tw + 10, y + 14), radius=4, fill=tag_color(item))
    d.text((x + 5 + tw / 2, y + 7), tag, font=mono(10, True), fill=BG, anchor='mm')
    x += tw + 16
    d.text((x, y + 7), item['src'], font=cjk(13), fill=FG, anchor='lm')
    x += d.textlength(item['src'], font=cjk(13)) + 6
    if item.get('ts'):
        age = fmt_ago(now - item['ts'])
        d.text((x, y + 7), age, font=mono(11), fill=DIM, anchor='lm')
        x += d.textlength(age, font=mono(11))
    return x


def render_card(items, idx, now, paused=False, status=None):
    """The top W x CARD_H part: header and the current headline."""
    img = Image.new('RGB', (W, CARD_H), BG)
    d = ImageDraw.Draw(img)
    clock = time.strftime('%H:%M', time.localtime(now))
    cw = d.textlength(clock, font=mono(12, True))
    d.text((W - 12 - cw, 12), clock, font=mono(12, True), fill=FG, anchor='lm')
    right, col = (status, WARN) if status else ('PAUSED', WARN) if paused else (
        (f'{idx + 1}/{len(items)}', DIM) if items else ('', DIM))
    d.text((W - 20 - cw - d.textlength(right, font=mono(11)), 12), right, font=mono(11),
           fill=col, anchor='lm')
    d.line((12, 24, W - 12, 24), fill=LINE)

    if not items:
        d.text((12, 46), 'No news yet', font=cjk(20, True), fill=FG)
        d.text((12, 78), 'Fetching feeds…' if status != 'offline' else 'Offline, retrying soon',
               font=cjk(14), fill=DIM)
        return img

    item = items[idx]
    draw_source(d, item, now)
    top, width = 30, W - 24
    bottom = 120 if item.get('summary') else CARD_H - 4
    for size in (22, 20, 18, 17):
        lh = round(size * 1.3)
        lines, cut = clip_lines(d, item['title'], cjk(size, True), width, (bottom - top) // lh)
        if not cut:
            break
    for i, line in enumerate(lines):
        d.text((12, top + i * lh), line, font=cjk(size, True), fill=FG)
    y = top + len(lines) * lh + 4
    if item.get('summary') and CARD_H - 4 - y >= 17:
        rows, _ = clip_lines(d, item['summary'], cjk(13), width, (CARD_H - 4 - y) // 17)
        for i, line in enumerate(rows):
            d.text((12, y + i * 17), line, font=cjk(13), fill=DIM)
    return img


def build_ticker(items, idx, count=6):
    """(label image, strip image) for the headlines after idx."""
    label = Image.new('RGB', (LABEL_W, BAND_H - 2), BAND)
    d = ImageDraw.Draw(label)
    d.rounded_rectangle((6, 3, LABEL_W - 4, BAND_H - 6), radius=4, fill=ACCENT)
    d.text(((LABEL_W + 2) / 2, (BAND_H - 3) / 2), 'NEXT', font=mono(10, True), fill=BG, anchor='mm')

    upcoming = [items[(idx + k) % len(items)] for k in range(1, min(count, len(items) - 1) + 1)]
    if not upcoming:
        return label, Image.new('RGB', (W - LABEL_W, BAND_H - 2), BAND)
    probe = ImageDraw.Draw(label)
    parts = []
    for it in upcoming:
        parts += [(it['short'] + '  ', cjk(13, True), tag_color(it)),
                  (it['title'], cjk(13), FG), ('   •   ', cjk(13), DIM)]
    width = int(sum(probe.textlength(t, font=f) for t, f, _ in parts)) + 1
    strip = Image.new('RGB', (max(width, W - LABEL_W), BAND_H - 2), BAND)
    d = ImageDraw.Draw(strip)
    x = 0
    for t, f, c in parts:
        d.text((x, (BAND_H - 2) / 2), t, font=f, fill=c, anchor='lm')
        x += d.textlength(t, font=f)
    return label, strip


def render_band(ticker, offset, progress, paused=False):
    """The bottom W x BAND_H part: progress bar and the scrolling ticker."""
    label, strip = ticker
    img = Image.new('RGB', (W, BAND_H), BAND)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, 1), fill=LINE)
    if progress > 0:
        d.rectangle((0, 0, int(W * min(1, progress)), 1), fill=DIM if paused else ACCENT)
    view = W - LABEL_W
    x = -(int(offset) % strip.width)
    while x < view:
        img.paste(strip, (LABEL_W + x, 2))
        x += strip.width
    img.paste(label, (0, 2))
    return img


def render_detail(item, now):
    """Full text of one item as a tall W-wide image (scrolled by the caller)."""
    probe = ImageDraw.Draw(Image.new('RGB', (1, 1)))
    width = W - 30
    title = wrap(probe, item['title'], cjk(18, True), width)
    body = wrap(probe, item.get('summary') or '(no summary in feed)', cjk(14), width)
    host = urlparse(item.get('link') or '').netloc
    total = 30 + len(title) * 24 + 6 + len(body) * 19 + 26
    img = Image.new('RGB', (W, max(H, total)), BG)
    d = ImageDraw.Draw(img)
    y = 30
    for line in title:
        d.text((12, y), line, font=cjk(18, True), fill=FG)
        y += 24
    y += 6
    for line in body:
        d.text((12, y), line, font=cjk(14), fill=FG if item.get('summary') else DIM)
        y += 19
    if host:
        d.text((12, y + 6), host, font=mono(11), fill=ACCENT)
    return img


def detail_frame(tall, scroll, item, now):
    img = tall.crop((0, scroll, W, scroll + H))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, 24), fill=BG)
    draw_source(d, item, now)
    hint = 'ESC back'
    d.text((W - 12 - d.textlength(hint, font=mono(11)), 12), hint, font=mono(11), fill=DIM, anchor='lm')
    d.line((12, 24, W - 12, 24), fill=LINE)
    if tall.height > H:
        bar = max(12, int((H - 28) * H / tall.height))
        y = 26 + int((H - 28 - bar) * scroll / (tall.height - H))
        d.rectangle((W - 4, y, W - 2, y + bar), fill=DIM)
    return img


def to_rgb565(img):
    import numpy as np
    a = np.asarray(img, dtype=np.uint16)
    v = ((a[..., 0] >> 3) << 11) | ((a[..., 1] >> 2) << 5) | (a[..., 2] >> 3)
    return v.astype('<u2').tobytes()


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


def read_keys(fd):
    """Key codes pressed since the last call, in order."""
    if fd is None:
        return []
    size = struct.calcsize('llHHi')
    try:
        buf = os.read(fd, size * 64)
    except BlockingIOError:
        return []
    pressed = []
    for i in range(0, len(buf) - size + 1, size):
        _, _, etype, code, value = struct.unpack_from('llHHi', buf, i)
        if etype == 1 and value == 1:
            pressed.append(code)
    return pressed


class Fetcher(threading.Thread):
    """Fetches the feeds of the current settings every POLL seconds, or when woken."""
    def __init__(self):
        super().__init__(daemon=True)
        self.wake = threading.Event()
        self.busy = False
        self.offline = False
        self.version = 0

    def run(self):
        cached = feeds.load_cache()
        chosen = feeds.selected_feeds(feeds.load_settings())
        age = time.time() - (cached or {}).get('fetched_at', 0)
        if cached and age < POLL and not feeds.missing(cached, chosen):
            self.wake.wait(POLL - age)
            self.wake.clear()
        while True:
            self.busy = True
            chosen = feeds.selected_feeds(feeds.load_settings())
            try:
                data = feeds.refresh(chosen, feeds.load_cache())
                self.offline = len(data['failed']) == len(chosen)
                feeds.save_cache(data)
            except Exception:
                self.offline = True
            self.busy = False
            self.version += 1
            self.wake.wait(POLL)
            self.wake.clear()


class App:
    def __init__(self, now, settings=None):
        self.settings = settings or feeds.load_settings()
        self.items = self.load_items()
        self.idx, self.paused, self.detail, self.panel = 0, False, None, None
        self.show(0, now)

    @property
    def interval(self):
        return self.settings['interval']

    def load_items(self):
        return feeds.items_for(feeds.load_cache(), feeds.selected_feeds(self.settings))

    def show(self, idx, now):
        self.idx = idx % len(self.items) if self.items else 0
        self.shown_at = now
        self.ticker = build_ticker(self.items, self.idx) if self.items else None
        self.card_key = None

    def reload(self, now):
        link = self.items[self.idx].get('link') if self.items else None
        self.items = self.load_items()
        links = [i.get('link') for i in self.items]
        self.show(links.index(link) if link in links else 0, now)

    def close_overlay(self, now):
        self.detail = self.panel = None
        self.shown_at, self.card_key = now, None

    def key(self, code, now, fetcher):
        """Handle one key; returns False to quit."""
        if self.panel:
            if self.panel.handle(code) == 'done':
                if self.panel.s != self.settings:
                    self.settings = self.panel.s
                    feeds.save_settings(self.settings)
                    self.reload(now)
                    fetcher.wake.set()
                self.close_overlay(now)
            return True
        if self.detail:
            tall, scroll = self.detail
            if code in (K_ESC, K_ENTER, K_BACKSPACE, K_Q):
                self.close_overlay(now)
            elif code in (K_UP, K_DOWN):
                step = -34 if code == K_UP else 34
                self.detail = (tall, max(0, min(tall.height - H, scroll + step)))
            return True
        if code in (K_ESC, K_Q):
            return False
        if code in (K_DOWN, K_RIGHT) and self.items:
            self.show(self.idx + 1, now)
        elif code in (K_UP, K_LEFT) and self.items:
            self.show(self.idx - 1, now)
        elif code == K_ENTER and self.items:
            self.detail = (render_detail(self.items[self.idx], now), 0)
        elif code == K_S:
            from settings_ui import Settings
            self.panel = Settings(self.settings)
        elif code in (K_P, K_SPACE):
            self.paused = not self.paused
            self.shown_at, self.card_key = now, None
        elif code == K_R and not fetcher.busy:
            fetcher.wake.set()
        return True

    def tick(self, now):
        if self.items and not (self.paused or self.detail or self.panel) \
                and now - self.shown_at >= self.interval:
            self.show(self.idx + 1, now)

    def overlay(self, now):
        """(key, render function) for the full-screen detail or settings view, else None."""
        if self.panel:
            return ('s', self.panel.version), self.panel.render
        if self.detail:
            item = self.items[self.idx]
            return ('d', id(self.detail[0]), self.detail[1]), \
                lambda: detail_frame(*self.detail, item, now)
        return None


def run():
    import signal
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    try:
        kbd = os.open(KBD, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        kbd = None
    fetcher = Fetcher()
    fetcher.start()
    app = App(time.time())
    version, overlay_key = 0, None
    with open(FB, 'r+b', buffering=0) as fb:
        try:
            while True:
                now = time.time()
                if fetcher.version != version:
                    version = fetcher.version
                    app.reload(now)
                if not all(app.key(c, now, fetcher) for c in read_keys(kbd)):
                    break
                app.tick(now)
                overlay = app.overlay(now)
                if overlay:
                    if overlay[0] != overlay_key:
                        fb.seek(0)
                        fb.write(to_rgb565(overlay[1]()))
                        overlay_key = overlay[0]
                    time.sleep(FRAME)
                    continue
                overlay_key = None
                status = 'updating' if fetcher.busy and not app.items else \
                    'offline' if fetcher.offline else None
                ckey = (app.idx, app.paused, status, len(app.items), int(now // 30))
                if ckey != app.card_key:
                    fb.seek(0)
                    fb.write(to_rgb565(render_card(app.items, app.idx, now, app.paused, status)))
                    app.card_key = ckey
                if app.ticker:
                    elapsed = now - app.shown_at
                    offset = 0 if app.paused else max(0, elapsed - TICKER_DELAY) * TICKER_SPEED
                    progress = 1 if app.paused else elapsed / app.interval
                    fb.seek(CARD_H * W * 2)
                    fb.write(to_rgb565(render_band(app.ticker, offset, progress, app.paused)))
                time.sleep(FRAME)
        except KeyboardInterrupt:
            pass
        finally:
            fb.seek(0)
            fb.write(b'\x00' * (W * H * 2))


PREVIEW_COMBOS = [('en-life', ['en'], ['life']), ('zh-life', ['zh'], ['life']),
                  ('en-general', ['en'], ['general']), ('zh-general', ['zh'], ['general']),
                  ('zh-tech', ['zh'], ['tech'])]


def preview(out, fetch_now=False):
    """Main, detail and settings frames for the saved settings, plus one main
    frame per language/category combination. --fetch refetches every feed."""
    from settings_ui import Settings
    cache = feeds.load_cache()
    if fetch_now or feeds.missing(cache, feeds.catalog()):
        cache = feeds.refresh(feeds.catalog(), cache)
        feeds.save_cache(cache)
    settings, now = feeds.load_settings(), time.time()
    stem = out.rsplit('.', 1)[0]

    def frame(items, idx, offset, progress, paused=False):
        img = Image.new('RGB', (W, H), BG)
        img.paste(render_card(items, idx, now, paused), (0, 0))
        img.paste(render_band(build_ticker(items, idx), offset, progress, paused), (0, CARD_H))
        return img

    items = feeds.items_for(cache, feeds.selected_feeds(settings))
    frame(items, 0, 140, 0.45).save(out)
    pick = next((i for i, it in enumerate(items) if len(it.get('summary', '')) > 150), 0)
    detail_frame(render_detail(items[pick], now), 0, items[pick], now).save(f'{stem}-detail.png')
    for name, langs, cats in PREVIEW_COMBOS:
        combo = dict(settings, languages=langs, categories=cats)
        got = feeds.items_for(cache, feeds.selected_feeds(combo))
        if got:
            frame(got, 0, 90, 0.3).save(f'{stem}-{name}.png')
    panel = Settings(dict(settings, languages=['en', 'zh'], categories=['tech', 'life']))
    panel.col, panel.row = 1, 1
    panel.render().save(f'{stem}-settings.png')
    panel.row = None
    panel.render().save(f'{stem}-settings-interval.png')


def main(argv):
    if '--fetch' in argv and '--preview' not in argv:
        chosen = feeds.selected_feeds(feeds.load_settings())
        data = feeds.refresh(chosen, feeds.load_cache())
        feeds.save_cache(data)
        for i in feeds.items_for(data, chosen):
            print(f"{i['short']:>9}  {fmt_ago(time.time() - (i['ts'] or 0)):>7}  {i['title']}")
        if data['failed']:
            print('failed:', ', '.join(data['failed']))
    elif '--make-icon' in argv:
        i = argv.index('--make-icon')
        make_icon(argv[i + 1], int(argv[i + 2]) if len(argv) > i + 2 else 100)
    elif '--preview' in argv:
        preview(argv[argv.index('--preview') + 1], '--fetch' in argv)
    else:
        run()


if __name__ == '__main__':
    main(sys.argv[1:])
