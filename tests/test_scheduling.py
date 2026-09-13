"""Draft, scheduled and published are three states, not two.

Public visibility requires BOTH status == published AND a publish date that
has arrived. Checking only the status made a future-dated article return 200
at its own URL while correctly staying out of listings, the sitemap and the
feed -- so an embargoed piece was reachable by anyone holding the link.
"""
from datetime import timedelta

from app.extensions import db
from app.models import Article, utcnow, STATUS_DRAFT, STATUS_PUBLISHED


def _article(author, **over):
    fields = dict(title='Scheduled Piece', slug='scheduled-piece',
                  body_markdown='Body.', body_html='<p>Body.</p>',
                  summary='Body.', status=STATUS_PUBLISHED,
                  published_at=utcnow() + timedelta(days=7), author=author)
    fields.update(over)
    article = Article(**fields)
    db.session.add(article)
    db.session.commit()
    return article


# --- a draft never publishes itself ---------------------------------------

def test_draft_with_a_future_date_never_publishes(client, author):
    """Setting a date on a draft must not schedule it. Only 'published'
    plus a date does that."""
    a = _article(author, status=STATUS_DRAFT, title='Still A Draft',
                 slug='still-a-draft')
    assert a.display_status == 'draft'
    assert a.is_published is False
    assert a.is_scheduled is False
    assert client.get(a.url).status_code == 404
    assert b'Still A Draft' not in client.get('/').data


def test_draft_with_a_past_date_still_does_not_publish(client, author):
    a = _article(author, status=STATUS_DRAFT, title='Past Draft',
                 slug='past-draft', published_at=utcnow() - timedelta(days=2))
    assert a.is_published is False
    assert client.get(a.url).status_code == 404


# --- the pending state ----------------------------------------------------

def test_scheduled_article_reads_as_pending(author):
    a = _article(author)
    assert a.display_status == 'pending'
    assert a.is_scheduled is True
    assert a.is_published is False, 'not live until its date arrives'


def test_scheduled_article_is_not_publicly_reachable(client, author):
    """Regression: this returned 200 before the date arrived."""
    a = _article(author)
    assert client.get(a.url).status_code == 404


def test_scheduled_article_is_absent_everywhere_public(client, author):
    a = _article(author)
    assert a.slug not in client.get('/sitemap.xml').get_data(as_text=True)
    assert a.slug not in client.get('/feed.xml').get_data(as_text=True)
    assert a.title.encode() not in client.get('/').data


def test_author_can_preview_a_scheduled_article(client, login, author):
    a = _article(author)
    login('authoruser')
    r = client.get(a.url)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert 'Scheduled for' in body, 'must not be labelled a draft'
    assert 'noindex' in body


def test_scheduled_becomes_live_when_its_date_arrives(client, author):
    a = _article(author)
    assert client.get(a.url).status_code == 404
    a.published_at = utcnow() - timedelta(minutes=1)
    db.session.commit()
    assert a.is_published is True
    assert a.is_scheduled is False
    assert a.display_status == 'published'
    assert client.get(a.url).status_code == 200
    assert a.title.encode() in client.get('/').data


def test_published_with_no_date_is_not_live(client, author):
    """An empty date must not be treated as 'now'."""
    a = _article(author, published_at=None)
    assert a.is_published is False
    assert client.get(a.url).status_code == 404


# --- what the editor sees -------------------------------------------------

def test_admin_list_shows_pending_not_published(client, login, admin, author):
    _article(author)
    login('adminuser')
    html = client.get('/admin/articles').get_data(as_text=True)
    assert 'pill-pending' in html
    assert '>pending<' in html


def test_admin_list_marks_the_date_as_scheduled(client, login, admin, author):
    _article(author)
    login('adminuser')
    assert 'scheduled' in client.get('/admin/articles').get_data(as_text=True)


def test_live_article_still_reads_as_published(client, login, admin, author,
                                               make_article):
    make_article(author, title='Actually Live')
    login('adminuser')
    html = client.get('/admin/articles').get_data(as_text=True)
    assert 'pill-published' in html


def _stat(html, label):
    import re
    m = re.search(r'stat-num">(\d+)</span> ' + label, html)
    assert m, f'no {label} stat on the dashboard'
    return int(m.group(1))


def test_dashboard_counts_pending_separately(client, login, admin, author,
                                             make_article):
    """Regression: every status=published article counted as published,
    so a batch of scheduled imports read as live."""
    make_article(author, title='Live One')
    _article(author)
    _article(author, title='Later Piece', slug='later-piece',
             published_at=utcnow() + timedelta(days=30))
    _article(author, status=STATUS_DRAFT, title='Draft', slug='draft')
    login('adminuser')
    html = client.get('/admin/').get_data(as_text=True)
    assert _stat(html, 'published') == 1
    assert _stat(html, 'pending') == 2
    assert _stat(html, 'drafts') == 1


def test_dashboard_shows_when_pending_articles_go_live(client, login, admin,
                                                       author):
    a = _article(author)
    login('adminuser')
    html = client.get('/admin/').get_data(as_text=True)
    assert 'Scheduled' in html
    assert a.published_at.strftime('%d %B %Y, %H:%M UTC') in html


def test_pending_filter_lists_only_scheduled(client, login, admin, author,
                                             make_article):
    make_article(author, title='Actually Live')
    _article(author)
    login('adminuser')
    html = client.get('/admin/articles?status=pending').get_data(as_text=True)
    assert 'Scheduled Piece' in html
    assert 'Actually Live' not in html
    live = client.get('/admin/articles?status=published').get_data(as_text=True)
    assert 'Actually Live' in live
    assert 'Scheduled Piece' not in live


def test_article_list_shows_scheduled_time(client, login, admin, author):
    a = _article(author)
    login('adminuser')
    html = client.get('/admin/articles').get_data(as_text=True)
    assert a.published_at.strftime('%H:%M UTC') in html


def test_pending_pill_is_styled(app):
    import os
    css = open(os.path.join(app.static_folder, 'css', 'style.css')).read()
    assert '.pill-pending' in css
