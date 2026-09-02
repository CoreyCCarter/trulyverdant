"""The preflight command must catch configuration that fails silently."""
from app.cli import _preflight_checks


def _levels(app):
    return {name: (level, detail)
            for level, name, detail in _preflight_checks(app)}


def test_flags_dev_secret_key(app):
    from config import DEV_SECRET_KEY
    app.config['SECRET_KEY'] = DEV_SECRET_KEY
    assert _levels(app)['SECRET_KEY'][0] == 'fail'


def test_flags_localhost_site_url(app):
    app.config['SITE_URL'] = 'http://localhost:8000'
    assert _levels(app)['SITE_URL'][0] == 'fail'


def test_flags_insecure_session_cookie(app):
    app.config['SESSION_COOKIE_SECURE'] = False
    assert _levels(app)['SESSION_COOKIE_SECURE'][0] == 'fail'


def test_flags_wildcard_bind(app, monkeypatch):
    monkeypatch.setenv('GUNICORN_BIND', '0.0.0.0:8000')
    level, detail = _levels(app)['GUNICORN_BIND']
    assert level == 'fail'
    assert 'unauthenticated' in detail


def test_flags_loopback_bind_as_warning(app, monkeypatch):
    monkeypatch.setenv('GUNICORN_BIND', '127.0.0.1:8000')
    assert _levels(app)['GUNICORN_BIND'][0] == 'warn'


def test_flags_noindex(app):
    app.config['SEO_INDEXABLE'] = False
    assert _levels(app)['SEO_INDEXABLE'][0] == 'warn'


def test_flags_adsense_without_ads_txt(app):
    app.config['ADSENSE_CLIENT_ID'] = 'ca-pub-1234567890123456'
    app.config['ADS_TXT'] = ''
    level, detail = _levels(app)['adsense']
    assert level == 'warn'
    assert 'ads.txt' in detail


def test_flags_missing_admin(app):
    """A fresh database with no admin cannot be administered."""
    assert _levels(app)['admin account'][0] == 'fail'


def test_passes_a_correct_production_config(app, admin, monkeypatch):
    monkeypatch.setenv('GUNICORN_BIND', '10.8.0.2:8000')
    app.config.update(
        SECRET_KEY='x' * 48,
        SITE_URL='https://trulyverdant.com',
        SESSION_COOKIE_SECURE=True,
        PROXY_FIX_HOPS=1,
        SEO_INDEXABLE=True,
    )
    results = _levels(app)
    # The test fixture builds the schema with create_all(), so there is no
    # alembic_version row and the migration check cannot pass here. It is
    # exercised for real by test_flags_unmigrated_database below.
    failed = {k: v for k, v in results.items()
              if v[0] == 'fail' and k != 'database'}
    assert not failed, f'unexpected failures: {failed}'


def test_flags_unmigrated_database(app):
    """A schema that is not at head must fail, not warn: the app would
    raise on the first query touching a missing column."""
    level, detail = _levels(app)['database']
    assert level == 'fail'
    assert 'flask db upgrade' in detail
