"""RSS / Atom fetching and parsing for News (standard library only)."""
import html
import json
import os
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

HOME = os.path.expanduser('~')
CONFIG = os.path.join(HOME, '.config/zero-news.json')
CACHE = os.path.join(HOME, '.local/share/zero-news/news.json')
USER_AGENT = 'zero-news/1.0 (CardputerZero)'
TIMEOUT = 20
PER_FEED = 12
MAX_ITEMS = 80
SUMMARY_MAX = 400

LANGUAGES = ['en', 'zh']
CATEGORIES = ['tech', 'life', 'general']
TAGS = {'tech': 'TECH', 'life': 'LIFE', 'general': 'NEWS'}
INTERVALS = [8, 12, 20, 30, 60]
DEFAULT_SETTINGS = {'languages': ['en', 'zh'], 'categories': ['tech'], 'interval': 12}


def _f(lang, cat, name, short, url, **extra):
    return dict(lang=lang, cat=cat, name=name, short=short, url=url, **extra)


# tz: hours offset for feeds whose dates carry no zone.
CATALOG = [
    _f('en', 'tech', 'Hacker News', 'HN', 'https://hnrss.org/frontpage'),
    _f('en', 'tech', 'The Verge', 'Verge', 'https://www.theverge.com/rss/index.xml'),
    _f('en', 'tech', 'Ars Technica', 'Ars', 'https://feeds.arstechnica.com/arstechnica/index'),
    _f('en', 'tech', 'Engadget', 'Engadget', 'https://www.engadget.com/rss.xml'),
    _f('en', 'life', 'BBC Health', 'BBC', 'https://feeds.bbci.co.uk/news/health/rss.xml'),
    _f('en', 'life', 'NYT Well', 'NYT', 'https://rss.nytimes.com/services/xml/rss/nyt/Well.xml'),
    _f('en', 'life', 'NYT Food', 'NYT', 'https://rss.nytimes.com/services/xml/rss/nyt/DiningandWine.xml'),
    _f('en', 'life', 'NYT Travel', 'NYT', 'https://rss.nytimes.com/services/xml/rss/nyt/Travel.xml'),
    _f('en', 'life', 'CBC Arts', 'CBC', 'https://www.cbc.ca/webfeed/rss/rss-arts'),
    _f('en', 'general', 'BBC World', 'BBC', 'https://feeds.bbci.co.uk/news/world/rss.xml'),
    _f('en', 'general', 'CBC News', 'CBC', 'https://www.cbc.ca/webfeed/rss/rss-topstories'),
    _f('en', 'general', 'CBC Toronto', 'CBC', 'https://www.cbc.ca/webfeed/rss/rss-canada-toronto'),
    _f('en', 'general', 'NYT', 'NYT', 'https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml'),
    _f('zh', 'tech', 'unwire.hk', 'unwire', 'https://unwire.hk/feed/'),
    _f('zh', 'tech', 'iThome', 'iThome', 'https://www.ithome.com.tw/rss', tz=8),
    _f('zh', 'tech', '科技新報', '科技新報', 'https://technews.tw/feed/'),
    _f('zh', 'tech', 'INSIDE', 'INSIDE', 'https://www.inside.com.tw/feed/rss'),
    _f('zh', 'life', '明報娛樂', '明報', 'https://news.mingpao.com/rss/ins/s00007.xml'),
    _f('zh', 'life', 'RTHK 體育', 'RTHK', 'https://rthk9.rthk.hk/rthk/news/rss/c_expressnews_csport.xml'),
    _f('zh', 'life', '中央社 生活', '中央社', 'https://feeds.feedburner.com/rsscna/lifehealth'),
    _f('zh', 'life', '中央社 文化', '中央社', 'https://feeds.feedburner.com/rsscna/culture'),
    _f('zh', 'life', '自由時報 生活', '自由時報', 'https://news.ltn.com.tw/rss/life.xml'),
    _f('zh', 'general', 'RTHK 本地', 'RTHK', 'https://rthk9.rthk.hk/rthk/news/rss/c_expressnews_clocal.xml'),
    _f('zh', 'general', 'RTHK 國際', 'RTHK', 'https://rthk9.rthk.hk/rthk/news/rss/c_expressnews_cinternational.xml'),
    _f('zh', 'general', '星島', '星島', 'https://www.stheadline.com/rss'),
    _f('zh', 'general', 'BBC 中文', 'BBC', 'https://feeds.bbci.co.uk/zhongwen/trad/rss.xml'),
]


def _read_config():
    try:
        with open(CONFIG) as f:
            raw = json.load(f)
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def load_settings():
    """{'languages', 'categories', 'interval'} from ~/.config/zero-news.json, else defaults."""
    raw = _read_config()
    s = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULT_SETTINGS.items()}
    for key, allowed in (('languages', LANGUAGES), ('categories', CATEGORIES)):
        got = [v for v in raw.get(key, []) if v in allowed] if isinstance(raw.get(key), list) else []
        if got:
            s[key] = got
    if raw.get('interval') in INTERVALS:
        s['interval'] = raw['interval']
    return s


def save_settings(s):
    """Write the settings, keeping any other keys (such as a custom "feeds" list)."""
    raw = _read_config()
    raw.update({k: s[k] for k in DEFAULT_SETTINGS})
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    tmp = CONFIG + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(raw, f, ensure_ascii=False, indent=1)
    os.replace(tmp, CONFIG)


