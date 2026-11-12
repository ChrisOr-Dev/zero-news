"""Settings screen for News: languages, categories, rotation speed and My feeds.

Two columns of checkboxes (Language, Category), then a rotation row and a
My feeds row (opens my_feeds_ui). Arrows move, ENTER/SPACE toggles (or cycles
the interval), ESC saves and closes. At least one language and one category
always stay checked.
"""
from PIL import Image, ImageDraw

import feeds
from keys import nav
from text import cjk, mono
from zero_news import (W, H, BG, FG, DIM, LINE, ACCENT, K_ESC, K_ENTER, K_SPACE, K_UP,
                       K_DOWN, K_LEFT, K_RIGHT, K_S)

SEL = (30, 40, 58)
COL_X = (12, 166)
ROW_Y0, ROW_H = 42, 20
INTERVAL_Y, MINE_Y = 103, 124
# (setting key, value, label); column = 0 for languages, 1 for categories
COLUMNS = [
    [('languages', 'en', 'English'), ('languages', 'zh', '中文')],
    [('categories', 'tech', '科技 Tech'), ('categories', 'life', '生活 Life'),
     ('categories', 'general', '綜合 General')],
]
INTERVAL, MINE = 'interval', 'mine'   # rows below the columns


class Settings:
    def __init__(self, settings):
        self.s = {k: (list(v) if isinstance(v, list) else v) for k, v in settings.items()}
        self.col, self.row = 0, 0   # row None = the interval row, MINE = My feeds
        self.sub = None             # the open My feeds screen
        self.changes = 0

    @property
    def version(self):
        """Changes whenever the screen needs redrawing."""
        return self.changes, id(self.sub), self.sub and self.sub.version

    @property
    def focus(self):
        return INTERVAL if self.row is None else MINE if self.row == MINE else \
            COLUMNS[self.col][self.row]

    def handle(self, code):
        """Process one key; returns 'done' when the screen should close."""
        if self.sub:  # gets raw codes for its text input
            if self.sub.handle(code) == 'done':
                self.sub = None
                self.changes += 1
            return None
        code = nav(code)
        if code in (K_ESC, K_S):
            return 'done'
        if code == K_UP:
            if self.row == MINE:
                self.row = None
            elif self.row is None:
                self.row = len(COLUMNS[self.col]) - 1
            elif self.row > 0:
                self.row -= 1
        elif code == K_DOWN:
            if self.row is None:
                self.row = MINE
            elif self.row != MINE:
                self.row = self.row + 1 if self.row + 1 < len(COLUMNS[self.col]) else None
        elif code in (K_LEFT, K_RIGHT):
            if self.row is None:
                self.step_interval(1 if code == K_RIGHT else -1)
            elif self.row != MINE:
                self.col = 1 if code == K_RIGHT else 0
                self.row = min(self.row, len(COLUMNS[self.col]) - 1)
        elif code in (K_ENTER, K_SPACE):
            if self.row is None:
                self.step_interval(1, wrap=True)
            elif self.row == MINE:
                from my_feeds_ui import MyFeeds
                self.sub = MyFeeds(self.s)
            else:
                self.toggle(*self.focus[:2])
        else:
            return None
        self.changes += 1
        return None

    def toggle(self, key, value):
        chosen = self.s[key]
        if value in chosen:
            if len(chosen) > 1:
                chosen.remove(value)
        else:
            order = feeds.LANGUAGES if key == 'languages' else feeds.CATEGORIES
            self.s[key] = [v for v in order if v in chosen or v == value]

    def step_interval(self, step, wrap=False):
        opts = feeds.INTERVALS
        i = opts.index(self.s['interval']) if self.s['interval'] in opts else 1
        i = (i + step) % len(opts) if wrap else max(0, min(len(opts) - 1, i + step))
        self.s['interval'] = opts[i]

    def render(self):
        if self.sub:
            return self.sub.render()
        img = Image.new('RGB', (W, H), BG)
        d = ImageDraw.Draw(img)
        d.text((12, 12), 'Settings', font=cjk(15, True), fill=FG, anchor='lm')
        hint = 'ESC save'
        d.text((W - 12 - d.textlength(hint, font=mono(11)), 12), hint, font=mono(11),
               fill=DIM, anchor='lm')
        d.line((12, 24, W - 12, 24), fill=LINE)

        for c, (x, title) in enumerate(zip(COL_X, ('LANGUAGE', 'CATEGORY'))):
            d.text((x, 33), title, font=mono(10, True), fill=DIM, anchor='lm')
            for r, (key, value, label) in enumerate(COLUMNS[c]):
                y = ROW_Y0 + r * ROW_H
                if (c, r) == (self.col, self.row):
                    d.rounded_rectangle((x - 5, y, x + 142, y + ROW_H - 2), radius=5,
                                        fill=SEL, outline=ACCENT)
                self._checkbox(d, x + 1, y + 3, value in self.s[key])
                d.text((x + 21, y + 9), label, font=cjk(14), fill=FG, anchor='lm')

        n_mine = len(self.s['my_feeds'])
        for y, row, label, val in (
                (INTERVAL_Y, None, 'Rotate every', f"‹ {self.s['interval']}s ›"),
                (MINE_Y, MINE, 'My feeds', f'{n_mine} ›' if n_mine else 'add RSS ›')):
            if self.row == row:
                d.rounded_rectangle((7, y, W - 7, y + ROW_H - 2), radius=5, fill=SEL,
                                    outline=ACCENT)
            d.text((13, y + 9), label, font=cjk(14), fill=FG, anchor='lm')
            d.text((W - 13 - d.textlength(val, font=mono(13, True)), y + 9), val,
                   font=mono(13, True), fill=ACCENT if self.row == row else FG, anchor='lm')

        d.line((12, 146, W - 12, 146), fill=LINE)
        n = len(feeds.selected_feeds(self.s))
        d.text((12, 158), f'{n} sources', font=mono(11), fill=DIM, anchor='lm')
        keys = 'F/Z/X/C move  OK select'
        d.text((W - 12 - d.textlength(keys, font=mono(10)), 158), keys, font=mono(10),
               fill=DIM, anchor='lm')
        return img

    @staticmethod
    def _checkbox(d, x, y, on):
        if on:
            d.rounded_rectangle((x, y, x + 12, y + 12), radius=3, fill=ACCENT)
            d.line((x + 3, y + 6, x + 5, y + 9, x + 10, y + 3), fill=BG, width=2)
        else:
            d.rounded_rectangle((x, y, x + 12, y + 12), radius=3, outline=DIM, width=1)
