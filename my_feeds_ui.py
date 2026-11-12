"""My feeds: the user's own RSS feeds, opened from the settings screen.

A list with "+ Add RSS feed" on top. Adding asks for an address (a feed URL
or a website that links to its feed), checks it in the background with
feeds.probe and adds it under the feed's own title. DEL twice removes the
selected feed. Changes go into the settings dict passed in.
"""
import threading
from urllib.parse import urlparse

from PIL import Image, ImageDraw

import feeds
from keys import nav, to_char
from text import cjk, mono, ellipsize
from zero_news import (W, H, BG, FG, DIM, LINE, ACCENT, WARN, K_ESC, K_ENTER, K_SPACE,
                       K_BACKSPACE, K_UP, K_DOWN)

SEL = (30, 40, 58)
ROW_Y0, ROW_H, VISIBLE = 30, 22, 5
MAX_INPUT = 120


def _spawn(fn):
    threading.Thread(target=fn, daemon=True).start()


class MyFeeds:
    def __init__(self, settings, probe=feeds.probe, spawn=_spawn):
        self.s = settings
        self.probe, self.spawn = probe, spawn
        self.state = 'list'        # 'list', 'input' or 'checking'
        self.row, self.top = 0, 0  # row 0 = "+ Add RSS feed"
        self.text = ''
        self.message = None        # (text, color) shown in the footer
        self.confirm = None        # row waiting for a second DEL
        self.token = 0             # bumped to ignore a probe that was cancelled
        self.version = 0

    @property
    def mine(self):
        return self.s['my_feeds']

    def handle(self, code):
        """Process one raw key code; returns 'done' when the screen should close."""
        if self.state == 'list':
            result = self._list_key(code)
        elif self.state == 'input':
            self._input_key(code)
            result = None
        else:  # checking: only ESC (cancel) does anything
            if nav(code) == K_ESC:
                self.token += 1
                self.state, self.message = 'input', None
            result = None
        self.version += 1
        return result

    def _list_key(self, code):
        code = nav(code)
        confirm, self.confirm = self.confirm, None
        if code == K_ESC:
            return 'done'
        self.message = None
        if code == K_UP:
            self.row = max(0, self.row - 1)
        elif code == K_DOWN:
            self.row = min(len(self.mine), self.row + 1)
        elif code in (K_ENTER, K_SPACE) and self.row == 0:
            if len(self.mine) >= feeds.MAX_MY_FEEDS:
                self.message = (f'List is full ({feeds.MAX_MY_FEEDS} feeds)', WARN)
            else:
                self.state, self.text = 'input', ''
        elif code == K_BACKSPACE and self.row > 0:
            if confirm == self.row:
                gone = self.mine[self.row - 1]
                self.s['my_feeds'] = [f for f in self.mine if f is not gone]
                self.row = min(self.row, len(self.mine))
                self.message = (f"Removed {gone['name']}", DIM)
            else:
                self.confirm = self.row
                self.message = ('Press DEL again to remove', WARN)
        self.top = min(max(self.top, self.row - VISIBLE + 1), self.row)
        return None

    def _input_key(self, code):
        if nav(code) == K_ESC:
            self.state, self.message = 'list', None
        elif code == K_BACKSPACE:
            self.text = self.text[:-1]
        elif code == K_ENTER:
            if self.text.strip():
                self._check(self.text.strip())
        else:
            c = to_char(code)
            if c and len(self.text) < MAX_INPUT:
                self.text += c
                self.message = None

    def _check(self, url):
        self.token += 1
        token = self.token
        self.state, self.message = 'checking', ('Checking feed…', ACCENT)

        def work():
            try:
                feed, error = self.probe(url), None
            except ValueError as e:
                feed, error = None, str(e)
            except Exception:
                feed, error = None, 'Could not load this address'
            if token != self.token:
                return
            if feed and any(f['url'] == feed['url'] for f in self.mine):
                feed, error = None, 'Already in My feeds'
            if feed:
                self.s['my_feeds'] = self.mine + [feed]
                self.state, self.row = 'list', len(self.mine)
                self.top = max(0, self.row - VISIBLE + 1)
                self.message = (f"Added {feed['name']}", ACCENT)
            else:
                self.state, self.message = 'input', (error, WARN)
            self.version += 1

        self.spawn(work)

    def render(self):
        img = Image.new('RGB', (W, H), BG)
        d = ImageDraw.Draw(img)
        adding = self.state != 'list'
        d.text((12, 12), 'Add RSS feed' if adding else 'My feeds', font=cjk(15, True),
               fill=FG, anchor='lm')
        hint = 'ESC cancel' if adding else 'ESC back'
        d.text((W - 12 - d.textlength(hint, font=mono(11)), 12), hint, font=mono(11),
               fill=DIM, anchor='lm')
        d.line((12, 24, W - 12, 24), fill=LINE)
        if adding:
            self._render_input(d)
            keys = 'OK add  DEL erase'
        else:
            self._render_list(d)
            keys = 'OK add' if self.row == 0 else 'DEL remove'
        d.line((12, 144, W - 12, 144), fill=LINE)
        text, color = self.message or (f'{len(self.mine)} of {feeds.MAX_MY_FEEDS} feeds', DIM)
        kw = d.textlength(keys, font=mono(10))
        d.text((W - 12 - kw, 157), keys, font=mono(10), fill=DIM, anchor='lm')
        d.text((12, 157), self._fit(d, text, cjk(12), W - 36 - kw), font=cjk(12), fill=color,
               anchor='lm')
        return img

    def _render_list(self, d):
        rows = [None] + self.mine
        for i in range(self.top, min(len(rows), self.top + VISIBLE)):
            y = ROW_Y0 + (i - self.top) * ROW_H
            if i == self.row:
                d.rounded_rectangle((7, y, W - 7, y + ROW_H - 2), radius=5, fill=SEL,
                                    outline=WARN if self.confirm == i else ACCENT)
            if i == 0:
                d.text((13, y + 10), '+ Add RSS feed', font=cjk(14, True), fill=ACCENT, anchor='lm')
                continue
            f = rows[i]
            host = urlparse(f['url']).netloc.removeprefix('www.')
            hw = min(d.textlength(host, font=mono(10)), 120)
            d.text((W - 13 - hw, y + 10), self._fit(d, host, mono(10), 120), font=mono(10),
                   fill=DIM, anchor='lm')
            d.text((13, y + 10), self._fit(d, f['name'], cjk(14), W - 40 - hw), font=cjk(14),
                   fill=FG, anchor='lm')
        if len(rows) > VISIBLE:
            bar = int(VISIBLE * ROW_H * VISIBLE / len(rows))
            y = ROW_Y0 + int((VISIBLE * ROW_H - bar) * self.top / (len(rows) - VISIBLE))
            d.rectangle((W - 4, y, W - 2, y + bar), fill=DIM)

    def _render_input(self, d):
        d.text((12, 40), 'Feed or website address', font=cjk(12), fill=DIM, anchor='lm')
        box = (8, 52, W - 8, 80)
        d.rounded_rectangle(box, radius=5, fill=SEL, outline=ACCENT)
        f = mono(13)
        shown = self.text
        while shown and d.textlength(shown, font=f) > W - 34:
            shown = shown[1:]
        d.text((16, 66), shown, font=f, fill=FG, anchor='lm')
        if self.state == 'input':
            x = 16 + d.textlength(shown, font=f) + 1
            d.rectangle((x, 58, x + 7, 74), fill=ACCENT)
        d.text((12, 96), 'e.g.  hnrss.org/newest   theverge.com', font=mono(11), fill=DIM,
               anchor='lm')
        d.text((12, 114), 'https:// is added for you.', font=cjk(12), fill=DIM, anchor='lm')
        d.text((12, 130), 'A website works if it links to its RSS feed.', font=cjk(12),
               fill=DIM, anchor='lm')

    @staticmethod
    def _fit(d, text, font, width):
        return text if d.textlength(text, font=font) <= width else ellipsize(d, text, font, width)
