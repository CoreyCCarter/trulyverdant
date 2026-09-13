import logging
import os
from logging.handlers import RotatingFileHandler

from flask import Flask, request, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config, DEV_SECRET_KEY
from app.extensions import db, migrate, login, csrf


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    if not app.debug and not app.testing and \
            app.config['SECRET_KEY'] == DEV_SECRET_KEY:
        raise RuntimeError(
            'SECRET_KEY is not set. Refusing to start with the insecure '
            'development default: session cookies would be forgeable. '
            'Set SECRET_KEY in the environment or .env.')

    # Cookie flags come from Config as real values (see the note there about
    # setdefault being a no-op). Only the https requirement is relaxed here,
    # so the development server and the test client still work over http.
    if app.debug or app.testing:
        app.config['SESSION_COOKIE_SECURE'] = False
    elif not app.config['SESSION_COOKIE_SECURE']:
        # Easy to leave switched on after local http testing, and the
        # consequence -- session cookies sent in the clear -- is invisible.
        app.logger.warning(
            'SESSION_COOKIE_SECURE is false outside debug. Session cookies '
            'will be sent over plain http. Remove the override from .env '
            'once TLS is in place.')

    # Behind nginx, honour X-Forwarded-* so url_for(_external=True),
    # request.is_secure and the Secure cookie flag reflect the real request
    # rather than the local http hop from the proxy.
    if app.config.get('PROXY_FIX_HOPS'):
        hops = app.config['PROXY_FIX_HOPS']
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops,
                                x_host=hops, x_prefix=hops)

    db.init_app(app)
    migrate.init_app(app, db)
    login.init_app(app)
    csrf.init_app(app)

    from app.public import bp as public_bp
    app.register_blueprint(public_bp)

    from app.auth import bp as auth_bp
    app.register_blueprint(auth_bp, url_prefix='/auth')

    from app.admin import bp as admin_bp
    app.register_blueprint(admin_bp, url_prefix='/admin')

    from app.errors import bp as errors_bp
    app.register_blueprint(errors_bp)

    from app import cli
    cli.register(app)

    register_template_helpers(app)
    configure_logging(app)

    @app.after_request
    def security_headers(response):
        # Deliberately no Content-Security-Policy: AdSense injects scripts,
        # frames and images from a wide and changing set of Google hosts, and
        # a policy strict enough to be worth having would block ad serving.
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('Referrer-Policy',
                                    'strict-origin-when-cross-origin')
        response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')

        # Static assets are proxied through the app (nginx is on another
        # host), so without this every image revalidates on every page view
        # and crosses the tunnel again. Uploads are content-addressed by a
        # random stem and versioned assets carry ?v=, so neither filename is
        # ever reused -- both are safe to cache indefinitely.
        # Only on success: a year-long immutable cache on a 404 would
        # poison the browser cache for a URL that later becomes valid.
        if request.path.startswith('/static/') and \
                response.status_code in (200, 206, 304):
            if request.path.startswith('/static/uploads/') or \
                    request.args.get('v'):
                response.headers['Cache-Control'] = \
                    'public, max-age=31536000, immutable'
            else:
                response.headers['Cache-Control'] = 'public, max-age=3600'
        return response

    return app


def register_template_helpers(app):
    from app.images import image_url, image_srcset

    @app.context_processor
    def inject_globals():
        from flask_login import current_user
        cfg = app.config

        # Cookieless, so no consent gate. Excluded for signed-in staff and
        # for the admin and auth areas: at low traffic your own visits
        # otherwise dominate the numbers you are trying to read.
        analytics_enabled = bool(
            cfg['UMAMI_SCRIPT_URL'] and cfg['UMAMI_WEBSITE_ID']
            and not current_user.is_authenticated
            and not request.path.startswith(('/admin', '/auth')))
        # Ads need a publisher id, and are withheld until consent when
        # consent is required. Templates check `ads_enabled` before
        # emitting any Google script.
        from datetime import datetime, timezone
        consented = request.cookies.get('cookie_consent') == 'accepted'
        ads_configured = bool(cfg['ADSENSE_CLIENT_ID'])
        ads_enabled = ads_configured and (
            consented or not cfg['REQUIRE_COOKIE_CONSENT'])
        return {
            'site_name': cfg['SITE_NAME'],
            'site_tagline': cfg['SITE_TAGLINE'],
            'site_description': cfg['SITE_DESCRIPTION'],
            'site_url': cfg['SITE_URL'],
            'contact_email': cfg['CONTACT_EMAIL'],
            'seo_indexable': cfg['SEO_INDEXABLE'],
            'ads_configured': ads_configured,
            'ads_enabled': ads_enabled,
            'needs_consent': cfg['REQUIRE_COOKIE_CONSENT'] and not consented
                             and ads_configured,
            'adsense_client': cfg['ADSENSE_CLIENT_ID'],
            'slot_header': cfg['ADSENSE_SLOT_HEADER'],
            'slot_in_article': cfg['ADSENSE_SLOT_IN_ARTICLE'],
            'slot_sidebar': cfg['ADSENSE_SLOT_SIDEBAR'],
            'analytics_enabled': analytics_enabled,
            'umami_script_url': cfg['UMAMI_SCRIPT_URL'],
            'umami_website_id': cfg['UMAMI_WEBSITE_ID'],
            'now_year': datetime.now(timezone.utc).year,
        }

    def static_url(filename):
        """A /static URL carrying an mtime-derived version.

        Lets css/js be cached for a year while still updating on deploy;
        without the version they can only be cached briefly, because the
        filenames never change.
        """
        try:
            version = str(int(os.path.getmtime(
                os.path.join(app.static_folder, filename))))[-8:]
        except OSError:
            return url_for('static', filename=filename)
        return url_for('static', filename=filename, v=version)

    app.jinja_env.globals['static_url'] = static_url
    app.jinja_env.globals['image_url'] = image_url
    app.jinja_env.globals['image_srcset'] = image_srcset

    @app.template_filter('combine')
    def combine(base, extra):
        merged = dict(base)
        merged.update(extra or {})
        return merged

    @app.template_filter('humandate')
    def humandate(value):
        return value.strftime('%d %B %Y') if value else ''

    @app.template_filter('humandatetime')
    def humandatetime(value):
        """Date and time, labelled UTC: that is how publish times are stored
        and how the editor's date field is interpreted."""
        from app.models import as_utc
        value = as_utc(value)
        return value.strftime('%d %B %Y, %H:%M UTC') if value else ''

    @app.template_filter('isodate')
    def isodate(value):
        from app.models import as_utc
        value = as_utc(value)
        return value.isoformat() if value else ''


def configure_logging(app):
    if app.debug or app.testing:
        return
    os.makedirs('logs', exist_ok=True)
    handler = RotatingFileHandler('logs/trulyverdant.log',
                                  maxBytes=1_048_576, backupCount=10)
    handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'))
    handler.setLevel(logging.INFO)
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)
    app.logger.info('%s startup', app.config['SITE_NAME'])
