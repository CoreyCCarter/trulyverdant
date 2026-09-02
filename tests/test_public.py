from app.models import STATUS_DRAFT, STATUS_PUBLISHED


def test_homepage_is_public(client, author, make_article):
    make_article(author, title='Public Piece')
    r = client.get('/')
    assert r.status_code == 200
    assert b'Public Piece' in r.data


def test_article_page_renders(client, author, make_article):
    a = make_article(author, title='Monstera Care')
    r = client.get(a.url)
    assert r.status_code == 200
    assert b'Monstera Care' in r.data


def test_draft_is_404_for_the_public(client, author, make_article):
    a = make_article(author, title='Secret Draft', status=STATUS_DRAFT)
    assert client.get(a.url).status_code == 404
    assert b'Secret Draft' not in client.get('/').data


def test_author_can_preview_own_draft(client, login, author, make_article):
    a = make_article(author, title='My Draft', status=STATUS_DRAFT)
    login('authoruser')
    r = client.get(a.url)
    assert r.status_code == 200
    assert b'Draft preview' in r.data
    # A draft must never be indexable, even when it renders.
    assert b'noindex' in r.data


def test_other_author_cannot_see_draft(client, login, author, other_author,
                                       make_article):
    a = make_article(author, title='Not Yours', status=STATUS_DRAFT)
    login('otheruser')
    assert client.get(a.url).status_code == 404


def test_search_finds_articles(client, author, make_article):
    make_article(author, title='Yellow Leaves', body='About chlorosis.')
    r = client.get('/search?q=chlorosis')
    assert r.status_code == 200
    assert b'Yellow Leaves' in r.data


def test_category_and_tag_pages(client, author, make_article, category):
    make_article(author, title='In A Category', category=category)
    r = client.get(f'/category/{category.slug}')
    assert r.status_code == 200
    assert b'In A Category' in r.data


def test_canonical_and_description_present(client, author, make_article):
    a = make_article(author, title='Canonical Test', body='Body prose here.')
    html = client.get(a.url).get_data(as_text=True)
    assert f'<link rel="canonical" href="https://example.test{a.url}">' in html
    assert '<meta name="description"' in html
    assert 'application/ld+json' in html
