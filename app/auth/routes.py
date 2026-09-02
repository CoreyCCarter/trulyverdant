from urllib.parse import urlsplit

from flask import render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, current_user, login_required

from app.extensions import db
from app.models import User, Invite, utcnow
from app.auth import bp
from app.auth.forms import LoginForm, AcceptInviteForm, ProfileForm, \
    ChangePasswordForm


def _safe_next(target):
    """Only follow same-site redirects, so ?next= cannot bounce off-site."""
    if not target:
        return None
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc:
        return None
    return target if target.startswith('/') else None


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('admin.dashboard'))
    form = LoginForm()
    if form.validate_on_submit():
        ident = form.username.data.strip().lower()
        user = User.query.filter(
            db.or_(User.username == ident, User.email == ident)).first()
        if user is None or not user.check_password(form.password.data):
            # Deliberately identical message for unknown user and bad
            # password, so this cannot be used to enumerate accounts.
            flash('Invalid credentials.', 'error')
            return redirect(url_for('auth.login'))
        if not user.is_active:
            flash('That account has been deactivated.', 'error')
            return redirect(url_for('auth.login'))
        login_user(user, remember=form.remember_me.data)
        return redirect(_safe_next(request.args.get('next'))
                        or url_for('admin.dashboard'))
    return render_template('auth/login.html', form=form, page_title='Sign in')


@bp.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    flash('Signed out.', 'success')
    return redirect(url_for('public.index'))


@bp.route('/invite/<token>', methods=['GET', 'POST'])
def accept_invite(token):
    """The only route that creates an account. There is no open sign-up."""
    invite = Invite.query.filter_by(token=token).first()
    if invite is None or not invite.is_pending:
        return render_template('auth/invite_invalid.html',
                               page_title='Invitation'), 404

    form = AcceptInviteForm()
    if form.validate_on_submit():
        user = User(
            username=form.username.data.strip().lower(),
            email=invite.email,
            display_name=(form.display_name.data or '').strip() or None,
            role=invite.role,
        )
        user.set_password(form.password.data)
        invite.accepted_at = utcnow()
        db.session.add(user)
        db.session.commit()
        login_user(user)
        flash('Welcome aboard. Your account is ready.', 'success')
        return redirect(url_for('admin.dashboard'))

    return render_template('auth/accept_invite.html', form=form,
                           invite=invite, page_title='Accept invitation')


@bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    form = ProfileForm(current_user, obj=current_user)
    if form.validate_on_submit():
        current_user.display_name = (form.display_name.data or '').strip() or None
        current_user.email = form.email.data.strip().lower()
        current_user.bio = form.bio.data
        db.session.commit()
        flash('Profile updated.', 'success')
        return redirect(url_for('auth.profile'))
    return render_template('auth/profile.html', form=form,
                           password_form=ChangePasswordForm(),
                           page_title='Your profile')


@bp.route('/password', methods=['POST'])
@login_required
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            flash('Current password is incorrect.', 'error')
        else:
            current_user.set_password(form.password.data)
            db.session.commit()
            flash('Password changed.', 'success')
    else:
        for errors in form.errors.values():
            for error in errors:
                flash(error, 'error')
    return redirect(url_for('auth.profile'))
