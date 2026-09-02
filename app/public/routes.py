from datetime import timezone

from flask import (render_template, request, abort, Response, current_app,
                   url_for, redirect)
from flask_login import current_user

from app.extensions import db
from app.models import (Article, Category, Tag, User, path_for,
                        STATUS_PUBLISHED)
from app.content import split_for_ad
from app.images import image_url
from app.public import bp


def _page():
    return request.args.get('page', 1, type=int)


def _paginate(query):
    return db.paginate(query, page=_page(),
                       per_page=current_app.config['ARTICLES_PER_PAGE'],
                       error_out=False)


@bp.route('/')
def index():
    articles = _paginate(Article.published())
    return render_template('public/index.html', articles=articles,
                           page_title=None)


@bp.route('/article/<slug>')
def article(slug):
    item = Article.query.filter_by(slug=slug).first_or_404()

    # Drafts stay invisible to the public, but the author and admins can
    # preview them at the real URL before publishing.
    if not item.is_published:
        can_preview = current_user.is_authenticated and (
            current_user.is_admin or item.author_id == current_user.id)
        if not can_preview:
            abort(404)

    body_before, body_after = split_for_ad(item.body_html)
    related = (Article.published()
               .where(Article.id != item.id)
               .where(Article.category_id == item.category_id)
               .limit(3)) if item.category_id else None
    related = db.session.scalars(related).unique().all() if related is not None else []

    return render_template(
        'public/article.html', article=item,
        body_before=body_before, body_after=body_after, related=related,
        page_title=item.title,
        page_description=item.description,
        og_type='article',
        og_image=image_url(item.hero_image) if item.hero_image else None,
        # A draft being previewed must never be indexed.
        no_index=not item.is_published)


@bp.route('/category/<slug>')
def category(slug):
    item = Category.query.filter_by(slug=slug).first_or_404()
    articles = _paginate(
        Article.published().where(Article.category_id == item.id))
    return render_template('public/listing.html', articles=articles,
                           heading=item.name, intro=item.description,
                           page_title=item.name)


@bp.route('/tag/<slug>')
def tag(slug):
    item = Tag.query.filter_by(slug=slug).first_or_404()
    articles = _paginate(
        Article.published().where(Article.tags.any(Tag.id == item.id)))
    return render_template('public/listing.html', articles=articles,
                           heading=f'Tagged “{item.name}”', intro=None,
                           page_title=f'Tagged {item.name}')


@bp.route('/author/<username>')
def author(username):
    item = User.query.filter_by(username=username).first_or_404()
    articles = _paginate(
        Article.published().where(Article.author_id == item.id))
    return render_template('public/author.html', author=item,
                           articles=articles, page_title=item.name)


@bp.route('/search')
def search():
    q = (request.args.get('q') or '').strip()
    articles = None
    if q:
        like = f'%{q}%'
        articles = _paginate(Article.published().where(
            db.or_(Article.title.ilike(like),
                   Article.summary.ilike(like),
                   Article.body_markdown.ilike(like))))
    return render_template('public/search.html', q=q, articles=articles,
                           page_title=f'Search: {q}' if q else 'Search')


@bp.route('/about')
def about():
    authors = (User.query.filter_by(is_active_user=True)
               .order_by(User.created_at).all())
    return render_template('public/about.html', authors=authors,
                           page_title='About')


@bp.route('/privacy')
def privacy():
    return render_template('public/privacy.html', page_title='Privacy Policy')


@bp.route('/contact')
def contact():
    return render_template('public/contact.html', page_title='Contact')


# --------------------------------------------------------------------------
# Machine-readable endpoints. AdSense review and Google Search both expect
# these to exist and to be reachable without authentication.
# --------------------------------------------------------------------------

@bp.route('/robots.txt')
def robots():
    site = current_app.config['SITE_URL']
    if not current_app.config['SEO_INDEXABLE']:
        body = 'User-agent: *\nDisallow: /\n'
    else:
        body = (
            'User-agent: *\n'
            'Allow: /\n'
            'Disallow: /admin/\n'
            'Disallow: /auth/\n'
            'Disallow: /search\n'
            f'\nSitemap: {site}{path_for("public.sitemap")}\n'
        )
    return Response(body, mimetype='text/plain')


@bp.route('/ads.txt')
def ads_txt():
    """Google will not serve ads on a domain without a matching ads.txt."""
    body = current_app.config['ADS_TXT']
    if not body:
        abort(404)
    return Response(body.strip() + '\n', mimetype='text/plain')


@bp.route('/sitemap.xml')
def sitemap():
    site = current_app.config['SITE_URL']
    urls = []

    def add(loc, lastmod=None, priority='0.5', freq='weekly'):
        urls.append({'loc': site + loc, 'lastmod': lastmod,
                     'priority': priority, 'changefreq': freq})

    add(path_for('public.index'), priority='1.0', freq='daily')
    add(path_for('public.about'), priority='0.3', freq='monthly')
    add(path_for('public.privacy'), priority='0.1', freq='yearly')
    add(path_for('public.contact'), priority='0.3', freq='yearly')

    for a in db.session.scalars(Article.published()).unique():
        add(a.url, lastmod=(a.updated_at or a.published_at), priority='0.8')
    for c in Category.query.all():
        if c.articles.filter_by(status=STATUS_PUBLISHED).count():
            add(c.url, priority='0.6')
    for t in Tag.query.all():
        if t.articles.filter_by(status=STATUS_PUBLISHED).count():
            add(t.url, priority='0.4')

    xml = render_template('public/sitemap.xml', urls=urls)
    return Response(xml, mimetype='application/xml')


@bp.route('/feed.xml')
def feed():
    articles = db.session.scalars(Article.published().limit(20)).unique().all()
    xml = render_template('public/feed.xml', articles=articles,
                          build_date=_rfc822(articles[0].published_at)
                          if articles else None)
    return Response(xml, mimetype='application/rss+xml')


def _rfc822(dt):
    if not dt:
        return ''
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime('%a, %d %b %Y %H:%M:%S +0000')


bp.add_app_template_filter(_rfc822, 'rfc822')


@bp.route('/feed')
def feed_alias():
    return redirect(url_for('public.feed'), code=301)
