"""Authorisation boundaries and content sanitisation."""
import io

from PIL import Image

from app.models import Article, User, Invite, STATUS_DRAFT


# --- content sanitisation -------------------------------------------------

def test_script_in_markdown_never_reaches_the_page(client, login, author):
    login('authoruser')
    client.post('/admin/articles/new', data={
        'title': 'Nasty', 'body_markdown':
            '<script>alert(1)</script>\n\n<img src=x onerror=alert(2)>\n\n'
            '[x](javascript:alert(3))',
        'status': 'published', 'category': '0', 'summary': '', 'tags': '',
        'hero_alt': '', 'meta_description': '', 'slug': '', 'published_at': '',
    }, follow_redirects=True)
    article = Article.query.filter_by(title='Nasty').one()
    html = client.get(article.url).get_data(as_text=True)

    # The page legitimately contains the inline theme script, so assert on
    # the article body specifically rather than the whole document.
    body = html[html.index('<div class="prose">'):html.index('</article>')]
    assert '<script' not in body
    assert 'onerror' not in body
    assert 'javascript:alert' not in body
    # And the payload must not appear anywhere on the page, escaped or not.
    assert 'alert(1)' not in html
    assert 'alert(2)' not in html


def test_title_is_escaped_in_output(client, login, author, make_article):
    a = make_article(author, title='<script>alert(1)</script>')
    html = client.get(a.url).get_data(as_text=True)
    assert '<script>alert(1)</script>' not in html
    assert '&lt;script&gt;' in html


# --- admin access ---------------------------------------------------------

def test_admin_area_requires_login(client):
    for path in ['/admin/', '/admin/articles', '/admin/articles/new',
                 '/admin/categories', '/admin/people']:
        r = client.get(path)
        assert r.status_code == 302 and '/auth/login' in r.headers['Location']


def test_author_cannot_reach_admin_only_pages(client, login, author):
    login('authoruser')
    assert client.get('/admin/categories').status_code == 403
    assert client.get('/admin/people').status_code == 403


def test_admin_can_reach_admin_only_pages(client, login, admin):
    login('adminuser')
    assert client.get('/admin/categories').status_code == 200
    assert client.get('/admin/people').status_code == 200


def test_author_cannot_edit_another_authors_article(client, login, author,
                                                    other_author, make_article,
                                                    db):
    a = make_article(author, title='Mine')
    login('otheruser')
    assert client.get(f'/admin/articles/{a.id}').status_code == 403
    r = client.post(f'/admin/articles/{a.id}', data={
        'title': 'Hijacked', 'body_markdown': 'x', 'status': 'draft',
        'category': '0'})
    assert r.status_code == 403
    assert db.session.get(Article, a.id).title == 'Mine'


def test_author_cannot_delete_another_authors_article(client, login, author,
                                                      other_author,
                                                      make_article, db):
    a = make_article(author, title='Keep Me')
    login('otheruser')
    assert client.post(f'/admin/articles/{a.id}/delete').status_code == 403
    assert db.session.get(Article, a.id) is not None


def test_admin_can_edit_any_article(client, login, admin, author,
                                    make_article):
    a = make_article(author, title='Someone Elses')
    login('adminuser')
    assert client.get(f'/admin/articles/{a.id}').status_code == 200


# --- accounts and invites -------------------------------------------------

def test_there_is_no_public_registration(client):
    for path in ['/auth/register', '/register', '/signup', '/auth/signup']:
        assert client.get(path).status_code == 404


def test_invite_flow_creates_an_account(client, app, admin, db):
    invite = Invite.create('newbie@example.test', invited_by=admin)
    db.session.add(invite)
    db.session.commit()
    r = client.post(f'/auth/invite/{invite.token}', data={
        'username': 'newbie', 'display_name': 'New Bie',
        'password': 'a-long-enough-password', 'password2': 'a-long-enough-password',
    }, follow_redirects=True)
    assert r.status_code == 200
    assert User.query.filter_by(username='newbie').one().email == 'newbie@example.test'


def test_invite_cannot_be_reused(client, app, admin, db):
    invite = Invite.create('once@example.test', invited_by=admin)
    db.session.add(invite)
    db.session.commit()
    data = {'username': 'firstuse', 'display_name': '',
            'password': 'a-long-enough-password',
            'password2': 'a-long-enough-password'}
    client.post(f'/auth/invite/{invite.token}', data=data, follow_redirects=True)
    client.get('/auth/logout')
    data['username'] = 'seconduse'
    r = client.post(f'/auth/invite/{invite.token}', data=data)
    assert r.status_code == 404
    assert User.query.filter_by(username='seconduse').first() is None


def test_bad_invite_token_404s(client):
    assert client.get('/auth/invite/not-a-real-token').status_code == 404


def test_deactivated_user_cannot_sign_in(client, login, author, db):
    author.is_active_user = False
    db.session.commit()
    r = login('authoruser')
    assert b'deactivated' in r.data
    assert client.get('/admin/').status_code == 302


