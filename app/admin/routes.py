from functools import wraps

from flask import (render_template, redirect, url_for, flash, request, abort,
                   current_app)
from flask_login import login_required, current_user

from app.extensions import db
from app.models import (Article, Category, Tag, User, Invite, unique_slug,
                        utcnow, STATUS_PUBLISHED, STATUS_DRAFT, ROLE_ADMIN)
from app.content import render_markdown, summarise, reading_time
from app.images import (save_image, delete_image, image_url,
                        list_images, ImageError)
from app.admin import bp
from app.admin.forms import ArticleForm, CategoryForm, InviteForm, ConfirmForm


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def _may_edit(article):
    return current_user.is_admin or article.author_id == current_user.id


def _category_choices():
    choices = [(0, '— none —')]
    choices += [(c.id, c.name) for c in
                Category.query.order_by(Category.name).all()]
    return choices


def _tag_names():
    return [t.name for t in Tag.query.order_by(Tag.name).all()]


def _sync_tags(article, raw):
    names = [n.strip() for n in (raw or '').split(',') if n.strip()]
    tags = []
    # no_autoflush: the article may still be pending and half-built.
    with db.session.no_autoflush:
        for name in dict.fromkeys(n.lower() for n in names):
            tag = db.session.scalars(
                db.select(Tag).where(db.func.lower(Tag.name) == name)).first()
            if tag is None:
                tag = Tag(name=name, slug=unique_slug(Tag, name))
                db.session.add(tag)
            tags.append(tag)
    article.tags = tags


STATUS_PENDING = 'pending'


def _state_filter(status, now):
    """SQL for the three states an editor sees, matching display_status.

    The status column alone only knows draft/published; a published article
    dated in the future is pending, and counting it as published made the
    dashboard claim articles were live that were not."""
    if status == STATUS_DRAFT:
        return Article.status == STATUS_DRAFT
    if status == STATUS_PENDING:
        return db.and_(Article.status == STATUS_PUBLISHED,
                       Article.published_at > now)
    # Spelled out rather than NOT(pending): NOT(NULL > now) is NULL in SQL,
    # which would drop an undated article that display_status calls published.
    return db.and_(Article.status == STATUS_PUBLISHED,
                   db.or_(Article.published_at.is_(None),
                          Article.published_at <= now))


def _own(stmt):
    if not current_user.is_admin:
        stmt = stmt.where(Article.author_id == current_user.id)
    return stmt


@bp.route('/')
@login_required
def dashboard():
    now = utcnow()
    articles = db.session.scalars(_own(db.select(Article))
                                  .order_by(Article.updated_at.desc())
                                  .limit(10)).unique().all()
    scheduled = db.session.scalars(
        _own(db.select(Article)).where(_state_filter(STATUS_PENDING, now))
        .order_by(Article.published_at.asc()).limit(10)).unique().all()
    counts = {state: db.session.scalar(
                  _own(db.select(db.func.count(Article.id)))
                  .where(_state_filter(state, now)))
              for state in (STATUS_PUBLISHED, STATUS_PENDING, STATUS_DRAFT)}
    return render_template('admin/dashboard.html', articles=articles,
                           scheduled=scheduled, counts=counts,
                           page_title='Dashboard')


@bp.route('/articles')
@login_required
def articles():
    query = _own(db.select(Article))
    status = request.args.get('status')
    if status in (STATUS_DRAFT, STATUS_PENDING, STATUS_PUBLISHED):
        query = query.where(_state_filter(status, utcnow()))
    # Pending lists soonest first: the question on that tab is "what goes
    # out next".
    order = (Article.published_at.asc() if status == STATUS_PENDING
             else Article.updated_at.desc())
    items = db.paginate(query.order_by(order),
                        page=request.args.get('page', 1, type=int),
                        per_page=20, error_out=False)
    return render_template('admin/articles.html', articles=items,
                           status=status, form=ConfirmForm(),
                           page_title='Articles')


@bp.route('/articles/new', methods=['GET', 'POST'])
@login_required
def new_article():
    form = ArticleForm()
    form.category.choices = _category_choices()
    form.has_hero = False
    if form.validate_on_submit():
        article = Article(author=current_user)
        if _apply_article_form(form, article):
            db.session.add(article)
            db.session.commit()
            flash('Article saved.', 'success')
            return redirect(url_for('admin.edit_article', article_id=article.id))
        db.session.rollback()
    return render_template('admin/article_form.html', form=form, article=None,
                           all_tags=_tag_names(), page_title='New article')


@bp.route('/articles/<int:article_id>', methods=['GET', 'POST'])
@login_required
def edit_article(article_id):
    article = db.get_or_404(Article, article_id)
    if not _may_edit(article):
        abort(403)
    form = ArticleForm(obj=article)
    form.category.choices = _category_choices()
    form.has_hero = bool(article.hero_image)
    if request.method == 'GET':
        form.category.data = article.category_id or 0
        form.tags.data = ', '.join(t.name for t in article.tags)
    if form.validate_on_submit():
        if _apply_article_form(form, article):
            db.session.commit()
            flash('Article saved.', 'success')
            return redirect(url_for('admin.edit_article', article_id=article.id))
    return render_template('admin/article_form.html', form=form,
                           article=article, all_tags=_tag_names(),
                           page_title=f'Edit: {article.title}')


