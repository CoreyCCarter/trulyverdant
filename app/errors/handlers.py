from flask import render_template

from app.extensions import db
from app.errors import bp


@bp.app_errorhandler(403)
def forbidden(error):
    return render_template('errors/error.html', code=403,
                           title='Not allowed',
                           message="You do not have access to that page."), 403


@bp.app_errorhandler(404)
def not_found(error):
    return render_template('errors/error.html', code=404,
                           title='Page not found',
                           message="That page does not exist. It may have "
                                   "been moved or renamed."), 404


@bp.app_errorhandler(413)
def too_large(error):
    return render_template('errors/error.html', code=413,
                           title='File too large',
                           message="That upload exceeds the size limit."), 413


@bp.app_errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return render_template('errors/error.html', code=500,
                           title='Something went wrong',
                           message="An unexpected error occurred. It has "
                                   "been logged."), 500
