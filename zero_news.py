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
Keys: F/X previous/next, ENTER details (up/down a line, left/right a page), S settings, P pause, R refresh, hold ESC 3s to exit.
"""
import os
import sys
import threading
import time
from urllib.parse import urlparse

from PIL import Image, ImageDraw

import article
import feeds
import keys
import net
from keys import (K_ESC, K_BACKSPACE, K_Q, K_R, K_P, K_ENTER, K_S, K_SPACE, K_UP, K_LEFT,
                  K_RIGHT, K_DOWN, read_keys, nav)
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
HOLD_EXIT = 3.0       # seconds ESC must be held to quit
ESC_GRACE = 0.4       # a release shorter than this (key bounce) still counts as held

BG = (12, 15, 22)
FG = (240, 242, 246)
DIM = (140, 150, 166)
LINE = (38, 46, 60)
BAND = (24, 30, 42)
ACCENT = (110, 180, 255)
WARN = (232, 178, 70)
EXIT = (200, 64, 56)
TAG_COLORS = {'TECH': (64, 200, 190), 'LIFE': (236, 112, 170), 'NEWS': (240, 150, 70),
              'MY': (170, 140, 255)}

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
        draw_hints(d, CARD_H - 22, (('S', 'settings'), ('R', 'refresh'), ('ESC', 'hold to exit')))
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


def draw_hints(d, y, hints):
    """Key caps with labels in one row starting at (8, y), 20px tall."""
    x = 8
    for key, label in hints:
        kw = d.textlength(key, font=mono(10, True)) + 8
        d.rounded_rectangle((x, y + 3, x + kw, y + 17), radius=3, fill=DIM)
        d.text((x + kw / 2, y + 10), key, font=mono(10, True), fill=BG, anchor='mm')
        x += kw + 4
        d.text((x, y + 10), label, font=cjk(12), fill=FG, anchor='lm')
        x += d.textlength(label, font=cjk(12)) + 9


def render_band(ticker, offset, progress, paused=False, hints=False):
    """The bottom W x BAND_H part: progress bar, then the scrolling ticker or,
    when hints is set, the main keys."""
    label, strip = ticker
    img = Image.new('RGB', (W, BAND_H), BAND)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, 1), fill=LINE)
    if progress > 0:
        d.rectangle((0, 0, int(W * min(1, progress)), 1), fill=DIM if paused else ACCENT)
    if hints:
        draw_hints(d, 3, (('↑ ↓', 'prev/next'), ('OK', 'read'),
                          ('P', 'resume' if paused else 'pause'), ('S', 'settings')))
        return img
    view = W - LABEL_W
    x = -(int(offset) % strip.width)
    while x < view:
        img.paste(strip, (LABEL_W + x, 2))
        x += strip.width
    img.paste(label, (0, 2))
    return img


def render_exit(progress):
    """The band while ESC is held: a bar filling over HOLD_EXIT seconds."""
    img = Image.new('RGB', (W, BAND_H), BAND)
    d = ImageDraw.Draw(img)
    if progress > 0:
        d.rectangle((0, 0, int(W * min(1, progress)), BAND_H), fill=EXIT)
    left = max(0.0, HOLD_EXIT * (1 - progress))
    d.text((W / 2, BAND_H / 2), f'Keep holding ESC to exit  {left:.1f}s', font=mono(11, True),
           fill=FG, anchor='mm')
    return img


class Detail:
    """One story laid out for the details view. Only the lines in view are drawn,
    so long articles open and scroll fast."""

    def __init__(self, item):
        self.item, self.scroll, self.version = item, 0, 0
        self.text = item.get('body') or item.get('summary') or ''
        self.loading = False
        self.full = None  # article text from the web page, set by the fetch thread
        self.layout()

    def layout(self):
        probe = ImageDraw.Draw(Image.new('RGB', (1, 1)))
        width = W - 30
        self.lines, y = [], 30
        for line in wrap(probe, self.item['title'], cjk(18, True), width):
            self.lines.append((y, line, cjk(18, True), FG))
            y += 24
        y += 6
        fill = FG if self.text else DIM
        for para in (self.text or '(no summary in feed)').split('\n'):
            for line in wrap(probe, para, cjk(14), width):
                self.lines.append((y, line, cjk(14), fill))
                y += 19
            y += 8
        self.host_y = y
        self.height = max(H, y + 26)
        self.scroll = min(self.scroll, self.height - H)
        self.version += 1

    def update(self):
        """Take the fetched article (main thread: fonts are not shared across threads)."""
        if self.loading and self.full is not None:
            self.loading = False
            if len(self.full) > len(self.text):
                self.text = self.full
                self.layout()
            else:
                self.version += 1

    def move(self, step):
        self.scroll = max(0, min(self.height - H, self.scroll + step))

    def frame(self, now):
        img = Image.new('RGB', (W, H), BG)
        d = ImageDraw.Draw(img)
        for y, line, font, fill in self.lines:
            y -= self.scroll
            if 0 <= y + 24 and y < H:
                d.text((12, y), line, font=font, fill=fill)
        host = urlparse(self.item.get('link') or '').netloc
        if host and self.host_y - self.scroll < H:
            d.text((12, self.host_y + 6 - self.scroll), host, font=mono(11), fill=ACCENT)
        d.rectangle((0, 0, W, 24), fill=BG)
        used = draw_source(d, self.item, now)
        # Longest hint that fits beside the source name and age.
        hints = ('loading full text', 'loading…') if self.loading else \
            ('↑ ↓ line  ← → page', '↑ ↓  ← →')
        hint = next((h for h in hints if W - 12 - d.textlength(h, font=mono(11)) >= used + 10),
                    hints[-1])
        d.text((W - 12 - d.textlength(hint, font=mono(11)), 12), hint, font=mono(11),
               fill=ACCENT if self.loading else DIM, anchor='lm')
        d.line((12, 24, W - 12, 24), fill=LINE)
        if self.height > H:
            bar = max(12, int((H - 28) * H / self.height))
            y = 26 + int((H - 28 - bar) * self.scroll / (self.height - H))
            d.rectangle((W - 4, y, W - 2, y + bar), fill=DIM)
        return img


ARTICLES = {}  # link -> fetched article text ('' if the page had none), oldest first
ARTICLE_CACHE = 30


def fetch_article(det, link):
    """Thread: fetch the story's web page and hand its text to the details view."""
    try:
        text = article.fetch(link)
    except Exception:
        text = ''
    ARTICLES[link] = text
    while len(ARTICLES) > ARTICLE_CACHE:
        ARTICLES.pop(next(iter(ARTICLES)))
    det.full = text


