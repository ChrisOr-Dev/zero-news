import unittest
from unittest import mock

from PIL import Image, ImageDraw

import article
import feeds
import keys
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

    def test_full_text_body(self):
        raw = RSS.replace(b'</description>\n<pubDate>Thu, 12 Nov 2026 10:00:00',
                          b'</description>\n<content:encoded><![CDATA[<p>First &amp; para</p>'
                          b'<figure><img src="x"/><figcaption>Cap</figcaption></figure>'
                          b'<p>Second<br/>line</p>]]></content:encoded>\n'
                          b'<pubDate>Thu, 12 Nov 2026 10:00:00', 1)
        raw = raw.replace(b'<rss version="2.0">',
                          b'<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">')
        items = feeds.parse_feed(raw, FEED)
        self.assertEqual(items[1]['body'], 'First & para\nCap\nSecond\nline')
        self.assertEqual(items[1]['summary'], 'Some bold text')  # card text unchanged
        self.assertNotIn('body', items[0])
        atom = ATOM.replace(b'</summary>', b'</summary><content type="html">&lt;p&gt;Hello world, '
                            b'and the rest&lt;/p&gt;</content>')
        self.assertEqual(feeds.parse_feed(atom, FEED)[0]['body'], 'Hello world, and the rest')
        self.assertNotIn('body', feeds.parse_feed(ATOM, FEED)[0])  # nothing longer than summary
        long = feeds.clean_body('<p>' + 'w ' * 3000 + '</p>')
        self.assertEqual(len(long), feeds.BODY_MAX)
        self.assertTrue(long.endswith('…'))

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
        mine = {'name': 'Blog', 'short': 'Blog', 'url': 'https://b/rss'}
        dup = {'name': 'HN', 'short': 'HN', 'url': feeds.CATALOG[0]['url']}
        got = feeds.selected_feeds({'languages': ['zh'], 'categories': ['life'],
                                    'my_feeds': [mine]})
        self.assertEqual(got[-1], dict(mine, cat='mine'))  # always shown, tagged MY
        self.assertEqual(feeds.TAGS[got[-1]['cat']], 'MY')
        got = feeds.selected_feeds({'languages': ['en'], 'categories': ['tech'],
                                    'my_feeds': [dup]})  # already a selected built-in feed
        self.assertEqual(sum(f['url'] == dup['url'] for f in got), 1)


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
        mine = [{'name': 'Blog', 'short': 'Blog', 'url': 'https://b/rss'}]
        feeds.save_settings({'languages': ['zh'], 'categories': ['tech'], 'interval': 30,
                             'my_feeds': mine})
        self.assertEqual(feeds.load_settings(), {'languages': ['zh'], 'categories': ['tech'],
                                                 'interval': 30, 'my_feeds': mine})
        self.assertEqual(feeds.catalog(), [{'name': 'Mine', 'url': 'https://m', 'short': 'Mine'}])

    def test_invalid_my_feeds_dropped(self):
        self.write('{"my_feeds": [{"name": "A", "url": "https://a"}, {"name": "no url"}, 5,'
                   ' {"url": "https://noname"}]}')
        self.assertEqual(feeds.load_settings()['my_feeds'],
                         [{'name': 'A', 'short': 'A', 'url': 'https://a'}])


