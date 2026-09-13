from app.models import STATUS_DRAFT, STATUS_PUBLISHED


def test_homepage_is_public(client, author, make_article):
    make_article(author, title='Public Piece')
    r = client.get('/')
    assert r.status_code == 200
    assert b'Public Piece' in r.data


def test_categories_are_not_in_the_header_nav(client, author, make_article):
    import re
    from app.extensions import db
    from app.models import Category
    cat = Category(name='Succulents', slug='succulents')
    db.session.add(cat)
    db.session.commit()
    make_article(author, title='Filed Piece', category=cat)
    html = client.get('/').get_data(as_text=True)
    nav = re.search(r'<nav id="nav".*?</nav>', html, re.S).group(0)
    assert cat.url not in nav
    assert 'Succulents' not in nav
    assert cat.url in html, 'category pages must stay reachable elsewhere'


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


# --- login is unlisted ----------------------------------------------------

def test_no_public_page_links_to_login(client, author, make_article,
                                       category):
    """The sign-in URL must be reachable only by typing it. Nothing a reader
    can see should advertise it."""
    make_article(author, title='Listed', category=category)
    for path in ['/', '/about', '/contact', '/privacy', '/search',
                 f'/category/{category.slug}', '/author/authoruser']:
        html = client.get(path).get_data(as_text=True)
        assert '/auth/login' not in html, f'{path} links to the login page'


def test_login_still_reachable_directly(client):
    assert client.get('/auth/login').status_code == 200


def test_auth_pages_are_noindex(client):
    """Otherwise the login form turns up in search results, which defeats
    the point of keeping it unlisted."""
    assert b'noindex' in client.get('/auth/login').data


def test_signed_in_staff_still_get_an_admin_link(client, login, author,
                                                 make_article):
    make_article(author, title='Anything')
    login('authoruser')
    assert '/admin/' in client.get('/').get_data(as_text=True)


def test_robots_disallows_auth(client):
    assert 'Disallow: /auth/' in client.get('/robots.txt').get_data(as_text=True)


# --- summary styling ------------------------------------------------------

def test_summary_is_italic_everywhere_it_appears(client, author, make_article,
                                                 app):
    import os, re
    a = make_article(author, title='Italic Summary', body='Some prose here.')
    assert 'article-summary' in client.get(a.url).get_data(as_text=True)
    assert 'card-summary' in client.get('/').get_data(as_text=True)

    css = open(os.path.join(app.static_folder, 'css', 'style.css')).read()
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    rule = re.search(r'\.article-summary,\s*\.card-summary\s*\{([^}]*)\}', css)
    assert rule and 'font-style: italic' in rule.group(1)


def test_lede_itself_is_not_italic(app):
    """.lede is shared with the tagline, category intros, author bios and
    error messages. Only summaries should italicise."""
    import os, re
    css = open(os.path.join(app.static_folder, 'css', 'style.css')).read()
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    lede = re.search(r'(?m)^\.lede\s*\{([^}]*)\}', css)
    assert lede and 'italic' not in lede.group(1)
