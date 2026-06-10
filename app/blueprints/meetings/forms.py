"""WTForms form definitions for meeting management."""

from flask_wtf import FlaskForm
from wtforms import DateTimeLocalField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional


class MeetingForm(FlaskForm):
    """Form for creating or editing a meeting (bureau only)."""

    title = StringField(
        "Sujet / titre",
        validators=[DataRequired(message="Le titre est obligatoire."), Length(max=200)],
    )
    date = DateTimeLocalField(
        "Date et heure",
        format="%Y-%m-%dT%H:%M",
        validators=[DataRequired(message="La date est obligatoire.")],
    )
    location = StringField(
        "Lieu",
        validators=[Optional(), Length(max=200)],
    )
    minutes = TextAreaField(
        "Compte-rendu",
        validators=[Optional(), Length(max=10000)],
    )


class AttendanceForm(FlaskForm):
    """Bare form used solely for CSRF protection on the attendance checkbox list."""


class LinkTaskForm(FlaskForm):
    """Form for linking a task to a meeting (bureau only)."""

    task_id = SelectField(
        "Tâche",
        coerce=int,
        validators=[DataRequired(message="Sélectionnez une tâche.")],
    )


class CreateTaskFromMeetingForm(FlaskForm):
    """Form for creating a new task directly from a meeting (bureau only)."""

    title = StringField(
        "Titre de la tâche",
        validators=[DataRequired(message="Le titre est obligatoire."), Length(max=200)],
    )
