"""Performance and structured-data behaviour that affects discovery.

These are all things whose absence is invisible in normal use: a missing
cache header just makes the site slower, absent schema just means no rich
result, and a blank alt attribute only costs you image-search traffic.
"""
import io
import json
import os
import re
import xml.etree.ElementTree as ET

import pytest
from PIL import Image

from app.models import Article


def _png(size=(1200, 800)):
    buf = io.BytesIO()
    Image.new('RGB', size, (20, 120, 50)).save(buf, 'PNG')
    buf.seek(0)
    return buf


def _ld_blocks(html):
    return [json.loads(b) for b in re.findall(
        r'type="application/ld\+json">\s*(\{.*?\})\s*</script>', html, re.S)]


@pytest.fixture
def served_upload(app):
    """A real file under the served static folder.

    The app fixture points UPLOAD_FOLDER at a temp dir, but /static is
    served from app/static -- so a file must exist there to exercise the
    response headers.
    """
    folder = os.path.join(app.static_folder, 'uploads')
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, 'pytest_probe-480.webp')
    Image.new('RGB', (480, 320), (0, 0, 0)).save(path, 'WEBP')
    yield '/static/uploads/pytest_probe-480.webp'
    os.remove(path)


# --- static caching -------------------------------------------------------

def test_uploads_are_cached_immutably(client, served_upload):
    """Upload filenames carry a random stem and are never reused."""
    r = client.get(served_upload)
    assert r.status_code == 200
    assert 'immutable' in r.headers['Cache-Control']
    assert 'max-age=31536000' in r.headers['Cache-Control']


def test_versioned_assets_are_cached_immutably(client):
    r = client.get('/static/css/style.css?v=12345678')
    assert 'immutable' in r.headers['Cache-Control']


def test_unversioned_assets_get_a_short_cache(client):
    """Without a version in the URL a long cache would serve stale CSS
    after a deploy, because the filename never changes."""
    r = client.get('/static/css/style.css')
    assert r.headers['Cache-Control'] == 'public, max-age=3600'


def test_missing_static_file_is_not_cached(client):
    """A year-long immutable cache on a 404 would poison the browser cache
    for a URL that later becomes valid."""
    r = client.get('/static/uploads/definitely-not-here-480.webp')
    assert r.status_code == 404
    assert r.headers.get('Cache-Control') in (None, 'no-cache')


def test_stylesheet_url_carries_a_version(client):
    html = client.get('/').get_data(as_text=True)
    assert re.search(r'/static/css/style\.css\?v=\d+', html)


# --- sitewide structured data --------------------------------------------

def test_sitewide_entities_on_every_page(client, author, make_article):
    a = make_article(author, title='Anything')
    for path in ['/', '/about', a.url]:
        graph = _ld_blocks(client.get(path).get_data(as_text=True))[0]['@graph']
        types = {node['@type'] for node in graph}
        assert {'Organization', 'WebSite'} <= types, path


def test_search_action_declared(client):
    graph = _ld_blocks(client.get('/').get_data(as_text=True))[0]['@graph']
    site = [n for n in graph if n['@type'] == 'WebSite'][0]
    target = site['potentialAction']['target']['urlTemplate']
    assert target == 'https://example.test/search?q={search_term_string}'


def test_website_references_the_organization_by_id(client):
    """A shared @id is what ties articles to a publisher rather than
    leaving each one orphaned."""
    graph = _ld_blocks(client.get('/').get_data(as_text=True))[0]['@graph']
    org = [n for n in graph if n['@type'] == 'Organization'][0]
    site = [n for n in graph if n['@type'] == 'WebSite'][0]
    assert site['publisher']['@id'] == org['@id']


# --- breadcrumbs ----------------------------------------------------------

def test_breadcrumb_trail_includes_category(client, author, make_article,
                                            category):
    a = make_article(author, title='In A Category', category=category)
    crumbs = [d for d in _ld_blocks(client.get(a.url).get_data(as_text=True))
              if d.get('@type') == 'BreadcrumbList'][0]['itemListElement']
    assert [c['position'] for c in crumbs] == [1, 2, 3]
    assert [c['name'] for c in crumbs] == ['Home', category.name, a.title]
    assert crumbs[2]['item'] == f'https://example.test{a.url}'


def test_breadcrumb_omits_absent_category(client, author, make_article):
    a = make_article(author, title='Uncategorised')
    crumbs = [d for d in _ld_blocks(client.get(a.url).get_data(as_text=True))
              if d.get('@type') == 'BreadcrumbList'][0]['itemListElement']
    assert [c['position'] for c in crumbs] == [1, 2]
    assert crumbs[1]['name'] == a.title


# --- body images ----------------------------------------------------------

