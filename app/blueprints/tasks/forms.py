"""WTForms form definitions for task management."""

from decimal import Decimal

from flask_wtf import FlaskForm
from wtforms import DateField, DecimalField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, NumberRange, Optional

from app.models.task import TaskPriority, TaskSource, TaskStatus

_STATUS_CHOICES = [
    (TaskStatus.OPEN.value, "Ouverte"),
    (TaskStatus.IN_PROGRESS.value, "En cours"),
    (TaskStatus.DONE.value, "Terminée"),
    (TaskStatus.CANCELLED.value, "Annulée"),
]

_PRIORITY_CHOICES = [
    (TaskPriority.LOW.value, "Basse"),
    (TaskPriority.NORMAL.value, "Normale"),
    (TaskPriority.HIGH.value, "Haute"),
    (TaskPriority.URGENT.value, "Urgente"),
]

_SOURCE_CHOICES = [
    (TaskSource.MANUAL.value, "Manuelle"),
    (TaskSource.CENTER_BREAKDOWN.value, "Panne centre"),
    (TaskSource.MEETING.value, "Réunion"),
    (TaskSource.EMAIL.value, "Email"),
    (TaskSource.EVENT.value, "Événement"),
]


class TaskForm(FlaskForm):
    """Form for creating or editing a task (bureau only)."""

    title = StringField(
        "Titre",
        validators=[DataRequired(message="Le titre est obligatoire."), Length(max=200)],
    )
    description = TextAreaField(
        "Description",
        validators=[Optional(), Length(max=2000)],
    )
    priority = SelectField(
        "Priorité",
        choices=_PRIORITY_CHOICES,
        default=TaskPriority.NORMAL.value,
    )
    status = SelectField(
        "Statut",
        choices=_STATUS_CHOICES,
        default=TaskStatus.OPEN.value,
    )
    assigned_to_id = SelectField(
        "Assignée à",
        coerce=lambda x: int(x) if x else None,
        validators=[Optional()],
    )
    due_date = DateField(
        "Échéance",
        validators=[Optional()],
    )
    source_event_id = SelectField(
        "Événement lié",
        coerce=lambda x: int(x) if x else None,
        validators=[Optional()],
    )


class TaskCommentForm(FlaskForm):
    """Form for posting a comment on a task."""

    body = TextAreaField(
        "Commentaire",
        validators=[
            DataRequired(message="Le commentaire ne peut pas être vide."),
            Length(max=2000),
        ],
    )


class TaskClaimForm(FlaskForm):
    """Minimal form for a member to self-assign a task (CSRF only)."""


class TaskStatusForm(FlaskForm):
    """Form for changing task status (bureau only, CSRF only)."""

    status = SelectField("Statut", choices=_STATUS_CHOICES)


class ConvertToMaintenanceForm(FlaskForm):
    """Form for converting a center-breakdown task into a MaintenanceRecord."""

    machine_id = SelectField("Machine", coerce=int, validators=[DataRequired()])
    description = TextAreaField("Description", validators=[Optional(), Length(max=2000)])
    cost = DecimalField(
        "Coût (€)",
        places=2,
        default=Decimal("0"),
        validators=[Optional(), NumberRange(min=0)],
    )