def test_login_does_not_reveal_whether_a_user_exists(client):
    a = client.post('/auth/login', data={'username': 'ghost',
                                         'password': 'whatever'},
                    follow_redirects=True)
    b = client.post('/auth/login', data={'username': 'authoruser',
                                         'password': 'wrong'},
                    follow_redirects=True)
    assert b'Invalid credentials' in a.data and b'Invalid credentials' in b.data


def test_logout_rejects_get(client, login, author):
    login('authoruser')
    assert client.get('/auth/logout').status_code == 405


def test_open_redirect_is_blocked(client, author):
    r = client.post('/auth/login?next=https://evil.test/steal',
                    data={'username': 'authoruser',
                          'password': 'correct-horse-battery'})
    assert 'evil.test' not in r.headers.get('Location', '')


# --- uploads --------------------------------------------------------------

def _png_bytes(size=(60, 40)):
    buf = io.BytesIO()
    Image.new('RGB', size, (10, 120, 40)).save(buf, 'PNG')
    buf.seek(0)
    return buf


def test_image_upload_produces_webp_variants(client, login, author, app):
    login('authoruser')
    r = client.post('/admin/articles/new', data={
        'title': 'With Image', 'body_markdown': 'Body.', 'status': 'draft',
        'category': '0', 'summary': '', 'tags': '', 'hero_alt': 'A green square',
        'meta_description': '', 'slug': '', 'published_at': '',
        'hero': (_png_bytes(), 'photo.png'),
    }, content_type='multipart/form-data', follow_redirects=True)
    assert r.status_code == 200
    article = Article.query.filter_by(title='With Image').one()
    assert article.hero_image, 'no image stored'
    import os
    stem = article.hero_image.split(':')[0]
    files = os.listdir(app.config['UPLOAD_FOLDER'])
    assert any(f.startswith(stem) and f.endswith('.webp') for f in files)


def test_non_image_upload_is_rejected(client, login, author):
    login('authoruser')
    r = client.post('/admin/articles/new', data={
        'title': 'Bad Upload', 'body_markdown': 'Body.', 'status': 'draft',
        'category': '0', 'summary': '', 'tags': '', 'hero_alt': '',
        'meta_description': '', 'slug': '', 'published_at': '',
        'hero': (io.BytesIO(b'#!/bin/sh\nrm -rf /'), 'evil.sh'),
    }, content_type='multipart/form-data', follow_redirects=True)
    assert Article.query.filter_by(title='Bad Upload').first() is None
    assert b'Images only' in r.data or b'Unsupported file type' in r.data


def test_image_extension_lie_is_rejected(client, login, author):
    """A shell script renamed to .png must not be accepted."""
    login('authoruser')
    client.post('/admin/articles/new', data={
        'title': 'Liar', 'body_markdown': 'Body.', 'status': 'draft',
        'category': '0', 'summary': '', 'tags': '', 'hero_alt': '',
        'meta_description': '', 'slug': '', 'published_at': '',
        'hero': (io.BytesIO(b'not really an image'), 'evil.png'),
    }, content_type='multipart/form-data', follow_redirects=True)
    assert Article.query.filter_by(title='Liar').first() is None


# --- session cookie flags -------------------------------------------------

def test_session_cookie_flags_are_actually_applied(app):
    """Regression: Flask pre-defines these keys, so setdefault() on them is
    a silent no-op and the hardening never takes effect."""
    assert app.config['SESSION_COOKIE_HTTPONLY'] is True
    assert app.config['SESSION_COOKIE_SAMESITE'] == 'Lax'


def test_session_cookie_secure_survives_create_app(monkeypatch):
    """create_app must not quietly clear Secure outside debug/testing.

    Set explicitly rather than relying on the ambient .env, which may carry
    SESSION_COOKIE_SECURE=false for local http development.
    """
    from app import create_app
    from config import Config

    class Prod(Config):
        SECRET_KEY = 'not-the-dev-default'
        SQLALCHEMY_DATABASE_URI = 'sqlite://'
        SESSION_COOKIE_SECURE = True

    prod = create_app(Prod)
    assert prod.config['SESSION_COOKIE_SECURE'] is True, \
        'session cookie must keep Secure when not in debug/testing'


def test_secure_cookie_defaults_to_true_when_env_unset():
    """Unset must mean Secure. Tests the decision directly: reloading the
    config module cannot isolate this, because load_dotenv repopulates the
    environment from .env, which may carry a local-http override."""
    from config import _bool
    assert _bool(None, True) is True          # env var absent -> Secure
    assert _bool('false', True) is False      # explicit opt-out honoured
    assert _bool('true', True) is True


def test_insecure_cookie_outside_debug_is_logged(caplog):
    """Serving https with a non-Secure session cookie is invisible in
    behaviour, so it must be loud in the logs."""
    import logging
    from app import create_app
    from config import Config

    class Sloppy(Config):
        SECRET_KEY = 'not-the-dev-default'
        SQLALCHEMY_DATABASE_URI = 'sqlite://'
        SESSION_COOKIE_SECURE = False

    with caplog.at_level(logging.WARNING):
        create_app(Sloppy)
    assert any('SESSION_COOKIE_SECURE is false' in r.message
               for r in caplog.records)