def to_rgb565(img):
    import numpy as np
    a = np.asarray(img, dtype=np.uint16)
    v = ((a[..., 0] >> 3) << 11) | ((a[..., 1] >> 2) << 5) | (a[..., 2] >> 3)
    return v.astype('<u2').tobytes()


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
        self.esc_at = self.esc_up = None  # ESC went down (main view) / was let go
        self.show(0, now)
        self.hinting = True  # key hints replace the ticker until the first change of story

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
        self.hinting = False

    def reload(self, now):
        link = self.items[self.idx].get('link') if self.items else None
        self.items = self.load_items()
        links = [i.get('link') for i in self.items]
        hinting = self.hinting
        self.show(links.index(link) if link in links else 0, now)
        self.hinting = hinting

    def close_overlay(self, now):
        self.detail = self.panel = None
        self.shown_at, self.card_key = now, None

    def key(self, code, now, fetcher):
        """Handle one key; returns False to quit."""
        if self.panel:  # gets raw codes: My feeds has text input
            if self.panel.handle(code) == 'done':
                if self.panel.s != self.settings:
                    self.settings = self.panel.s
                    feeds.save_settings(self.settings)
                    self.reload(now)
                    fetcher.wake.set()
                self.close_overlay(now)
            return True
        code = nav(code)
        if self.detail:
            if code in (K_ESC, K_ENTER, K_BACKSPACE, K_Q):
                self.close_overlay(now)
            elif code in (K_UP, K_DOWN, K_LEFT, K_RIGHT):
                self.detail.move({K_UP: -34, K_DOWN: 34, K_LEFT: -(H - 50), K_RIGHT: H - 50}[code])
            return True
        if code == K_ESC:
            self.esc_at = self.esc_at if self.esc_at is not None else now
            self.esc_up = None
        elif code in (K_DOWN, K_RIGHT) and self.items:
            self.show(self.idx + 1, now)
        elif code in (K_UP, K_LEFT) and self.items:
            self.show(self.idx - 1, now)
        elif code == K_ENTER and self.items:
            self.open_detail()
        elif code == K_S:
            from settings_ui import Settings
            self.panel = Settings(self.settings)
        elif code in (K_P, K_SPACE):
            self.paused = not self.paused
            self.shown_at, self.card_key = now, None
        elif code == K_R and not fetcher.busy:
            fetcher.wake.set()
        return True

    def open_detail(self):
        """Details view with the feed's text now and the web page's text when fetched."""
        item = self.items[self.idx]
        det = self.detail = Detail(item)
        link = item.get('link')
        if link in ARTICLES:
            det.loading, det.full = True, ARTICLES[link]
            det.update()
        elif link:
            det.loading = True
            threading.Thread(target=fetch_article, args=(det, link), daemon=True).start()

    def exit_progress(self, now):
        """0..1 while ESC is held in the main view (quit at 1), else None."""
        if keys.is_down(K_ESC):
            self.esc_up = None
        elif self.esc_up is None:
            self.esc_up = now
        if self.esc_at is None or self.panel or self.detail or \
                (self.esc_up is not None and now - self.esc_up > ESC_GRACE):
            self.esc_at = None
            return None
        return (now - self.esc_at) / HOLD_EXIT

    def tick(self, now):
        if self.items and not (self.paused or self.detail or self.panel) \
                and now - self.shown_at >= self.interval:
            self.show(self.idx + 1, now)

    def overlay(self, now):
        """(key, render function) for the full-screen detail or settings view, else None."""
        if self.panel:
            return ('s', self.panel.version), self.panel.render
        if self.detail:
            det = self.detail
            det.update()
            return ('d', id(det), det.version, det.scroll, det.loading), lambda: det.frame(now)
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
                hold = app.exit_progress(now)
                if hold is not None and hold >= 1:
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
                if hold is not None:
                    fb.seek(CARD_H * W * 2)
                    fb.write(to_rgb565(render_exit(hold)))
                elif app.ticker:
                    elapsed = now - app.shown_at
                    offset = 0 if app.paused else max(0, elapsed - TICKER_DELAY) * TICKER_SPEED
                    progress = 1 if app.paused else elapsed / app.interval
                    fb.seek(CARD_H * W * 2)
                    fb.write(to_rgb565(render_band(app.ticker, offset, progress, app.paused,
                                                   app.paused or app.hinting)))
                time.sleep(FRAME)
        except KeyboardInterrupt:
            pass
        finally:
            fb.seek(0)
            fb.write(b'\x00' * (W * H * 2))


def main(argv):
    net.prefer_ipv4()
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
        from icon import make_icon
        make_icon(argv[i + 1], int(argv[i + 2]) if len(argv) > i + 2 else 100)
    elif '--preview' in argv:
        from preview import preview
        preview(argv[argv.index('--preview') + 1], '--fetch' in argv)
    else:
        run()


if __name__ == '__main__':
    main(sys.argv[1:])