def catalog():
    """The built-in feeds, or a custom "feeds" list from the config file."""
    custom = _read_config().get('feeds')
    if isinstance(custom, list):
        feeds = [dict(f) for f in custom if isinstance(f, dict) and f.get('url') and f.get('name')]
        for f in feeds:
            f.setdefault('short', f['name'][:8])
        if feeds:
            return feeds
    return [dict(f) for f in CATALOG]


def selected_feeds(settings, feeds=None):
    """Feeds matching the chosen languages and categories (a feed without
    lang/cat matches any)."""
    return [f for f in (feeds if feeds is not None else catalog())
            if f.get('lang', settings['languages'][0]) in settings['languages']
            and f.get('cat', settings['categories'][0]) in settings['categories']]


CJK = re.compile(r'[　-鿿＀-￯]')
# The CJK font draws these as full-width glyphs, which leaves gaps in Latin text.
LATIN_PUNCT = str.maketrans({'‘': "'", '’': "'", '“': '"', '”': '"', '—': ' - ', '…': '...'})


def clean_text(s):
    """Plain text from an RSS field that may hold escaped HTML."""
    if not s:
        return ''
    s = html.unescape(s)
    s = re.sub(r'<[^>]+>', ' ', s)
    s = html.unescape(s)
    if not CJK.search(s):
        s = s.translate(LATIN_PUNCT)
    return re.sub(r'\s+', ' ', s).strip()


def parse_date(s, tz_hours=0):
    """Unix time from an RFC 822 or ISO 8601 date, or None."""
    if not s:
        return None
    s = s.strip()
    try:
        dt = parsedate_to_datetime(s)
    except (TypeError, ValueError, IndexError):
        dt = None
    if dt is None:
        iso = re.sub(r'\s+', 'T', s, count=1).replace('Z', '+00:00')
        try:
            dt = datetime.fromisoformat(iso)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone(timedelta(hours=tz_hours)))
    return int(dt.timestamp())


def _local(tag):
    return tag.rsplit('}', 1)[-1]


def _child(el, *names):
    for c in el:
        if _local(c.tag) in names:
            return c
    return None


def _text(el, *names):
    c = _child(el, *names)
    return (c.text or '') if c is not None else ''


def _atom_link(entry):
    best = ''
    for c in entry:
        if _local(c.tag) == 'link':
            href = c.get('href') or (c.text or '')
            if c.get('rel', 'alternate') == 'alternate':
                return href.strip()
            best = best or href.strip()
    return best


def parse_feed(raw, feed):
    """Items (newest first) from RSS 2.0 or Atom bytes."""
    root = ET.fromstring(raw)
    entries = [e for e in root.iter() if _local(e.tag) in ('item', 'entry')]
    tz = feed.get('tz', 0)
    now = int(time.time())
    items = []
    for e in entries:
        title = clean_text(_text(e, 'title'))
        if not title:
            continue
        summary = clean_text(_text(e, 'description', 'summary') or _text(e, 'content'))
        if 'Article URL:' in summary or summary == title:
            summary = ''  # hnrss puts only links here
        if len(summary) > SUMMARY_MAX:
            summary = summary[:SUMMARY_MAX - 1].rstrip() + '…'
        ts = parse_date(_text(e, 'pubDate', 'published', 'updated', 'date'), tz)
        if ts and ts > now + 3600:
            ts = None  # scheduled post with a far-future date: treat as undated
        items.append({
            'title': title,
            'summary': summary,
            'link': _atom_link(e) if _local(e.tag) == 'entry' else _text(e, 'link').strip(),
            'ts': min(ts, now) if ts else None,
            'src': feed['name'],
            'short': feed['short'],
            'tag': TAGS.get(feed.get('cat'), 'NEWS'),
        })
    items.sort(key=lambda i: i['ts'] or 0, reverse=True)
    return items


def fetch_feed(feed):
    req = urllib.request.Request(feed['url'], headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return parse_feed(r.read(), feed)


def merge(per_feed):
    """One list across feeds: newest first, PER_FEED per feed, no duplicate titles."""
    seen, out = set(), []
    pool = [i for items in per_feed for i in items[:PER_FEED]]
    for i in sorted(pool, key=lambda i: i['ts'] or 0, reverse=True):
        key = i['title'].lower()
        if key not in seen:
            seen.add(key)
            out.append(i)
    return out[:MAX_ITEMS]


def load_cache():
    try:
        with open(CACHE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_cache(data):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    tmp = CACHE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, CACHE)


def items_for(cache, feeds):
    """The merged headline list of the given feeds from the cache."""
    per_feed = (cache or {}).get('feeds', {})
    return merge(per_feed.get(f['url'], []) for f in feeds)


def missing(cache, feeds):
    """True if any of the feeds has never been fetched into the cache."""
    per_feed = (cache or {}).get('feeds', {})
    return any(f['url'] not in per_feed for f in feeds)


def refresh(feeds, cache=None, fetch=fetch_feed):
    """Fetch the feeds in parallel and return the new cache dict
    {'feeds': {url: [items]}, 'fetched_at', 'failed'}. A feed that fails keeps
    its cached items; feeds not fetched this time (other settings) stay cached."""
    per_feed = dict((cache or {}).get('feeds', {}))
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {f['url']: pool.submit(fetch, f) for f in feeds}
    failed = []
    for f in feeds:
        try:
            per_feed[f['url']] = futures[f['url']].result()
        except Exception:
            failed.append(f['name'])
            per_feed.setdefault(f['url'], [])
    return {'feeds': per_feed, 'fetched_at': int(time.time()), 'failed': failed}
