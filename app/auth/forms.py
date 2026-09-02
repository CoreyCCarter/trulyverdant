from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField, SubmitField, \
    TextAreaField
from wtforms.validators import DataRequired, Length, EqualTo, Email, \
    ValidationError, Optional

from app.models import User


class LoginForm(FlaskForm):
    username = StringField('Username or email', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    remember_me = BooleanField('Keep me signed in')
    submit = SubmitField('Sign in')


class AcceptInviteForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(),
                                                   Length(min=3, max=64)])
    display_name = StringField('Display name', validators=[Optional(),
                                                           Length(max=120)])
    password = PasswordField('Password', validators=[
        DataRequired(), Length(min=12, message='Use at least 12 characters.')])
    password2 = PasswordField('Repeat password', validators=[
        DataRequired(), EqualTo('password', message='Passwords must match.')])
    submit = SubmitField('Create account')

    def validate_username(self, field):
        if User.query.filter_by(username=field.data.strip().lower()).first():
            raise ValidationError('That username is taken.')


class ProfileForm(FlaskForm):
    display_name = StringField('Display name', validators=[Optional(),
                                                           Length(max=120)])
    email = StringField('Email', validators=[DataRequired(), Email(),
                                             Length(max=190)])
    bio = TextAreaField('Short bio', validators=[Optional(), Length(max=1000)],
                        description='Shown on your author page.')
    submit = SubmitField('Save')

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def validate_email(self, field):
        existing = User.query.filter_by(email=field.data.strip().lower()).first()
        if existing and existing.id != self.user.id:
            raise ValidationError('That email is already in use.')


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField('Current password',
                                     validators=[DataRequired()])
    password = PasswordField('New password', validators=[
        DataRequired(), Length(min=12, message='Use at least 12 characters.')])
    password2 = PasswordField('Repeat new password', validators=[
        DataRequired(), EqualTo('password', message='Passwords must match.')])
    submit = SubmitField('Change password')