class ProbeTests(unittest.TestCase):
    RSS = b'<rss><channel><title>My Blog</title><item><title>Hi</title></item></channel></rss>'

    def run_probe(self, url, pages):
        def get(u):
            if u not in pages:
                import urllib.error
                raise urllib.error.HTTPError(u, 404, 'nf', {}, None)
            return u, pages[u]
        with mock.patch.object(feeds, '_get', side_effect=get):
            return feeds.probe(url)

    def test_feed_page_link_and_wordpress_fallback(self):
        self.assertEqual(self.run_probe('b.com/rss', {'https://b.com/rss': self.RSS}),
                         {'name': 'My Blog', 'short': 'My Blog', 'url': 'https://b.com/rss'})
        page = b'<html><link rel="alternate" type="application/rss+xml" href="/x.xml"></html>'
        got = self.run_probe('https://b.com', {'https://b.com': page, 'https://b.com/x.xml': self.RSS})
        self.assertEqual(got['url'], 'https://b.com/x.xml')
        got = self.run_probe('b.com', {'https://b.com': b'<html></html>', 'https://b.com/feed': self.RSS})
        self.assertEqual(got['url'], 'https://b.com/feed')
        with self.assertRaisesRegex(ValueError, 'No RSS feed'):
            self.run_probe('b.com', {'https://b.com': b'<html></html>'})
        with self.assertRaisesRegex(ValueError, 'no headlines'):
            self.run_probe('b.com', {'https://b.com': b'<rss><channel><title>t</title></channel></rss>'})


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

    def test_wrap_keeps_cjk_punctuation_off_line_starts(self):
        text = '天文台發出特別天氣提示，指雨區正逐漸靠近。預料未來數小時本港雨勢有時頗大，提醒市民。' * 3
        for width in range(100, 200, 7):
            lines = zn.wrap(self.d, text, self.f, width)
            self.assertEqual(''.join(lines), text)
            for line in lines:
                self.assertNotIn(line[0], '，。')
                self.assertLessEqual(self.d.textlength(line, font=self.f), width + 16)

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
    def setUp(self):
        # Details views fetch the web page in a thread: no network in tests.
        self.started = []
        patcher = mock.patch.object(zn.threading, 'Thread', side_effect=self.thread)
        patcher.start()
        self.addCleanup(patcher.stop)
        zn.ARTICLES.clear()

    def thread(self, target, args, daemon):
        self.started.append((target, args))
        return mock.Mock()

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

    def test_hints_until_first_story_change(self):
        app, f = self.make(), mock.Mock(busy=False)
        self.assertTrue(app.hinting)
        with mock.patch.object(zn.App, 'load_items', return_value=list(app.items)):
            app.reload(1)  # first fetch finishing keeps the hints
        self.assertTrue(app.hinting)
        app.tick(11)
        self.assertFalse(app.hinting)
        app2 = self.make()
        app2.key(zn.K_DOWN, 0, f)
        self.assertFalse(app2.hinting)

    def test_keys(self):
        app, f = self.make(), mock.Mock(busy=False)
        app.key(zn.K_UP, 0, f)
        self.assertEqual(app.idx, 2)
        app.key(zn.K_DOWN, 0, f)
        app.key(zn.K_DOWN, 0, f)
        self.assertEqual(app.idx, 1)
        app.items[1]['body'] = 'para one\n' + 'word ' * 600
        app.key(zn.K_ENTER, 0, f)
        det = app.detail
        self.assertGreater(det.height, 3 * zn.H)  # body, not the short summary
        app.key(zn.K_RIGHT, 0, f)  # page down
        self.assertEqual(det.scroll, zn.H - 50)
        app.key(zn.K_UP, 0, f)
        self.assertEqual(det.scroll, zn.H - 84)
        for _ in range(100):
            app.key(zn.K_RIGHT, 0, f)
        self.assertEqual(det.scroll, det.height - zn.H)  # clamped at the end
        app.key(zn.K_LEFT, 0, f)
        self.assertEqual(det.scroll, det.height - zn.H - (zn.H - 50))
        app.tick(1000)  # no auto-advance while reading
        self.assertEqual(app.idx, 1)
        app.key(zn.K_ESC, 0, f)
        self.assertIsNone(app.detail)
        app.key(zn.K_R, 0, f)
        f.wake.set.assert_called_once()
        app.key(33, 0, f)  # plain F is up
        self.assertEqual(app.idx, 0)

    def test_hold_esc_to_exit(self):
        app, f = self.make(), mock.Mock(busy=False)
        self.assertIsNone(app.exit_progress(0))
        self.assertTrue(app.key(zn.K_Q, 0, f))  # Q no longer quits
        with mock.patch('keys.is_down', return_value=True):
            app.key(zn.K_ESC, 10, f)
            self.assertAlmostEqual(app.exit_progress(11.5), 0.5)
            self.assertGreaterEqual(app.exit_progress(13), 1)
        down = mock.patch('keys.is_down', return_value=True)
        up = mock.patch('keys.is_down', return_value=False)
        app = self.make()
        with down:  # a short bounce (finger moves) keeps counting from the first press
            app.key(zn.K_ESC, 20, f)
            app.exit_progress(20.5)
        with up:
            self.assertAlmostEqual(app.exit_progress(21), 1 / 3)
            self.assertAlmostEqual(app.exit_progress(21.3), 1.3 / 3)
        with down:
            app.key(zn.K_ESC, 21.35, f)
            self.assertGreaterEqual(app.exit_progress(23), 1)
        app = self.make()
        with down:  # let go for longer than the grace time: cancelled
            app.key(zn.K_ESC, 25, f)
        with up:
            app.exit_progress(26)
            self.assertIsNotNone(app.exit_progress(26 + zn.ESC_GRACE - 0.05))
            self.assertIsNone(app.exit_progress(26 + zn.ESC_GRACE + 0.05))
            self.assertIsNone(app.esc_at)
        app = self.make()
        with mock.patch('keys.is_down', return_value=True):  # ESC that closes details does not count
            app.key(zn.K_ENTER, 30, f)
            app.key(zn.K_ESC, 30, f)
            self.assertIsNone(app.detail)
            self.assertIsNone(app.exit_progress(40))
        for progress in (0, 0.001, 0.4, 1.2):  # first frame after ESC is progress 0
            self.assertEqual(zn.render_exit(progress).size, (zn.W, zn.BAND_H))

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
        self.assertEqual(zn.render_band(zn.build_ticker(app.items, 0), 0, 1, True, True).size,
                         (zn.W, zn.BAND_H))
        self.assertEqual(band.size, (zn.W, zn.BAND_H))
        self.assertEqual(len(zn.to_rgb565(band)), zn.W * zn.BAND_H * 2)
        self.assertEqual(zn.Detail(app.items[1]).frame(2000).size, (zn.W, zn.H))

    def test_details_fetch_full_article(self):
        app, f = self.make(), mock.Mock(busy=False)
        app.key(zn.K_DOWN, 0, f)
        app.key(zn.K_ENTER, 0, f)
        det = app.detail
        self.assertTrue(det.loading)
        self.assertEqual(det.text, 's' * 200)  # feed text shown at once
        [(target, args)] = self.started
        self.assertEqual(args, (det, 'https://x/h1'))
        before = app.overlay(0)[0]
        with mock.patch.object(zn.article, 'fetch', return_value='Full one\nFull two ' * 50):
            target(*args)
        after = app.overlay(0)[0]
        self.assertNotEqual(before, after)  # redrawn
        self.assertFalse(det.loading)
        self.assertTrue(det.text.startswith('Full one'))
        self.assertGreater(det.height, zn.H * 2)
        app.key(zn.K_ESC, 0, f)
        app.key(zn.K_ENTER, 0, f)  # second open uses the cached text, no new fetch
        self.assertEqual(len(self.started), 1)
        self.assertFalse(app.detail.loading)
        self.assertTrue(app.detail.text.startswith('Full one'))

    def test_details_keep_feed_text_when_page_is_poorer(self):
        app, f = self.make(), mock.Mock(busy=False)
        app.key(zn.K_DOWN, 0, f)
        app.key(zn.K_ENTER, 0, f)
        target, args = self.started[0]
        with mock.patch.object(zn.article, 'fetch', side_effect=OSError('offline')):
            target(*args)
        app.overlay(0)
        self.assertFalse(app.detail.loading)
        self.assertEqual(app.detail.text, 's' * 200)
        self.assertEqual(zn.ARTICLES, {'https://x/h1': ''})


