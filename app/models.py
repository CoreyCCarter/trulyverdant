from datetime import datetime, timezone, timedelta
import secrets
from urllib.parse import urlsplit

from flask import url_for
from flask_login import UserMixin
from slugify import slugify
from werkzeug.security import generate_password_hash, check_password_hash

from app.extensions import db, login


def utcnow():
    return datetime.now(timezone.utc)


def as_utc(dt):
    """Normalise a datetime read back from the database to aware UTC.

    Postgres timestamptz round-trips as timezone-aware, but SQLite drops the
    offset and hands back a naive datetime. Comparing that to an aware
    utcnow() raises TypeError, so every Python-side comparison goes through
    here.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def path_for(endpoint, **values):
    """url_for() that always yields a path, never an absolute URL.

    Outside a request context Flask makes url_for absolute whenever
    SERVER_NAME is set. These values get concatenated onto SITE_URL for the
    sitemap, feed and canonical tags, so an absolute one here would produce
    'https://site.comhttps://site.com/article/x'.
    """
    parts = urlsplit(url_for(endpoint, **values))
    return parts.path + (f'?{parts.query}' if parts.query else '')


ROLE_ADMIN = 'admin'
ROLE_AUTHOR = 'author'
ROLES = (ROLE_ADMIN, ROLE_AUTHOR)

STATUS_DRAFT = 'draft'
STATUS_PUBLISHED = 'published'


def unique_slug(model, value, *, column='slug', ignore_id=None):
    """Slugify `value`, appending -2, -3 ... until it is unique."""
    base = slugify(value) or 'untitled'
    candidate, suffix = base, 1
    # no_autoflush: this runs while the caller is still populating a pending
    # object, and an autoflush here would try to INSERT it half-built.
    with db.session.no_autoflush:
        while True:
            stmt = db.select(model).where(getattr(model, column) == candidate)
            if ignore_id is not None:
                stmt = stmt.where(model.id != ignore_id)
            if db.session.scalars(stmt).first() is None:
                return candidate
            suffix += 1
            candidate = f'{base}-{suffix}'


article_tags = db.Table(
    'article_tags',
    db.Column('article_id', db.Integer, db.ForeignKey('articles.id'),
              primary_key=True),
    db.Column('tag_id', db.Integer, db.ForeignKey('tags.id'),
              primary_key=True),
)


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), index=True, unique=True, nullable=False)
    email = db.Column(db.String(190), index=True, unique=True, nullable=False)
    password_hash = db.Column(db.String(256))
    display_name = db.Column(db.String(120))
    bio = db.Column(db.Text)
    role = db.Column(db.String(16), default=ROLE_AUTHOR, nullable=False)
    is_active_user = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    articles = db.relationship('Article', back_populates='author',
                               lazy='dynamic')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self):
        return self.role == ROLE_ADMIN

    @property
    def name(self):
        return self.display_name or self.username

    # Flask-Login uses this to refuse sign-in for deactivated accounts.
    @property
    def is_active(self):
        return bool(self.is_active_user)

    def __repr__(self):
        return f'<User {self.username}>'


@login.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


class Category(db.Model):
    __tablename__ = 'categories'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    slug = db.Column(db.String(100), unique=True, index=True, nullable=False)
    description = db.Column(db.String(300))

    articles = db.relationship('Article', back_populates='category',
                               lazy='dynamic')

    @property
    def url(self):
        return path_for('public.category', slug=self.slug)

    def __repr__(self):
        return f'<Category {self.name}>'


class Tag(db.Model):
    __tablename__ = 'tags'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), unique=True, nullable=False)
    slug = db.Column(db.String(80), unique=True, index=True, nullable=False)

    @property
    def url(self):
        return path_for('public.tag', slug=self.slug)

    def __repr__(self):
        return f'<Tag {self.name}>'


class Article(db.Model):
    __tablename__ = 'articles'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(220), unique=True, index=True, nullable=False)
    summary = db.Column(db.String(400))
    body_markdown = db.Column(db.Text, default='')
    body_html = db.Column(db.Text, default='')
    hero_image = db.Column(db.String(300))
    hero_alt = db.Column(db.String(200))

    status = db.Column(db.String(16), default=STATUS_DRAFT, nullable=False,
                       index=True)
    published_at = db.Column(db.DateTime(timezone=True), index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow,
                           onupdate=utcnow)

    # Overrides the auto-generated <meta name="description">.
    meta_description = db.Column(db.String(300))
    reading_minutes = db.Column(db.Integer, default=1)

    author_id = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'),
                            index=True)

    author = db.relationship('User', back_populates='articles')
    category = db.relationship('Category', back_populates='articles')
    tags = db.relationship('Tag', secondary=article_tags,
                           backref=db.backref('articles', lazy='dynamic'),
                           lazy='joined')

    @property
    def is_published(self):
        return self.status == STATUS_PUBLISHED

    @property
    def url(self):
        return path_for('public.article', slug=self.slug)

    @property
    def absolute_url(self):
        from flask import current_app
        return current_app.config['SITE_URL'] + self.url

    @property
    def description(self):
        return self.meta_description or self.summary or ''

    @staticmethod
    def published():
        """Select of published articles, newest first.

        Returns a 2.0-style Select rather than the legacy Query, which
        Flask-SQLAlchemy's paginate() expects and SQLAlchemy will require.
        The date filter keeps scheduled posts hidden until their time comes.
        """
        return db.select(Article).where(
            Article.status == STATUS_PUBLISHED,
            Article.published_at.isnot(None),
            Article.published_at <= utcnow(),
        ).order_by(Article.published_at.desc())

    def __repr__(self):
        return f'<Article {self.slug}>'


class Invite(db.Model):
    """A single-use invitation. There is no public registration: an admin
    issues one of these and the recipient sets their own password."""

    __tablename__ = 'invites'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(190), nullable=False, index=True)
    token = db.Column(db.String(64), unique=True, index=True, nullable=False)
    role = db.Column(db.String(16), default=ROLE_AUTHOR, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    expires_at = db.Column(db.DateTime(timezone=True))
    accepted_at = db.Column(db.DateTime(timezone=True))
    invited_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))

    invited_by = db.relationship('User', foreign_keys=[invited_by_id])

    @staticmethod
    def create(email, role=ROLE_AUTHOR, invited_by=None, valid_days=14):
        return Invite(
            email=email.strip().lower(),
            token=secrets.token_urlsafe(32),
            role=role,
            invited_by=invited_by,
            expires_at=utcnow() + timedelta(days=valid_days),
        )

    @property
    def is_pending(self):
        if self.accepted_at is not None:
            return False
        expires = as_utc(self.expires_at)
        return expires is None or expires > utcnow()

    @property
    def accept_url(self):
        from flask import current_app
        return current_app.config['SITE_URL'] + path_for(
            'auth.accept_invite', token=self.token)

    def __repr__(self):
        return f'<Invite {self.email}>'
