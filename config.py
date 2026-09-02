import os
from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, '.env'))

DEV_SECRET_KEY = 'dev-only-insecure-key'


def _bool(value, default=False):
    if value is None:
        return default
    return value.strip().lower() in ('1', 'true', 'yes', 'on')


def _normalise_db_url(url):
    """Point legacy postgres:// URLs at the installed psycopg3 driver."""
    if not url:
        return None
    if url.startswith('postgres://'):
        url = 'postgresql://' + url[len('postgres://'):]
    if url.startswith('postgresql://'):
        url = 'postgresql+psycopg://' + url[len('postgresql://'):]
    return url


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or DEV_SECRET_KEY

    SQLALCHEMY_DATABASE_URI = _normalise_db_url(os.environ.get('DATABASE_URL')) \
        or 'sqlite:///' + os.path.join(basedir, 'dev.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # --- Site identity (used in titles, feeds, structured data) -------------
    SITE_NAME = os.environ.get('SITE_NAME', 'TrulyVerdant')
    SITE_TAGLINE = os.environ.get(
        'SITE_TAGLINE', 'Field notes and growing guides for plant people')
    SITE_DESCRIPTION = os.environ.get(
        'SITE_DESCRIPTION',
        'In-depth articles on houseplants, gardening and plant care, '
        'written by people who actually grow them.')
    # Absolute base URL, no trailing slash. Required for canonical URLs,
    # the sitemap and the RSS feed to be valid.
    SITE_URL = (os.environ.get('SITE_URL') or 'http://localhost:5000').rstrip('/')
    SITE_AUTHOR = os.environ.get('SITE_AUTHOR', 'TrulyVerdant')
    CONTACT_EMAIL = os.environ.get('CONTACT_EMAIL', '')

    ARTICLES_PER_PAGE = int(os.environ.get('ARTICLES_PER_PAGE') or 10)

    # --- Uploads ------------------------------------------------------------
    UPLOAD_FOLDER = os.environ.get(
        'UPLOAD_FOLDER', os.path.join(basedir, 'app', 'static', 'uploads'))
    MAX_CONTENT_LENGTH = int(os.environ.get('MAX_UPLOAD_MB') or 10) * 1024 * 1024
    ALLOWED_IMAGE_EXTENSIONS = {'jpg', 'jpeg', 'png', 'webp', 'gif'}
    # Widths generated for responsive srcset, plus the max stored width.
    IMAGE_WIDTHS = (480, 960, 1600)

    # --- AdSense ------------------------------------------------------------
    # Ads render only when a client id is configured AND the visitor has
    # consented, so local development and pre-approval stay ad-free.
    ADSENSE_CLIENT_ID = os.environ.get('ADSENSE_CLIENT_ID', '').strip()
    ADSENSE_SLOT_HEADER = os.environ.get('ADSENSE_SLOT_HEADER', '').strip()
    ADSENSE_SLOT_IN_ARTICLE = os.environ.get('ADSENSE_SLOT_IN_ARTICLE', '').strip()
    ADSENSE_SLOT_SIDEBAR = os.environ.get('ADSENSE_SLOT_SIDEBAR', '').strip()
    # Served verbatim at /ads.txt. AdSense will not serve without this.
    ADS_TXT = os.environ.get('ADS_TXT', '').strip()
    # Blocks ad + analytics scripts until the visitor accepts. Required in
    # the EEA/UK, where Google also requires a certified CMP.
    REQUIRE_COOKIE_CONSENT = _bool(os.environ.get('REQUIRE_COOKIE_CONSENT'), True)

    # Discourage indexing of a staging deployment.
    SEO_INDEXABLE = _bool(os.environ.get('SEO_INDEXABLE'), True)

    # --- Session cookie -----------------------------------------------------
    # These must be set as real config values. Flask already defines all
    # three keys, so app.config.setdefault() on them is silently a no-op.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    # Secure cookies are only sent over https. create_app() relaxes this in
    # debug/testing; set it false explicitly to serve over plain http, e.g.
    # a local nginx without a certificate -- otherwise sign-in appears to do
    # nothing, because the browser silently discards the session cookie.
    SESSION_COOKIE_SECURE = _bool(os.environ.get('SESSION_COOKIE_SECURE'), True)

    # Number of trusted reverse proxies in front of the app. Set to 1 when
    # running behind nginx; leave 0 when Flask/gunicorn is exposed directly.
    # Trusting more hops than actually exist lets a client spoof its own IP
    # and scheme via forged X-Forwarded-* headers.
    PROXY_FIX_HOPS = int(os.environ.get('PROXY_FIX_HOPS') or 0)
