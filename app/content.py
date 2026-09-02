"""Markdown -> sanitised HTML, plus small text helpers.

Article bodies are stored twice: the Markdown source the author typed, and
the rendered HTML actually shown to readers. Rendering happens on save, so
page views stay cheap, and the HTML is sanitised at that point rather than
trusted at render time.
"""
import re

import markdown as md
import nh3

# Tags an author legitimately needs. Anything else (notably <script>,
# <style>, <iframe>, <form>) is stripped by nh3.
ALLOWED_TAGS = {
    'p', 'br', 'hr', 'span', 'div',
    'h2', 'h3', 'h4', 'h5', 'h6',
    'strong', 'b', 'em', 'i', 'u', 's', 'del', 'ins', 'mark', 'sub', 'sup',
    'ul', 'ol', 'li', 'dl', 'dt', 'dd',
    'blockquote', 'q', 'cite',
    'a', 'img', 'figure', 'figcaption', 'picture', 'source',
    'code', 'pre', 'kbd', 'samp', 'var',
    'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption', 'colgroup', 'col',
    'abbr', 'small', 'time',
}

ALLOWED_ATTRIBUTES = {
    '*': {'class', 'id', 'title'},
    'a': {'href', 'rel', 'target', 'class', 'title'},
    'img': {'src', 'alt', 'width', 'height', 'loading', 'decoding',
            'srcset', 'sizes', 'class', 'title'},
    'source': {'src', 'srcset', 'sizes', 'type', 'media'},
    'time': {'datetime'},
    'td': {'colspan', 'rowspan', 'align'},
    'th': {'colspan', 'rowspan', 'align', 'scope'},
    'ol': {'start', 'type'},
    'abbr': {'title'},
}

ALLOWED_URL_SCHEMES = {'http', 'https', 'mailto'}

_MD_EXTENSIONS = [
    'extra',        # tables, fenced code, footnotes, def lists, attr lists
    'sane_lists',
    'smarty',       # curly quotes and dashes
    'admonition',
    'toc',
]


def render_markdown(text, site_url=None):
    """Render Markdown to HTML, then sanitise. Never returns unsafe HTML."""
    if not text:
        return ''
    raw = md.markdown(text, extensions=_MD_EXTENSIONS, output_format='html')
    # The article title is the page's only <h1>; demote any the author typed
    # so the heading outline stays valid for search engines.
    raw = _H1_RE.sub(lambda m: f'<{m.group(1)}h2', raw)
    clean = nh3.clean(
        raw,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=ALLOWED_URL_SCHEMES,
        # rel is managed below so internal links are not needlessly
        # nofollowed, which would waste our own link equity.
        link_rel=None,
    )
    return _mark_external_links(clean, site_url)


_H1_RE = re.compile(r'<(/?)h1\b', re.IGNORECASE)
_ANCHOR_RE = re.compile(r'<a\s+([^>]*?)href="([^"]*)"([^>]*)>', re.IGNORECASE)


def _mark_external_links(html, site_url=None):
    """Add rel/target to outbound links only.

    Outbound links get nofollow (we do not vouch for them) and
    noopener/noreferrer (so the opened page cannot reach back via
    window.opener). Internal links are left alone.
    """
    host = ''
    if site_url:
        host = site_url.split('://')[-1].split('/')[0].lower()

    def repl(m):
        before, href, after = m.group(1), m.group(2), m.group(3)
        low = href.lower()
        external = low.startswith('http://') or low.startswith('https://')
        if external and host and host in low.split('/')[2:3]:
            external = False
        if not external:
            return m.group(0)
        attrs = (before + after).strip()
        attrs = re.sub(r'\s*(rel|target)="[^"]*"', '', attrs).strip()
        space = ' ' + attrs if attrs else ''
        return (f'<a href="{href}" rel="nofollow noopener noreferrer" '
                f'target="_blank"{space}>')

    return _ANCHOR_RE.sub(repl, html)


_TAG_RE = re.compile(r'<[^>]+>')
_WS_RE = re.compile(r'\s+')


def strip_html(html):
    return _WS_RE.sub(' ', _TAG_RE.sub(' ', html or '')).strip()


def summarise(html, limit=200):
    """First ~`limit` characters of prose, cut on a word boundary."""
    text = strip_html(html)
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(' ', 1)[0].rstrip(' ,;:.—-')
    return cut + '…'


def reading_time(text, wpm=220):
    words = len(strip_html(text).split())
    return max(1, round(words / wpm))


_PARA_CLOSE = re.compile(r'</p>', re.IGNORECASE)


def split_for_ad(html, after_paragraphs=3):
    """Split rendered HTML after the Nth paragraph.

    Lets the template drop one in-article ad into a natural break instead of
    interrupting mid-sentence. Returns (before, after); `after` is '' when the
    article is too short to be worth splitting.
    """
    if not html:
        return '', ''
    matches = list(_PARA_CLOSE.finditer(html))
    # Only split when there is a decent amount of article left afterwards.
    if len(matches) < after_paragraphs + 2:
        return html, ''
    idx = matches[after_paragraphs - 1].end()
    return html[:idx], html[idx:]
