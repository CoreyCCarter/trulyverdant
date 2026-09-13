"""Import Markdown files as articles.

Each file may start with YAML front matter. Anything it omits is taken from
the file itself when the file follows the house layout:

    # Title
    *One-line summary in italics.*
    **Post date:** September 13, 2026 · **Category:** Easy
    ...body...

and from a `YYYY-MM-DD_slug.md` filename. The H1, summary and metadata lines
are removed from the body, because the article page renders all three from
their own fields -- leaving them in would print each twice.

Local image references, in `hero_image` and inside the body as
`![alt](relative/path.jpg)`, are uploaded through the same pipeline as the
editor (re-encoded, EXIF stripped, WebP variants) and rewritten to their
published URLs. Every image must have alt text, matching the editor's rule.
"""
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone

import yaml
from flask import current_app, has_request_context
from slugify import slugify
from werkzeug.datastructures import FileStorage

from app.content import render_markdown, summarise, reading_time
from app.extensions import db
from app.images import save_image, delete_image, image_url
from app.models import (Article, Category, Tag, unique_slug, utcnow,
                        STATUS_DRAFT, STATUS_PUBLISHED)

FRONT_RE = re.compile(r'\A---[ \t]*\n(.*?)\n---[ \t]*\n', re.S)
FILENAME_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})_(.+)\.md$')
H1_RE = re.compile(r'^#\s+(.+?)\s*$')
ITALIC_RE = re.compile(r'^\*(?!\*)(.+?)(?<!\*)\*\s*$')
META_RE = re.compile(r'^\*\*Post date:\*\*')
META_CATEGORY_RE = re.compile(r'\*\*Category:\*\*\s*(.+?)\s*$')
# Relative paths only: absolute and http(s) references are left alone.
LOCAL_IMAGE_RE = re.compile(r'!\[([^\]]*)\]\((?!https?://|/)([^)\s]+)\)')


class ImportProblem(Exception):
    """A file that cannot be imported as written."""


@dataclass
class Result:
    file: str
    slug: str = ''
    action: str = ''        # created, updated, skipped, would-create, ...
    state: str = ''         # draft, pending, published
    images: int = 0
    message: str = ''


def parse_file(path):
    """Return (metadata, body) for one Markdown file."""
    with open(path, encoding='utf-8') as handle:
        text = handle.read()

    meta = {}
    front = FRONT_RE.match(text)
    if front:
        loaded = yaml.safe_load(front.group(1)) or {}
        if not isinstance(loaded, dict):
            raise ImportProblem('front matter must be a set of key: value pairs')
        meta.update(loaded)
        text = text[front.end():]

    lines = text.split('\n')
    i = 0

    def skip_blank(k):
        while k < len(lines) and not lines[k].strip():
            k += 1
        return k

    i = skip_blank(i)
    heading = H1_RE.match(lines[i]) if i < len(lines) else None
    if heading:
        meta.setdefault('title', heading.group(1))
        i = skip_blank(i + 1)
        italic = ITALIC_RE.match(lines[i]) if i < len(lines) else None
        if italic:
            meta.setdefault('summary', italic.group(1).strip())
            i = skip_blank(i + 1)
        if i < len(lines) and META_RE.match(lines[i]):
            category = META_CATEGORY_RE.search(lines[i])
            if category:
                meta.setdefault('category', category.group(1))
            i = skip_blank(i + 1)

    named = FILENAME_RE.match(os.path.basename(path))
    if named:
        meta.setdefault('date', named.group(1))
        meta.setdefault('slug', named.group(2))

    body = '\n'.join(lines[i:]).strip() + '\n'
    return meta, body


def _as_datetime(value):
    """UTC datetime from a YAML date, datetime or ISO string."""
    if value in (None, ''):
        return None
    if isinstance(value, datetime):          # check before date: subclass
        moment = value
    elif isinstance(value, date):
        moment = datetime(value.year, value.month, value.day, 12, 0)
    else:
        moment = datetime.fromisoformat(str(value).strip().replace('Z', '+00:00'))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _tag_names(value):
    if not value:
        return []
    items = value if isinstance(value, (list, tuple)) else str(value).split(',')
    names = [str(item).strip().lower() for item in items]
    return list(dict.fromkeys(name for name in names if name))


def _category(name):
    name = str(name or '').strip()
    if not name:
        return None
    found = Category.query.filter(
        db.func.lower(Category.name) == name.lower()).first()
    if found:
        return found
    created = Category(name=name, slug=unique_slug(Category, name))
    db.session.add(created)
    return created


