import unittest
from unittest import mock

from PIL import Image, ImageDraw

import feeds
import zero_news as zn

FEED = {'name': 'Test', 'short': 'T', 'tag': 'TECH', 'url': 'u'}

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>
<item><title>Older &amp; wiser</title><link>https://a.example/1</link>
<description>&lt;p&gt;Some &lt;b&gt;bold&lt;/b&gt; text&lt;/p&gt;</description>
<pubDate>Thu, 12 Nov 2026 10:00:00 GMT</pubDate></item>
<item><title>Newer</title><link>https://a.example/2</link>
<description>&lt;p&gt;Article URL: https://x Comments URL: https://y&lt;/p&gt;</description>
<pubDate>Thu, 12 Nov 2026 12:00:00 +0000</pubDate></item>
<item><title></title><link>https://a.example/3</link></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title type="html">Atom &lt;em&gt;entry&lt;/em&gt;</title>
<link rel="alternate" type="text/html" href="https://b.example/post"/>
<summary type="html">Hello &lt;a href="#"&gt;world&lt;/a&gt;</summary>
<updated>2026-11-12T10:19:58-05:00</updated></entry></feed>"""


def item(title, ts, short='T', link=None, summary=''):
    return {'title': title, 'ts': ts, 'short': short, 'src': short, 'tag': 'TECH',
            'link': link or f'https://x/{title}', 'summary': summary}


class FeedTests(unittest.TestCase):
    def test_rss(self):
        items = feeds.parse_feed(RSS, FEED)
        self.assertEqual([i['title'] for i in items], ['Newer', 'Older & wiser'])
        self.assertEqual(items[0]['summary'], '')  # hnrss link-only description dropped
        self.assertEqual(items[1]['summary'], 'Some bold text')
        self.assertEqual(items[1]['link'], 'https://a.example/1')
        self.assertEqual(items[0]['ts'] - items[1]['ts'], 7200)

    def test_atom(self):
        [e] = feeds.parse_feed(ATOM, FEED)
        self.assertEqual(e['title'], 'Atom entry')
        self.assertEqual(e['summary'], 'Hello world')
        self.assertEqual(e['link'], 'https://b.example/post')
        self.assertEqual(e['ts'], feeds.parse_date('Thu, 12 Nov 2026 15:19:58 GMT'))

    def test_clean_text_quotes_and_future_dates(self):
        self.assertEqual(feeds.clean_text('Nvidia’s ‘hard’ year — so…'), "Nvidia's 'hard' year - so...")
        self.assertEqual(feeds.clean_text('他說「好」—“對”'), '他說「好」—“對”')  # CJK text untouched
        noon = feeds.parse_date('Thu, 12 Nov 2026 12:00:00 GMT')
        with mock.patch.object(feeds.time, 'time', return_value=noon - 1800):  # 30 min skew
            self.assertEqual(feeds.parse_feed(RSS, FEED)[0]['ts'], noon - 1800)
        with mock.patch.object(feeds.time, 'time', return_value=noon - 7200):  # scheduled post
            items = feeds.parse_feed(RSS, FEED)
        self.assertEqual(items[-1]['title'], 'Newer')
        self.assertIsNone(items[-1]['ts'])

    def test_long_summary_gets_ellipsis(self):
        raw = RSS.replace(b'Some &lt;b&gt;bold&lt;/b&gt; text', b'z' * 900)
        s = feeds.parse_feed(raw, FEED)[1]['summary']
        self.assertEqual(len(s), feeds.SUMMARY_MAX)
        self.assertTrue(s.endswith('…'))

    def test_naive_date_uses_feed_offset(self):
        self.assertEqual(feeds.parse_date('2026-11-13  08:11:00', 8),
                         feeds.parse_date('Fri, 13 Nov 2026 00:11:00 GMT'))
        self.assertIsNone(feeds.parse_date('soon'))

    def test_merge_caps_dedupes_and_sorts(self):
        a = [item(f'a{n}', 100 - n) for n in range(20)]
        b = [item('A0', 500), item('b', 50)]
        out = feeds.merge([a, b])
        self.assertEqual(out[0]['title'], 'A0')
        self.assertNotIn('a0', [i['title'] for i in out])  # duplicate title, case-insensitive
        self.assertEqual(sum(i['title'].startswith('a') for i in out), feeds.PER_FEED - 1)
        self.assertEqual([i['ts'] for i in out], sorted((i['ts'] for i in out), reverse=True))

    def test_refresh_keeps_cached_items_of_failed_feed(self):
        ok = dict(FEED, name='ok', url='ok')
        bad = dict(FEED, name='bad', url='bad')
        cache = {'feeds': {'bad': [item('old', 1)]}}

        def fetch(f):
            if f['url'] == 'bad':
                raise OSError('down')
            return [item('fresh', 2)]
        cache['feeds']['other'] = [item('kept', 3)]  # feed of another setting
        data = feeds.refresh([ok, bad], cache, fetch)
        self.assertEqual(data['failed'], ['bad'])
        self.assertEqual([i['title'] for i in feeds.items_for(data, [ok, bad])], ['fresh', 'old'])
        self.assertEqual(data['feeds']['other'][0]['title'], 'kept')
        self.assertFalse(feeds.missing(data, [ok, bad]))
        self.assertTrue(feeds.missing(data, [dict(FEED, url='new')]))

    def test_tag_comes_from_category(self):
        self.assertEqual(feeds.parse_feed(RSS, dict(FEED, cat='life'))[0]['tag'], 'LIFE')
        self.assertEqual(feeds.parse_feed(RSS, FEED)[0]['tag'], 'NEWS')

    def test_selected_feeds(self):
        pick = feeds.selected_feeds({'languages': ['zh'], 'categories': ['life', 'tech']})
        self.assertTrue(pick)
        self.assertEqual({(f['lang'], f['cat']) for f in pick}, {('zh', 'life'), ('zh', 'tech')})
        for lang in feeds.LANGUAGES:  # every combination has sources
            for cat in feeds.CATEGORIES:
                self.assertGreaterEqual(
                    len(feeds.selected_feeds({'languages': [lang], 'categories': [cat]})), 4)
        bare = [{'name': 'x', 'url': 'u'}]  # custom feed without lang/cat matches anything
        self.assertEqual(feeds.selected_feeds({'languages': ['zh'], 'categories': ['life']}, bare), bare)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.dir = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(feeds, 'CONFIG', self.dir.name + '/cfg/zero-news.json')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.dir.cleanup)

    def write(self, text):
        import os
        os.makedirs(os.path.dirname(feeds.CONFIG), exist_ok=True)
        with open(feeds.CONFIG, 'w') as f:
            f.write(text)

    def test_defaults_without_file(self):
        self.assertEqual(feeds.load_settings(), feeds.DEFAULT_SETTINGS)
        self.assertEqual(len(feeds.catalog()), len(feeds.CATALOG))

    def test_invalid_values_fall_back(self):
        self.write('{"languages": ["fr"], "categories": ["life", "x"], "interval": 7}')
        s = feeds.load_settings()
        self.assertEqual(s['languages'], feeds.DEFAULT_SETTINGS['languages'])
        self.assertEqual(s['categories'], ['life'])
        self.assertEqual(s['interval'], feeds.DEFAULT_SETTINGS['interval'])
        self.write('[1, 2]')
        self.assertEqual(feeds.load_settings(), feeds.DEFAULT_SETTINGS)

    def test_save_keeps_custom_feeds(self):
        self.write('{"feeds": [{"name": "Mine", "url": "https://m"}, {"name": "no url"}]}')
        feeds.save_settings({'languages': ['zh'], 'categories': ['tech'], 'interval': 30})
        self.assertEqual(feeds.load_settings(),
                         {'languages': ['zh'], 'categories': ['tech'], 'interval': 30})
        self.assertEqual(feeds.catalog(), [{'name': 'Mine', 'url': 'https://m', 'short': 'Mine'}])


class TextTests(unittest.TestCase):
    def setUp(self):
        self.d = ImageDraw.Draw(Image.new('RGB', (1, 1)))
        self.f = zn.cjk(16)

    def test_wrap_fits_and_keeps_words(self):
        text = 'Google pushes privacy angle as Gemini-powered smartglasses debut worldwide'
        lines = zn.wrap(self.d, text, self.f, 150)
        self.assertGreater(len(lines), 1)
        self.assertEqual(' '.join(lines), text)
        for line in lines:
            self.assertLessEqual(self.d.textlength(line, font=self.f), 150)

    def test_wrap_breaks_cjk_and_long_tokens(self):
        text = '黃仁勳正式辭任NVIDIA CEO即時生效' + 'x' * 80
        lines = zn.wrap(self.d, text, self.f, 120)
        self.assertEqual(''.join(lines).replace(' ', ''), text.replace(' ', ''))
        for line in lines:
            self.assertLessEqual(self.d.textlength(line, font=self.f), 120)

    def test_clip_lines_adds_ellipsis(self):
        lines, cut = zn.clip_lines(self.d, 'word ' * 60, self.f, 150, 2)
        self.assertTrue(cut)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[-1].endswith('…'))
        self.assertEqual(zn.clip_lines(self.d, 'short', self.f, 150, 2), (['short'], False))

    def test_fmt_ago(self):
        self.assertEqual([zn.fmt_ago(s) for s in (5, 300, 7200, 200000, -50)],
                         ['now', '5m ago', '2h ago', '2d ago', 'now'])


class AppTests(unittest.TestCase):
    def make(self, n=3):
        items = [item(f'h{k}', 1000 - k, summary='s' * 200 if k == 1 else '') for k in range(n)]
        settings = {'languages': ['en'], 'categories': ['tech'], 'interval': 10}
        with mock.patch.object(zn.App, 'load_items', return_value=items):
            return zn.App(0, settings)

    def test_auto_advance_wraps_and_pauses(self):
        app = self.make()
        app.tick(9)
        self.assertEqual(app.idx, 0)
        app.tick(10)
        self.assertEqual(app.idx, 1)
        app.paused = True
        app.tick(100)
        self.assertEqual(app.idx, 1)
        app.paused = False
        app.tick(100)
        app.tick(110)
        self.assertEqual(app.idx, 0)

    def test_keys(self):
        app, f = self.make(), mock.Mock(busy=False)
        app.key(zn.K_UP, 0, f)
        self.assertEqual(app.idx, 2)
        app.key(zn.K_DOWN, 0, f)
        app.key(zn.K_DOWN, 0, f)
        self.assertEqual(app.idx, 1)
        app.key(zn.K_ENTER, 0, f)
        self.assertIsNotNone(app.detail)
        app.tick(1000)  # no auto-advance while reading
        self.assertEqual(app.idx, 1)
        app.key(zn.K_ESC, 0, f)
        self.assertIsNone(app.detail)
        app.key(zn.K_R, 0, f)
        f.wake.set.assert_called_once()
        self.assertFalse(app.key(zn.K_ESC, 0, f))

    def test_reload_keeps_current_story(self):
        app = self.make()
        app.show(2, 0)
        fresh = [item('new', 2000)] + app.items
        with mock.patch.object(zn.App, 'load_items', return_value=fresh):
            app.reload(5)
        self.assertEqual(app.items[app.idx]['title'], 'h2')

    def test_settings_change_saves_reloads_and_fetches(self):
        app, f = self.make(), mock.Mock(busy=False)
        app.key(zn.K_S, 0, f)
        self.assertIsNotNone(app.panel)
        app.tick(1000)  # no auto-advance in settings
        self.assertEqual(app.idx, 0)
        app.key(zn.K_DOWN, 0, f)       # English -> 中文
        app.key(zn.K_ENTER, 0, f)      # check 中文
        with mock.patch.object(feeds, 'save_settings') as save, \
                mock.patch.object(zn.App, 'load_items', return_value=[item('zh', 5)]):
            app.key(zn.K_ESC, 0, f)
        save.assert_called_once_with({'languages': ['en', 'zh'], 'categories': ['tech'],
                                      'interval': 10})
        self.assertIsNone(app.panel)
        self.assertEqual(app.items[0]['title'], 'zh')
        f.wake.set.assert_called_once()

    def test_settings_unchanged_does_nothing(self):
        app, f = self.make(), mock.Mock(busy=False)
        app.key(zn.K_S, 0, f)
        with mock.patch.object(feeds, 'save_settings') as save:
            app.key(zn.K_ESC, 0, f)
        save.assert_not_called()
        f.wake.set.assert_not_called()

    def test_render_sizes(self):
        app = self.make()
        self.assertEqual(zn.render_card(app.items, 1, 2000).size, (zn.W, zn.CARD_H))
        self.assertEqual(zn.render_card([], 0, 0, status='offline').size, (zn.W, zn.CARD_H))
        band = zn.render_band(zn.build_ticker(app.items, 0), 12345, 0.5)
        self.assertEqual(band.size, (zn.W, zn.BAND_H))
        self.assertEqual(len(zn.to_rgb565(band)), zn.W * zn.BAND_H * 2)
        tall = zn.render_detail(app.items[1], 2000)
        self.assertEqual(zn.detail_frame(tall, 0, app.items[1], 2000).size, (zn.W, zn.H))


class KeyTests(unittest.TestCase):
    def test_fzxc_act_as_arrows(self):
        import os
        import struct
        r, w = os.pipe()
        os.set_blocking(r, False)
        self.addCleanup(os.close, r)
        for code, value in ((33, 1), (33, 0), (44, 1), (45, 1), (46, 1), (31, 1), (103, 1)):
            os.write(w, struct.pack('llHHi', 0, 0, 1, code, value))
        os.close(w)
        self.assertEqual(zn.read_keys(r), [zn.K_UP, zn.K_LEFT, zn.K_DOWN, zn.K_RIGHT, zn.K_S, zn.K_UP])


class SettingsScreenTests(unittest.TestCase):
    def make(self):
        from settings_ui import Settings
        return Settings({'languages': ['en'], 'categories': ['tech'], 'interval': 12})

    def test_navigation(self):
        p = self.make()
        self.assertEqual(p.focus[1], 'en')
        p.handle(zn.K_RIGHT)
        self.assertEqual(p.focus[1], 'tech')
        p.handle(zn.K_DOWN)
        p.handle(zn.K_DOWN)
        self.assertEqual(p.focus[1], 'general')
        p.handle(zn.K_DOWN)
        self.assertEqual(p.focus, 'interval')
        p.handle(zn.K_RIGHT)  # on the interval row, arrows change the value
        self.assertEqual(p.s['interval'], 20)
        p.handle(zn.K_UP)
        self.assertEqual(p.focus[1], 'general')
        p.handle(zn.K_LEFT)  # row clamps to the shorter column
        self.assertEqual(p.focus[1], 'zh')
        self.assertEqual(p.handle(zn.K_ESC), 'done')

    def test_toggle_keeps_one_and_order(self):
        p = self.make()
        p.handle(zn.K_ENTER)  # unchecking the only language is refused
        self.assertEqual(p.s['languages'], ['en'])
        p.handle(zn.K_RIGHT)
        p.handle(zn.K_DOWN)
        p.handle(zn.K_DOWN)
        p.handle(zn.K_ENTER)  # general
        p.handle(zn.K_UP)
        p.handle(zn.K_ENTER)  # life
        self.assertEqual(p.s['categories'], ['tech', 'life', 'general'])
        p.handle(zn.K_UP)
        p.handle(zn.K_SPACE)  # tech off
        self.assertEqual(p.s['categories'], ['life', 'general'])

    def test_interval_cycles_and_render(self):
        p = self.make()
        p.row = None
        for _ in range(len(feeds.INTERVALS)):
            p.handle(zn.K_ENTER)
        self.assertEqual(p.s['interval'], 12)
        self.assertEqual(p.render().size, (zn.W, zn.H))


if __name__ == '__main__':
    unittest.main()
