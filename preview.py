"""Preview frames of News rendered to PNG files (zero_news.py --preview)."""
import time

from PIL import Image

import feeds
from zero_news import (W, H, CARD_H, BG, WARN, render_card, render_band, build_ticker,
                       render_detail, detail_frame, render_exit)


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

    def frame(items, idx, offset, progress, paused=False, hints=False):
        img = Image.new('RGB', (W, H), BG)
        img.paste(render_card(items, idx, now, paused), (0, 0))
        img.paste(render_band(build_ticker(items, idx), offset, progress, paused, hints or paused),
                  (0, CARD_H))
        return img

    items = feeds.items_for(cache, feeds.selected_feeds(settings))
    frame(items, 0, 140, 0.45).save(out)
    frame(items, 0, 0, 0.3, hints=True).save(f'{stem}-hints.png')
    frame(items, 0, 0, 1, paused=True).save(f'{stem}-paused.png')
    render_card([], 0, now).save(f'{stem}-empty.png')
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
    panel.row = 'mine'
    panel.render().save(f'{stem}-settings-mine.png')
    from my_feeds_ui import MyFeeds
    mine = MyFeeds(dict(settings, my_feeds=[
        {'name': 'Hacker News: Newest', 'short': 'Hacker News', 'url': 'https://hnrss.org/newest'},
        {'name': 'The Verge', 'short': 'The Verge', 'url': 'https://www.theverge.com/rss/index.xml'}]))
    mine.row = 2
    mine.render().save(f'{stem}-mine.png')
    mine.state, mine.text = 'input', 'theverge.com'
    mine.render().save(f'{stem}-mine-input.png')
    mine.text, mine.message = 'example.com', ('No RSS feed found', WARN)
    mine.render().save(f'{stem}-mine-error.png')
    img = frame(items, 0, 0, 0.3)
    img.paste(render_exit(0.55), (0, CARD_H))
    img.save(f'{stem}-exit.png')
