import click
from flask import current_app

from app.extensions import db
from app.models import (User, Article, Category, Tag, Invite, unique_slug,
                        utcnow, ROLE_ADMIN, ROLE_AUTHOR, STATUS_PUBLISHED)
from app.content import render_markdown, summarise, reading_time


def _preflight_checks(app):
    """Yield (level, name, detail) for each production readiness check.

    Level is 'ok', 'warn' or 'fail'. These target settings whose failure
    mode is silence: a stale SECRET_KEY forges sessions, a Secure cookie
    mismatched to the scheme makes sign-in appear to do nothing, and
    SEO_INDEXABLE left false quietly tells Google to go away.
    """
    import os
    from urllib.parse import urlsplit
    from sqlalchemy import text
    from config import DEV_SECRET_KEY

    cfg = app.config

    # --- secrets ---------------------------------------------------------
    key = cfg.get('SECRET_KEY') or ''
    if key == DEV_SECRET_KEY:
        yield 'fail', 'SECRET_KEY', 'still the insecure development default'
    elif len(key) < 32:
        yield 'warn', 'SECRET_KEY', f'only {len(key)} chars; use 32+'
    else:
        yield 'ok', 'SECRET_KEY', f'set ({len(key)} chars)'

    # --- public identity -------------------------------------------------
    site = cfg.get('SITE_URL') or ''
    parts = urlsplit(site)
    if not site or parts.hostname in (None, 'localhost', '127.0.0.1'):
        yield 'fail', 'SITE_URL', f'{site!r} is not a public address'
    elif parts.scheme != 'https':
        yield 'warn', 'SITE_URL', f'{site} is not https'
    elif site.endswith('/'):
        yield 'warn', 'SITE_URL', 'has a trailing slash; URLs will double up'
    else:
        yield 'ok', 'SITE_URL', site

    # --- cookies and proxying -------------------------------------------
    if not cfg.get('SESSION_COOKIE_SECURE'):
        yield 'fail', 'SESSION_COOKIE_SECURE', \
            'false: session cookies will be sent in the clear'
    else:
        yield 'ok', 'SESSION_COOKIE_SECURE', 'true'

    hops = cfg.get('PROXY_FIX_HOPS', 0)
    if hops < 1:
        yield 'warn', 'PROXY_FIX_HOPS', \
            '0: client IP and scheme will be the proxy hop, not the browser'
    else:
        yield 'ok', 'PROXY_FIX_HOPS', str(hops)

    # --- indexing --------------------------------------------------------
    if not cfg.get('SEO_INDEXABLE'):
        yield 'warn', 'SEO_INDEXABLE', \
            'false: emits noindex and blocks crawlers (correct only for staging)'
    else:
        yield 'ok', 'SEO_INDEXABLE', 'true'

    # --- bind address ----------------------------------------------------
    bind = os.environ.get('GUNICORN_BIND', '127.0.0.1:8000')
    if bind.startswith('0.0.0.0'):
        yield 'fail', 'GUNICORN_BIND', \
            '0.0.0.0 exposes the unauthenticated app on every interface'
    elif bind.startswith(('127.', 'localhost', 'unix:')):
        yield 'warn', 'GUNICORN_BIND', \
            f'{bind}: unreachable from a separate nginx host'
    else:
        yield 'ok', 'GUNICORN_BIND', bind

    # --- database --------------------------------------------------------
    try:
        from alembic.script import ScriptDirectory
        from alembic.runtime.migration import MigrationContext
        acfg = app.extensions['migrate'].migrate.get_config()
        head = ScriptDirectory.from_config(acfg).get_current_head()
        with db.engine.connect() as conn:
            conn.execute(text('select 1'))
            at = MigrationContext.configure(conn).get_current_revision()
        if at == head:
            yield 'ok', 'database', f'reachable, schema at head ({head})'
        else:
            yield 'fail', 'database', \
                f'schema at {at or "nothing"}, head is {head} -- run flask db upgrade'
    except Exception as exc:
        yield 'fail', 'database', f'{type(exc).__name__}: {str(exc)[:80]}'
        return

    # --- accounts --------------------------------------------------------
    try:
        admins = User.query.filter_by(role=ROLE_ADMIN, is_active_user=True).count()
        if admins:
            yield 'ok', 'admin account', f'{admins} active'
        else:
            yield 'fail', 'admin account', 'none -- run flask create-admin'
    except Exception as exc:
        yield 'fail', 'admin account', str(exc)[:80]

    # --- uploads ---------------------------------------------------------
    updir = cfg.get('UPLOAD_FOLDER')
    if updir and os.path.isdir(updir) and os.access(updir, os.W_OK):
        yield 'ok', 'upload folder', updir
    elif updir and not os.path.isdir(updir):
        yield 'warn', 'upload folder', f'{updir} does not exist yet'
    else:
        yield 'fail', 'upload folder', f'{updir} is not writable'

    # --- advertising (informational) -------------------------------------
    if cfg.get('ADSENSE_CLIENT_ID'):
        if cfg.get('ADS_TXT'):
            yield 'ok', 'adsense', 'client id and ads.txt configured'
        else:
            yield 'warn', 'adsense', \
                'client id set but ADS_TXT empty: /ads.txt 404s and ads will not serve'
    else:
        yield 'ok', 'adsense', 'not configured (no ad code emitted)'