def _apply_article_form(form, article):
    """Copy form data onto the article. Returns False if it could not be saved."""
    article.title = form.title.data.strip()

    desired_slug = (form.slug.data or '').strip() or article.title
    if not article.slug or desired_slug != article.slug:
        article.slug = unique_slug(Article, desired_slug,
                                   ignore_id=article.id)

    article.body_markdown = form.body_markdown.data
    article.body_html = render_markdown(article.body_markdown,
                                        current_app.config['SITE_URL'])
    article.summary = (form.summary.data or '').strip() \
        or summarise(article.body_html)
    article.meta_description = (form.meta_description.data or '').strip() or None
    article.reading_minutes = reading_time(article.body_markdown)
    article.category_id = form.category.data or None
    article.hero_alt = (form.hero_alt.data or '').strip() or None
    _sync_tags(article, form.tags.data)

    if form.remove_hero.data and article.hero_image:
        delete_image(article.hero_image)
        article.hero_image = None

    upload = form.hero.data
    if upload and getattr(upload, 'filename', ''):
        try:
            stored = save_image(upload)
        except ImageError as exc:
            flash(str(exc), 'error')
            return False
        if article.hero_image:
            delete_image(article.hero_image)
        article.hero_image = stored

    article.status = form.status.data
    if article.status == STATUS_PUBLISHED:
        article.published_at = form.published_at.data or \
            article.published_at or utcnow()
    else:
        # Keep the chosen date so re-publishing does not lose it.
        article.published_at = form.published_at.data or article.published_at
    return True


@bp.route('/articles/<int:article_id>/delete', methods=['POST'])
@login_required
def delete_article(article_id):
    article = db.get_or_404(Article, article_id)
    if not _may_edit(article):
        abort(403)
    form = ConfirmForm()
    if not form.validate_on_submit():
        abort(400)
    if article.hero_image:
        delete_image(article.hero_image)
    db.session.delete(article)
    db.session.commit()
    flash('Article deleted.', 'success')
    return redirect(url_for('admin.articles'))


@bp.route('/media', methods=['GET'])
@login_required
def media():
    """Images already uploaded, for reuse from the editor."""
    return {'images': list_images()}


@bp.route('/media/upload', methods=['POST'])
@login_required
def media_upload():
    """Upload one image from the editor and return markdown for it.

    Body images previously had no upload path at all -- save_image() was
    reachable only through the hero field -- so illustrating mid-article
    was impossible without hand-writing a URL.
    """
    try:
        stored = save_image(request.files.get('file'))
    except ImageError as exc:
        return {'error': str(exc)}, 400
    url = image_url(stored)
    return {'stored': stored, 'url': url, 'markdown': f'![]({url})'}


@bp.route('/preview', methods=['POST'])
@login_required
def preview():
    """Render Markdown for the editor's live preview.

    Uses the same pipeline as saving, so the preview shows exactly the
    sanitised HTML that would be stored -- never a browser-side
    approximation that could disagree with it.
    """
    payload = request.get_json(silent=True) or {}
    html = render_markdown(payload.get('markdown') or '',
                           current_app.config['SITE_URL'])
    return {'html': html}


@bp.route('/categories', methods=['GET', 'POST'])
@admin_required
def categories():
    form = CategoryForm()
    if form.validate_on_submit():
        name = form.name.data.strip()
        if Category.query.filter(db.func.lower(Category.name) ==
                                 name.lower()).first():
            flash('That category already exists.', 'error')
        else:
            db.session.add(Category(
                name=name, slug=unique_slug(Category, name),
                description=(form.description.data or '').strip() or None))
            db.session.commit()
            flash('Category added.', 'success')
        return redirect(url_for('admin.categories'))
    items = Category.query.order_by(Category.name).all()
    return render_template('admin/categories.html', categories=items,
                           form=form, confirm=ConfirmForm(),
                           page_title='Categories')


@bp.route('/categories/<int:category_id>/delete', methods=['POST'])
@admin_required
def delete_category(category_id):
    category = db.get_or_404(Category, category_id)
    if not ConfirmForm().validate_on_submit():
        abort(400)
    # Articles are kept; they simply become uncategorised.
    Article.query.filter_by(category_id=category.id).update(
        {'category_id': None})
    db.session.delete(category)
    db.session.commit()
    flash('Category deleted.', 'success')
    return redirect(url_for('admin.categories'))


@bp.route('/people', methods=['GET', 'POST'])
@admin_required
def people():
    form = InviteForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        if User.query.filter_by(email=email).first():
            flash('Someone with that email already has an account.', 'error')
        else:
            invite = Invite.create(email, role=form.role.data,
                                   invited_by=current_user)
            db.session.add(invite)
            db.session.commit()
            flash('Invitation created. Send them the link below.', 'success')
        return redirect(url_for('admin.people'))
    return render_template(
        'admin/people.html', form=form, confirm=ConfirmForm(),
        users=User.query.order_by(User.created_at).all(),
        invites=Invite.query.order_by(Invite.created_at.desc()).all(),
        page_title='People')


@bp.route('/people/<int:user_id>/toggle', methods=['POST'])
@admin_required
def toggle_user(user_id):
    user = db.get_or_404(User, user_id)
    if not ConfirmForm().validate_on_submit():
        abort(400)
    if user.id == current_user.id:
        flash('You cannot deactivate your own account.', 'error')
    elif user.is_admin and User.query.filter_by(
            role=ROLE_ADMIN, is_active_user=True).count() <= 1:
        flash('That is the last active admin.', 'error')
    else:
        user.is_active_user = not user.is_active_user
        db.session.commit()
        flash('Account updated.', 'success')
    return redirect(url_for('admin.people'))


@bp.route('/invites/<int:invite_id>/revoke', methods=['POST'])
@admin_required
def revoke_invite(invite_id):
    invite = db.get_or_404(Invite, invite_id)
    if not ConfirmForm().validate_on_submit():
        abort(400)
    db.session.delete(invite)
    db.session.commit()
    flash('Invitation revoked.', 'success')
    return redirect(url_for('admin.people'))
