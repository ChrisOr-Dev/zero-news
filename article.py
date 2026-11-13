"""Full article text fetched from a story's web page (shown in the details view)."""
import html
import re
import urllib.parse
import urllib.request

from feeds import clean_text

TIMEOUT = 10
MAX_PAGE = 3_000_000
MAX_TEXT = 8000
# Some sites send bare-bones pages or 403s to unknown clients.
USER_AGENT = ('Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/130.0 Safari/537.36')

DROP = re.compile(r'<(script|style|noscript|svg|nav|footer|aside|form|button|select|template)\b'
                  r'.*?</\1\s*>', re.S | re.I)
COMMENT = re.compile(r'<!--.*?-->', re.S)
# Inline tags vanish without a space, so links don't leave gaps like 'settled )'.
INLINE = re.compile(r'</?(?:a|em|strong|b|i|u|span|sup|sub|abbr|time|mark|cite|q|small)\b[^>]*>', re.I)
PARA = re.compile(r'<p\b[^>]*>(.*?)</p\s*>', re.S | re.I)
TITLE = re.compile(r'<title\b[^>]*>(.*?)</title', re.S | re.I)
ARTICLE = re.compile(r'<article\b.*</article\s*>', re.S | re.I)
BREAK = re.compile(r'<\s*(?:br|/?div|/?p|/?li|/?h[1-6]|/?tr|/?section|/?blockquote)\b[^>]*>', re.I)
CHARSET = re.compile(rb'<meta[^>]+charset=["\']?([\w-]+)', re.I)
# Site chrome that comes inside <p> tags.
JUNK = re.compile(
    r'^(?:advertisement|this video can not be played|image source|image caption|getty images'
    r'|posts from this (?:topic|author)|follow topics and authors|see all by|read more|related:'
    r'|sign up|subscribe|share this|copyright|©|all rights reserved|supported by|credit\.\.\.'
    r'|already a subscriber|want all of the times|senior writer|thanks for reading)'
    r'|this is the title for the native ad|we use cookies|enable javascript|thank you for your patience'
    r'|^ithome security$|何不給我們一個鼓勵|經授權，不得轉載'
    r'|為達最佳瀏覽效果|建議使用 ?chrome|請繼續往下閱讀|點擊立刻訂閱|電子報|版權所有|未經授權'
    r'|下載.{0,12}app|加入.{0,6}line|追蹤我們|延伸閱讀|相關新聞|責任編輯|贊助|google 新聞', re.I)
MIN_PARA = 12


def page_url(link):
    """A fetchable URL from a feed link: trims markup some feeds leave in it and
    percent-encodes non-ASCII paths."""
    link = re.split(r'["\s<>]', link.strip(), maxsplit=1)[0]
    u = urllib.parse.urlsplit(link)
    return urllib.parse.urlunsplit(u._replace(
        path=urllib.parse.quote(u.path, safe="/%:@!$&'()*+,;=~"),
        query=urllib.parse.quote(u.query, safe="=&%/:+,;~")))


def decode(raw, content_type=''):
    m = re.search(r'charset=([\w-]+)', content_type or '', re.I) or CHARSET.search(raw[:4000])
    enc = m.group(1) if m else 'utf-8'
    enc = enc.decode() if isinstance(enc, bytes) else enc
    try:
        return raw.decode(enc, 'replace')
    except LookupError:
        return raw.decode('utf-8', 'replace')


def _keep(paras, title=''):
    out, seen = [], set()
    for p in paras:
        if len(p) < MIN_PARA or JUNK.search(p[:60]) or p in seen or '<' in p or '="' in p or \
                (title and title.startswith(p)) or (title and p.startswith(title)):
            continue
        seen.add(p)
        out.append(p)
    return out


def extract(page):
    """Article paragraphs from an HTML page joined by newlines ('' if none found)."""
    page = INLINE.sub('', DROP.sub(' ', COMMENT.sub(' ', page)))
    t = TITLE.search(page)
    title = clean_text(t.group(1)) if t else ''
    m = ARTICLE.search(page)
    best = []
    for scope in ([m.group(0)] if m else []) + [page]:
        paras = _keep((clean_text(p) for p in PARA.findall(scope)), title)
        if sum(map(len, paras)) > sum(map(len, best)):
            best = paras
        if sum(map(len, best)) >= 200:
            break
    if sum(map(len, best)) < 200:
        # No <p> markup (e.g. RTHK): take the long runs of text between block tags.
        runs = _keep((clean_text(r) for r in BREAK.split(page)), title)
        runs = [r for r in runs if len(r) >= 30 and '{' not in r]
        if sum(map(len, runs)) > sum(map(len, best)):
            best = runs
    text = '\n'.join(best)
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT - 1].rstrip() + '…'
    return text


def fetch(link):
    """Full text of the story at link, or '' when it can't be had."""
    req = urllib.request.Request(page_url(link), headers={
        'User-Agent': USER_AGENT, 'Accept-Language': 'en,zh-Hant;q=0.8'})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        if 'html' not in (r.headers.get('content-type') or 'html'):
            return ''
        return extract(decode(r.read(MAX_PAGE), r.headers.get('content-type')))