def _tag(name):
    found = Tag.query.filter(db.func.lower(Tag.name) == name).first()
    if found:
        return found
    created = Tag(name=name, slug=unique_slug(Tag, name))
    db.session.add(created)
    return created


def _resolve(base, relative):
    path = os.path.normpath(os.path.join(base, relative))
    if not os.path.isfile(path):
        raise ImportProblem(f'image not found: {relative}')
    return path


def import_file(path, author, *, update=False, dry_run=False):
    meta, body = parse_file(path)
    result = Result(file=os.path.basename(path))

    title = str(meta.get('title') or '').strip()
    if not title:
        raise ImportProblem('no title: add a "# Heading" or a title: field')
    slug = slugify(str(meta.get('slug') or title))
    result.slug = slug

    status = str(meta.get('status') or STATUS_PUBLISHED).strip().lower()
    if status not in (STATUS_PUBLISHED, STATUS_DRAFT):
        raise ImportProblem(f'status must be published or draft, not {status!r}')
    published_at = _as_datetime(meta.get('published_at') or meta.get('date'))
    if status == STATUS_PUBLISHED and published_at is None:
        published_at = utcnow()

    base = os.path.dirname(os.path.abspath(path))
    hero_rel = meta.get('hero_image')
    hero_alt = str(meta.get('hero_alt') or '').strip()
    hero_path = _resolve(base, hero_rel) if hero_rel else None
    if hero_path and not hero_alt:
        raise ImportProblem('hero_image needs hero_alt')
    for alt, relative in LOCAL_IMAGE_RE.findall(body):
        _resolve(base, relative)
        if not alt.strip():
            raise ImportProblem(f'body image has no alt text: {relative}')
    result.images = (1 if hero_path else 0) + len(LOCAL_IMAGE_RE.findall(body))

    existing = Article.query.filter_by(slug=slug).first()
    if existing and not update:
        result.action = 'skipped'
        result.state = existing.display_status
        result.message = 'slug already exists (use --update to replace)'
        return result

    if dry_run:
        result.action = 'would-update' if existing else 'would-create'
        result.state = _state(status, published_at)
        return result

    uploaded = []

    def upload(file_path):
        with open(file_path, 'rb') as handle:
            stored = save_image(FileStorage(stream=handle,
                                            filename=os.path.basename(file_path)))
        uploaded.append(stored)
        return stored

    old_hero = None
    try:
        rewritten = LOCAL_IMAGE_RE.sub(
            lambda m: f'![{m.group(1)}]({image_url(upload(_resolve(base, m.group(2))))})',
            body)
        new_hero = upload(hero_path) if hero_path else None

        article = existing or Article(author=author)
        article.title = title[:200]
        article.slug = slug
        article.body_markdown = rewritten
        article.body_html = render_markdown(rewritten, current_app.config['SITE_URL'])
        article.summary = (str(meta.get('summary') or '').strip()
                           or summarise(article.body_html))[:400]
        description = str(meta.get('meta_description') or '').strip()
        article.meta_description = description[:300] or None
        article.reading_minutes = reading_time(rewritten)
        article.status = status
        article.published_at = published_at
        if new_hero:
            old_hero = existing.hero_image if existing else None
            article.hero_image = new_hero
            article.hero_alt = hero_alt[:200]

        db.session.add(article)
        with db.session.no_autoflush:
            article.category = _category(meta.get('category'))
            article.tags = [_tag(name) for name in _tag_names(meta.get('tags'))]
        db.session.commit()
    except Exception:
        db.session.rollback()
        for stored in uploaded:
            delete_image(stored)
        raise

    if old_hero:
        delete_image(old_hero)
    result.action = 'updated' if existing else 'created'
    result.state = article.display_status
    return result


def _state(status, published_at):
    if status != STATUS_PUBLISHED:
        return status
    return 'pending' if published_at and published_at > utcnow() else 'published'


def import_directory(directory, author, *, update=False, dry_run=False):
    """Import every .md file in `directory`, one transaction per file, so a
    bad file is reported without stopping or half-importing the rest."""
    def run():
        results = []
        for name in sorted(os.listdir(directory)):
            if not name.endswith('.md'):
                continue
            try:
                results.append(import_file(os.path.join(directory, name), author,
                                           update=update, dry_run=dry_run))
            except (ImportProblem, ValueError, yaml.YAMLError, OSError) as exc:
                db.session.rollback()
                results.append(Result(file=name, action='error', message=str(exc)))
        return results

    # Image URLs are built with url_for, which needs a request context
    # outside of a web request -- as in a CLI command.
    if has_request_context():
        return run()
    with current_app.test_request_context('/'):
        return run()