class ArticleTests(unittest.TestCase):
    def test_paragraphs_inside_article(self):
        page = ('<html><head><title>Big news - Site</title><script>var x = "<p>no</p>";</script></head>'
                '<body><nav><p>Home | World | Sports menu</p></nav><p>Outside text that is long enough.</p>'
                '<article><h1>Big news</h1><p>First <b>para</b> of the story, long enough to keep.</p>'
                '<p>Advertisement</p><p>Short</p>'
                '<p>Second para of the story, also long enough to keep here. ' + 'x ' * 80 + '</p>'
                '<p>Second para of the story, also long enough to keep here. ' + 'x ' * 80 + '</p>'
                '<p>Already a subscriber? Log in.</p></article><footer><p>Copyright 2026 Site</p></footer>')
        paras = article.extract(page).split('\n')
        self.assertEqual(len(paras), 2)
        self.assertEqual(paras[0], 'First para of the story, long enough to keep.')
        self.assertTrue(paras[1].startswith('Second para'))

    def test_text_without_p_tags(self):
        page = ('<title>天文台提示 - RTHK</title><div class="itemFullText">'
                '天文台發出特別天氣提示，指雨區正逐漸靠近，預料未來數小時本港雨勢有時頗大。<br><br>'
                '市民應留意天氣變化，並避免前往低窪地區，如有需要應及早採取預防措施。<br>'
                '<a href="x.jpg" title="圖片">天文台提示</a></div>' + '<div>menu</div>' * 3)
        self.assertEqual(article.extract(page).split('\n'), [
            '天文台發出特別天氣提示，指雨區正逐漸靠近，預料未來數小時本港雨勢有時頗大。',
            '市民應留意天氣變化，並避免前往低窪地區，如有需要應及早採取預防措施。'])

    def test_inline_tags_leave_no_gaps(self):
        page = '<article><p>A lawsuit (which <a href="#">you settled</a>) and <em>more</em> text here.</p></article>'
        self.assertEqual(article.extract(page), 'A lawsuit (which you settled) and more text here.')

    def test_page_url_and_decode(self):
        self.assertEqual(article.page_url('https://a.hk/x/%e5%9d%a6" target="blank'),
                         'https://a.hk/x/%e5%9d%a6')
        self.assertEqual(article.page_url(' https://b.com/新聞/1?q=a b'), 'https://b.com/%E6%96%B0%E8%81%9E/1?q=a')
        self.assertEqual(article.decode('中文'.encode('big5'), 'text/html; charset=big5'), '中文')
        self.assertEqual(article.decode(b'<meta charset="utf-8">\xe4\xb8\xad'), '<meta charset="utf-8">中')
        self.assertEqual(article.decode(b'ok', 'text/html; charset=bogus'), 'ok')

    def test_long_text_is_capped(self):
        page = '<article>' + '<p>' + 'word ' * 400 + '</p>' * 1 + ''.join(
            f'<p>{k} ' + 'more words here ' * 30 + '</p>' for k in range(20)) + '</article>'
        text = article.extract(page)
        self.assertEqual(len(text), article.MAX_TEXT)
        self.assertTrue(text.endswith('…'))


