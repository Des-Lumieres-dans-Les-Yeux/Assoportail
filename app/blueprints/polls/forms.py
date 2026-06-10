from flask_wtf import FlaskForm
from wtforms import BooleanField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional


class PollForm(FlaskForm):
    """Metadata form for creating/editing a poll (bureau only).

    Options and deadline are handled as raw HTML fields in the route
    to support dynamic JS add/remove without WTForms FieldList complexity.
    """

    title = StringField("Titre", validators=[DataRequired(), Length(max=200)])
    description = TextAreaField("Description", validators=[Optional(), Length(max=3000)])
    allows_multiple = BooleanField("Plusieurs réponses autorisées")


class VoteForm(FlaskForm):
    """CSRF-only form used to protect vote submissions."""
