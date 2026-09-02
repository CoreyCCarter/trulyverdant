import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app                       # noqa: E402
from app.extensions import db as _db             # noqa: E402
from app.models import (User, Article, Category, utcnow,                 # noqa: E402
                        ROLE_ADMIN, ROLE_AUTHOR, STATUS_PUBLISHED)
from config import Config, _normalise_db_url     # noqa: E402


# Default to in-memory SQLite for speed. Set TEST_DATABASE_URL to run the
# suite against a real Postgres instead -- CI does exactly that, so dialect
# differences show up there rather than in production.
_TEST_DB = os.environ.get('TEST_DATABASE_URL')


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = _normalise_db_url(_TEST_DB) or 'sqlite://'
    WTF_CSRF_ENABLED = False
    SITE_URL = 'https://example.test'
    SERVER_NAME = 'example.test'
    ADSENSE_CLIENT_ID = ''
    ADS_TXT = ''


@pytest.fixture
def app():
    upload_dir = tempfile.mkdtemp()

    class Cfg(TestConfig):
        UPLOAD_FOLDER = upload_dir

    application = create_app(Cfg)
    with application.app_context():
        _db.create_all()
        yield application
        _db.session.remove()
        _db.drop_all()


@pytest.fixture
def db(app):
    return _db


@pytest.fixture
def client(app):
    return app.test_client()


def _make_user(username, role=ROLE_AUTHOR, password='correct-horse-battery'):
    user = User(username=username, email=f'{username}@example.test', role=role)
    user.set_password(password)
    _db.session.add(user)
    _db.session.commit()
    return user


@pytest.fixture
def admin(app):
    return _make_user('adminuser', role=ROLE_ADMIN)


@pytest.fixture
def author(app):
    return _make_user('authoruser', role=ROLE_AUTHOR)


@pytest.fixture
def other_author(app):
    return _make_user('otheruser', role=ROLE_AUTHOR)


@pytest.fixture
def login(client):
    def _login(username, password='correct-horse-battery'):
        return client.post('/auth/login',
                           data={'username': username, 'password': password},
                           follow_redirects=True)
    return _login


@pytest.fixture
def make_article(app):
    def _make(author, title='A Test Article', status=STATUS_PUBLISHED,
              body='Body text here.', category=None):
        from app.content import render_markdown, summarise
        from app.models import unique_slug
        html = render_markdown(body)
        article = Article(
            title=title, slug=unique_slug(Article, title),
            body_markdown=body, body_html=html, summary=summarise(html),
            status=status, author=author, category=category,
            published_at=utcnow() if status == STATUS_PUBLISHED else None)
        _db.session.add(article)
        _db.session.commit()
        return article
    return _make


@pytest.fixture
def category(app):
    from app.models import unique_slug
    cat = Category(name='Houseplants', slug=unique_slug(Category, 'Houseplants'))
    _db.session.add(cat)
    _db.session.commit()
    return cat