class KeyTests(unittest.TestCase):
    def events(self, *pairs):
        import os
        import struct
        r, w = os.pipe()
        os.set_blocking(r, False)
        self.addCleanup(os.close, r)
        for code, value in pairs:
            os.write(w, struct.pack('llHHi', 0, 0, 1, code, value))
        os.close(w)
        return keys.read_keys(r)

    def test_fzxc_act_as_arrows(self):
        got = self.events((33, 1), (33, 0), (44, 1), (45, 1), (46, 1), (31, 1), (103, 1))
        self.assertEqual([keys.nav(c) for c in got],
                         [zn.K_UP, zn.K_LEFT, zn.K_DOWN, zn.K_RIGHT, zn.K_S, zn.K_UP])
        self.assertEqual([keys.to_char(c) for c in got[:4]], ['f', 'z', 'x', 'c'])

    def test_shift_and_held_keys(self):
        got = self.events((42, 1), (39, 1), (39, 0), (53, 1), (53, 0), (35, 1), (35, 0),
                          (42, 0), (53, 1), (1, 1))
        self.assertEqual(''.join(keys.to_char(c) for c in got[:4]), ':?H/')
        self.assertTrue(keys.is_down(53))
        self.assertTrue(keys.is_down(1))
        self.assertFalse(keys.is_down(42))
        self.assertEqual(keys.nav(got[0]), 39)
        self.events((53, 0), (1, 0))
        self.assertFalse(keys.is_down(1))


class SettingsScreenTests(unittest.TestCase):
    def make(self):
        from settings_ui import Settings
        return Settings({'languages': ['en'], 'categories': ['tech'], 'interval': 12,
                         'my_feeds': []})

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