def register(app):
    @app.cli.command('preflight')
    def preflight():
        """Check production configuration before starting."""
        symbols = {'ok': ('  ok  ', 'green'), 'warn': (' warn ', 'yellow'),
                   'fail': (' FAIL ', 'red')}
        failures = warnings = 0
        click.echo('')
        for level, name, detail in _preflight_checks(app):
            mark, colour = symbols[level]
            if level == 'fail':
                failures += 1
            elif level == 'warn':
                warnings += 1
            click.echo(f'{click.style(mark, fg=colour, reverse=True)} '
                       f'{name:<22} {detail}')
        click.echo('')
        if failures:
            raise click.ClickException(
                f'{failures} check(s) failed. Fix these before serving traffic.')
        if warnings:
            click.echo(click.style(
                f'{warnings} warning(s). Review before going live.', fg='yellow'))
        else:
            click.echo(click.style('All checks passed.', fg='green'))

    @app.cli.command('create-admin')
    @click.option('--username', prompt=True)
    @click.option('--email', prompt=True)
    @click.password_option()
    def create_admin(username, email, password):
        """Create the first administrator account."""
        username, email = username.strip().lower(), email.strip().lower()
        if User.query.filter_by(username=username).first():
            raise click.ClickException(f'Username {username!r} already exists.')
        if User.query.filter_by(email=email).first():
            raise click.ClickException(f'Email {email!r} already exists.')
        user = User(username=username, email=email, role=ROLE_ADMIN)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f'Admin {username!r} created.')

    @app.cli.command('invite')
    @click.argument('email')
    @click.option('--role', type=click.Choice([ROLE_ADMIN, ROLE_AUTHOR]),
                  default=ROLE_AUTHOR)
    def invite(email, role):
        """Create an invitation link for a new writer."""
        if User.query.filter_by(email=email.strip().lower()).first():
            raise click.ClickException('That email already has an account.')
        inv = Invite.create(email, role=role)
        db.session.add(inv)
        db.session.commit()
        click.echo('Send this link (valid 14 days):')
        click.echo(f"  {current_app.config['SITE_URL']}/auth/invite/{inv.token}")

    @app.cli.command('seed-demo')
    def seed_demo():
        """Populate categories and a few sample articles for local preview."""
        author = User.query.order_by(User.id).first()
        if author is None:
            raise click.ClickException('Create an admin first: flask create-admin')

        specs = [
            ('Houseplants', 'Indoor growing, light, watering and repotting.'),
            ('Propagation', 'Making more plants from the ones you have.'),
            ('Troubleshooting', 'Diagnosing pests, rot and unhappy leaves.'),
        ]
        cats = {}
        for name, desc in specs:
            cat = Category.query.filter_by(name=name).first()
            if cat is None:
                cat = Category(name=name, slug=unique_slug(Category, name),
                               description=desc)
                db.session.add(cat)
            cats[name] = cat
        db.session.flush()

        samples = [
            ('Why your monstera leaves are not splitting', 'Houseplants',
             'light, monstera',
             "Fenestration is a maturity signal, not a health one. A young "
             "monstera makes solid leaves because it has not yet accumulated "
             "the resources to build holes.\n\n"
             "## Light is almost always the answer\n\n"
             "The single most common cause of unsplit leaves on an otherwise "
             "healthy plant is insufficient light. A monstera in a north "
             "window is surviving, not thriving.\n\n"
             "### What to change\n\n"
             "Move the plant within a metre of an east or west facing window. "
             "Expect the next new leaf, not the existing ones, to show the "
             "difference — leaves do not retroactively fenestrate.\n\n"
             "## Give it something to climb\n\n"
             "In the wild a monstera is a climber. Without a support it stays "
             "in its juvenile form indefinitely. A moss pole changes the "
             "growth habit within a season.\n\n"
             "## Be patient with the timeline\n\n"
             "A healthy indoor monstera pushes a new leaf every four to six "
             "weeks in the growing season. Judge your changes over months."),
            ('Propagating pothos in water, properly', 'Propagation',
             'propagation, pothos',
             "Almost any pothos cutting will root in water. Whether it "
             "survives the move to soil is a different question.\n\n"
             "## Cut below a node\n\n"
             "The node is the small brown nub where a leaf meets the vine. "
             "Roots emerge from there and nowhere else. A cutting without a "
             "node will sit in water indefinitely and then rot.\n\n"
             "## Change the water\n\n"
             "Every three days. Stagnant water runs out of dissolved oxygen "
             "and roots suffocate.\n\n"
             "## Move to soil early\n\n"
             "Water roots and soil roots are structurally different. The "
             "longer a cutting grows in water, the harder the transition. "
             "Pot up once roots reach three to five centimetres.\n\n"
             "## Keep it humid for a fortnight\n\n"
             "A clear bag over the pot for two weeks buys the plant time to "
             "grow soil-adapted roots before it has to support itself."),
            ('Reading a yellow leaf', 'Troubleshooting', 'watering, diagnosis',
             "A yellow leaf is a symptom, not a diagnosis. Where it sits on "
             "the plant tells you more than the colour does.\n\n"
             "## Bottom leaves, slow and soft\n\n"
             "Usually overwatering. Check whether the top five centimetres of "
             "soil are still damp several days after watering.\n\n"
             "## Bottom leaves, dry and crisp\n\n"
             "Usually underwatering, or a rootbound plant that cannot hold "
             "enough moisture between drinks.\n\n"
             "## New growth, pale all over\n\n"
             "Often a nitrogen deficit in exhausted soil. Repot rather than "
             "reaching for fertiliser first.\n\n"
             "## Random leaves, patchy yellow with fine webbing\n\n"
             "Spider mites. Check the leaf undersides in good light."),
        ]

        created = 0
        for title, cat_name, tags, body in samples:
            if Article.query.filter_by(title=title).first():
                continue
            html = render_markdown(body, current_app.config['SITE_URL'])
            article = Article(
                title=title, slug=unique_slug(Article, title),
                body_markdown=body, body_html=html, summary=summarise(html),
                reading_minutes=reading_time(body), status=STATUS_PUBLISHED,
                published_at=utcnow(), author=author, category=cats[cat_name])
            db.session.add(article)
            for name in [t.strip() for t in tags.split(',')]:
                tag = Tag.query.filter_by(name=name).first()
                if tag is None:
                    tag = Tag(name=name, slug=unique_slug(Tag, name))
                    db.session.add(tag)
                    db.session.flush()
                article.tags.append(tag)
            created += 1

        db.session.commit()
        click.echo(f'Seeded {len(cats)} categories and {created} articles.')