def test_local_body_images_get_lazy_loading_and_dimensions(app, author,
                                                           make_article,
                                                           client):
    from app.content import render_markdown
    from app.images import save_image
    from werkzeug.datastructures import FileStorage
    stored = save_image(FileStorage(stream=_png(), filename='x.png'))
    stem = stored.split(':')[0]
    html = render_markdown(f'![leaf](/static/uploads/{stem}-480.webp)')
    assert 'loading="lazy"' in html
    assert 'decoding="async"' in html
    # Real pixel dimensions, so the layout does not shift as it loads.
    assert 'width="480"' in html and 'height="320"' in html


def test_external_body_images_are_lazy_without_dimensions(app):
    """An external image's size cannot be known at render time."""
    from app.content import render_markdown
    html = render_markdown('![remote](https://cdn.test/x.jpg)')
    assert 'loading="lazy"' in html
    assert 'width=' not in html


# --- image sitemap --------------------------------------------------------

def test_sitemap_lists_article_images(app, client, author, make_article):
    from app.images import save_image
    from werkzeug.datastructures import FileStorage
    from app.extensions import db
    stored = save_image(FileStorage(stream=_png(), filename='x.png'))
    a = make_article(author, title='With A Picture')
    a.hero_image = stored
    db.session.commit()

    xml = client.get('/sitemap.xml').get_data(as_text=True)
    ET.fromstring(xml)
    locs = re.findall(r'<image:loc>([^<]+)</image:loc>', xml)
    assert locs, 'no image entries in the sitemap'
    assert all(u.startswith('https://example.test/static/uploads/')
               for u in locs)


def test_sitemap_has_no_image_entries_without_images(client, author,
                                                     make_article):
    make_article(author, title='Text Only')
    xml = client.get('/sitemap.xml').get_data(as_text=True)
    ET.fromstring(xml)
    assert '<image:loc>' not in xml


# --- alt text is mandatory when there is an image -------------------------

def _article_payload(**over):
    data = {'title': 'Alt Test', 'body_markdown': 'Body.', 'status': 'draft',
            'category': '0', 'summary': '', 'tags': '', 'hero_alt': '',
            'meta_description': '', 'slug': '', 'published_at': ''}
    data.update(over)
    return data


def test_upload_without_alt_text_is_rejected(client, login, author):
    login('authoruser')
    r = client.post('/admin/articles/new',
                    data=_article_payload(hero=(_png(), 'photo.png')),
                    content_type='multipart/form-data', follow_redirects=True)
    assert Article.query.filter_by(title='Alt Test').first() is None
    assert b'Describe the image' in r.data


def test_upload_with_alt_text_is_accepted(client, login, author):
    login('authoruser')
    client.post('/admin/articles/new',
                data=_article_payload(hero_alt='A green monstera leaf',
                                      hero=(_png(), 'photo.png')),
                content_type='multipart/form-data', follow_redirects=True)
    saved = Article.query.filter_by(title='Alt Test').one()
    assert saved.hero_alt == 'A green monstera leaf'


def test_alt_text_not_required_without_an_image(client, login, author):
    login('authoruser')
    client.post('/admin/articles/new', data=_article_payload(),
                content_type='multipart/form-data', follow_redirects=True)
    assert Article.query.filter_by(title='Alt Test').one().hero_image is None


def test_clearing_alt_on_an_existing_image_is_rejected(client, login, author,
                                                       make_article):
    """The image is still there, so it still needs a description."""
    from app.extensions import db
    from app.images import save_image
    from werkzeug.datastructures import FileStorage
    a = make_article(author, title='Has Hero')
    a.hero_image = save_image(FileStorage(stream=_png(), filename='x.png'))
    a.hero_alt = 'Original description'
    db.session.commit()

    login('authoruser')
    r = client.post(f'/admin/articles/{a.id}',
                    data=_article_payload(title='Has Hero', hero_alt=''),
                    content_type='multipart/form-data', follow_redirects=True)
    assert b'Describe the image' in r.data
    db.session.refresh(a)
    assert a.hero_alt == 'Original description'


def test_removing_the_image_clears_the_alt_requirement(client, login, author,
                                                       make_article):
    from app.extensions import db
    from app.images import save_image
    from werkzeug.datastructures import FileStorage
    a = make_article(author, title='Dropping Hero')
    a.hero_image = save_image(FileStorage(stream=_png(), filename='x.png'))
    a.hero_alt = 'Something'
    db.session.commit()

    login('authoruser')
    client.post(f'/admin/articles/{a.id}',
                data=_article_payload(title='Dropping Hero', hero_alt='',
                                      remove_hero='y'),
                content_type='multipart/form-data', follow_redirects=True)
    db.session.refresh(a)
    assert a.hero_image is None
