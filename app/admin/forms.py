from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed
from wtforms import (StringField, TextAreaField, SelectField, SubmitField,
                     BooleanField, DateTimeLocalField)
from wtforms.validators import DataRequired, Length, Optional, Email

from app.models import ROLES, ROLE_AUTHOR

IMAGE_EXTS = ['jpg', 'jpeg', 'png', 'webp', 'gif']


class ArticleForm(FlaskForm):
    title = StringField('Title', validators=[DataRequired(), Length(max=200)])
    slug = StringField('URL slug', validators=[Optional(), Length(max=220)],
                       description='Leave blank to generate from the title. '
                                   'Changing it on a published article breaks '
                                   'existing links.')
    summary = TextAreaField('Summary', validators=[Optional(), Length(max=400)],
                            description='One or two sentences. Used on '
                                        'listings, in search results and for '
                                        'social previews.')
    body_markdown = TextAreaField('Body (Markdown)',
                                  validators=[DataRequired()])
    category = SelectField('Category', coerce=int, validators=[Optional()])
    tags = StringField('Tags', validators=[Optional(), Length(max=300)],
                       description='Comma separated.')
    hero = FileField('Header image',
                     validators=[FileAllowed(IMAGE_EXTS, 'Images only.')])
    hero_alt = StringField('Header image description',
                           validators=[Optional(), Length(max=200)],
                           description='Describes the image for screen '
                                       'readers and search engines.')
    remove_hero = BooleanField('Remove current header image')
    meta_description = TextAreaField('Meta description',
                                     validators=[Optional(), Length(max=300)],
                                     description='Overrides the summary in '
                                                 'search results.')
    status = SelectField('Status', choices=[('draft', 'Draft'),
                                            ('published', 'Published')])
    published_at = DateTimeLocalField('Publish date', format='%Y-%m-%dT%H:%M',
                                      validators=[Optional()],
                                      description='Leave blank to use now. '
                                                  'A future date schedules it.')
    submit = SubmitField('Save')


class CategoryForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(max=80)])
    description = TextAreaField('Description',
                                validators=[Optional(), Length(max=300)])
    submit = SubmitField('Save')


class InviteForm(FlaskForm):
    email = StringField('Email address',
                        validators=[DataRequired(), Email(), Length(max=190)])
    role = SelectField('Role', choices=[(r, r.title()) for r in ROLES],
                       default=ROLE_AUTHOR)
    submit = SubmitField('Create invitation')


class ConfirmForm(FlaskForm):
    """Bare CSRF-protected form for destructive POST buttons."""
    submit = SubmitField('Confirm')
