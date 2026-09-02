from app.models import STATUS_DRAFT


def test_robots_lists_sitemap_and_blocks_admin(client):
    body = client.get('/robots.txt').get_data(as_text=True)
    assert 'Sitemap: https://example.test/sitemap.xml' in body
    assert 'Disallow: /admin/' in body


def test_sitemap_lists_published_only(client, author, make_article):
    pub = make_article(author, title='Listed Article')
    draft = make_article(author, title='Hidden Draft', status=STATUS_DRAFT)
    xml = client.get('/sitemap.xml').get_data(as_text=True)
    assert f'https://example.test{pub.url}' in xml
    assert draft.slug not in xml


def test_feed_is_valid_rss(client, author, make_article):
    make_article(author, title='Feed Item')
    r = client.get('/feed.xml')
    assert r.status_code == 200
    assert r.mimetype == 'application/rss+xml'
    body = r.get_data(as_text=True)
    assert '<rss version="2.0"' in body
    assert 'Feed Item' in body
    import xml.etree.ElementTree as ET
    ET.fromstring(body)          # raises if malformed


def test_sitemap_is_well_formed(client, author, make_article, category):
    make_article(author, title='Sitemap Article', category=category)
    import xml.etree.ElementTree as ET
    ET.fromstring(client.get('/sitemap.xml').get_data(as_text=True))


def test_ads_txt_404s_until_configured(client):
    assert client.get('/ads.txt').status_code == 404


def test_ads_txt_served_when_configured(app, client):
    app.config['ADS_TXT'] = 'google.com, pub-0000000000000000, DIRECT, f08c47fec0942fa0'
    r = client.get('/ads.txt')
    assert r.status_code == 200
    assert r.mimetype == 'text/plain'
    assert 'pub-0000000000000000' in r.get_data(as_text=True)


def test_noindex_when_site_marked_non_indexable(app, client):
    app.config['SEO_INDEXABLE'] = False
    assert 'Disallow: /\n' in client.get('/robots.txt').get_data(as_text=True)
    assert b'noindex' in client.get('/').data