class MyFeedsTests(unittest.TestCase):
    FEED = {'name': 'Blog', 'short': 'Blog', 'url': 'https://b/rss'}

    def make(self, probe=None, mine=()):
        from my_feeds_ui import MyFeeds
        self.s = {'languages': ['en'], 'categories': ['tech'], 'interval': 12,
                  'my_feeds': list(mine)}
        return MyFeeds(self.s, probe or (lambda url: dict(self.FEED)), spawn=lambda fn: fn())

    def type(self, p, text):
        codes = {c: k for k, c in keys.CHARS.items()}
        for ch in text:
            p.handle(codes[ch])

    def test_add_feed(self):
        seen = []
        p = self.make(lambda url: seen.append(url) or dict(self.FEED))
        p.handle(zn.K_ENTER)
        self.assertEqual(p.state, 'input')
        self.type(p, 'b.cxz')  # c/x/z type letters here, not arrows
        p.handle(zn.K_BACKSPACE)
        p.handle(keys.SHIFT + 39)  # shift+; is ':'
        self.assertEqual(p.text, 'b.cx:')
        p.handle(zn.K_ENTER)
        self.assertEqual(seen, ['b.cx:'])
        self.assertEqual((p.state, p.row), ('list', 1))
        self.assertEqual(self.s['my_feeds'], [self.FEED])
        self.assertEqual(p.render().size, (zn.W, zn.H))

    def test_errors_and_duplicates_stay_in_input(self):
        def bad(url):
            raise ValueError('No RSS feed found at this address')
        p = self.make(bad)
        p.handle(zn.K_ENTER)
        self.type(p, 'x.com')
        p.handle(zn.K_ENTER)
        self.assertEqual(p.state, 'input')
        self.assertEqual(p.message[0], 'No RSS feed found at this address')
        p = self.make(mine=[self.FEED])
        p.handle(zn.K_ENTER)
        self.type(p, 'b')
        p.handle(zn.K_ENTER)
        self.assertEqual(p.message[0], 'Already in My feeds')
        self.assertEqual(len(self.s['my_feeds']), 1)
        p.handle(zn.K_ESC)
        self.assertEqual(p.state, 'list')
        self.assertEqual(p.handle(zn.K_ESC), 'done')

    def test_cancelled_check_is_ignored(self):
        jobs = []
        from my_feeds_ui import MyFeeds
        self.s = {'my_feeds': []}
        p = MyFeeds(self.s, lambda url: dict(self.FEED), spawn=jobs.append)
        p.handle(zn.K_ENTER)
        self.type(p, 'b')
        p.handle(zn.K_ENTER)
        self.assertEqual(p.state, 'checking')
        p.handle(zn.K_ESC)
        jobs[0]()
        self.assertEqual((p.state, self.s['my_feeds']), ('input', []))

    def test_delete_needs_two_presses_and_full_list(self):
        other = dict(self.FEED, url='https://o', name='Other')
        p = self.make(mine=[self.FEED, other])
        p.handle(45)  # plain X moves down in the list
        p.handle(zn.K_BACKSPACE)
        self.assertEqual(len(self.s['my_feeds']), 2)
        p.handle(zn.K_BACKSPACE)
        self.assertEqual(self.s['my_feeds'], [other])
        p.handle(zn.K_BACKSPACE)
        p.handle(zn.K_DOWN)  # any other key cancels
        p.handle(zn.K_BACKSPACE)
        self.assertEqual(self.s['my_feeds'], [other])
        full = self.make(mine=[dict(self.FEED, url=str(i)) for i in range(feeds.MAX_MY_FEEDS)])
        full.handle(zn.K_ENTER)
        self.assertEqual(full.state, 'list')
        for _ in range(8):
            full.handle(zn.K_DOWN)
        self.assertEqual(full.top, 4)
        self.assertEqual(full.render().size, (zn.W, zn.H))

    def test_settings_opens_my_feeds(self):
        from settings_ui import Settings, MINE
        p = Settings({'languages': ['en'], 'categories': ['tech'], 'interval': 12, 'my_feeds': []})
        p.row = None
        p.handle(zn.K_DOWN)
        self.assertEqual(p.row, MINE)
        v = p.version
        p.handle(zn.K_ENTER)
        self.assertIsNotNone(p.sub)
        self.assertNotEqual(p.version, v)
        p.handle(zn.K_S)  # S does not close My feeds; ESC goes back to settings
        self.assertIsNotNone(p.sub)
        p.handle(zn.K_ESC)
        self.assertIsNone(p.sub)
        self.assertEqual(p.render().size, (zn.W, zn.H))


class TestNet(unittest.TestCase):
    def test_ipv4_sorted_first(self):
        import socket
        import net
        v6 = (socket.AF_INET6, 1, 6, '', ('::1', 443, 0, 0))
        v4 = (socket.AF_INET, 1, 6, '', ('1.2.3.4', 443))
        with mock.patch.object(net, '_getaddrinfo', return_value=[v6, v6, v4]):
            self.assertEqual(net._ipv4_first('h', 443), [v4, v6, v6])


if __name__ == '__main__':
    unittest.main()
