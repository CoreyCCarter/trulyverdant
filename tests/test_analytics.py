"""Umami is cookieless, so it loads without a consent gate -- but it must
stay off the admin area and off staff visits, which would otherwise
dominate the numbers at low traffic."""
from app.cli import _preflight_checks


def _levels(app):
    return {name: (level, detail)
            for level, name, detail in _preflight_checks(app)}


def _configure(app):
    app.config['UMAMI_SCRIPT_URL'] = '/stats.js'
    app.config['UMAMI_WEBSITE_ID'] = 'abc-123'


# --- script emission ------------------------------------------------------

def test_no_script_when_unconfigured(client):
    assert b'stats.js' not in client.get('/').data


def test_script_emitted_when_configured(app, client):
    _configure(app)
    html = client.get('/').get_data(as_text=True)
    assert 'src="/stats.js"' in html
    assert 'data-website-id="abc-123"' in html


def test_half_configured_emits_nothing(app, client):
    """A script URL with no website id would load and silently record
    nothing, which looks like working analytics."""
    app.config['UMAMI_SCRIPT_URL'] = '/stats.js'
    app.config['UMAMI_WEBSITE_ID'] = ''
    assert b'stats.js' not in client.get('/').data


def test_staff_visits_are_not_tracked(app, client, login, author,
                                      make_article):
    _configure(app)
    a = make_article(author, title='Anything')
    assert 'stats.js' in client.get(a.url).get_data(as_text=True)
    login('authoruser')
    assert 'stats.js' not in client.get(a.url).get_data(as_text=True)


def test_auth_pages_are_not_tracked(app, client):
    _configure(app)
    assert b'stats.js' not in client.get('/auth/login').data


def test_admin_pages_are_not_tracked(app, client, login, admin):
    _configure(app)
    login('adminuser')
    assert b'stats.js' not in client.get('/admin/').data


def test_script_is_deferred(app, client):
    """Analytics must never block rendering."""
    _configure(app)
    html = client.get('/').get_data(as_text=True)
    assert 'defer' in html[html.index('stats.js') - 120:html.index('stats.js')]


# --- preflight ------------------------------------------------------------

def test_preflight_warns_when_unconfigured(app):
    level, detail = _levels(app)['analytics']
    assert level == 'warn'
    assert 'not configured' in detail


def test_preflight_fails_when_half_configured(app):
    app.config['UMAMI_SCRIPT_URL'] = '/stats.js'
    app.config['UMAMI_WEBSITE_ID'] = ''
    assert _levels(app)['analytics'][0] == 'fail'


def test_preflight_ok_when_configured(app):
    _configure(app)
    assert _levels(app)['analytics'][0] == 'ok'
