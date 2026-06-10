"""WTForms form definitions for event management."""

from flask_wtf import FlaskForm
from wtforms import (
    DateField,
    DateTimeLocalField,
    DecimalField,
    HiddenField,
    SelectField,
    StringField,
    TextAreaField,
    TimeField,
)
from wtforms.validators import DataRequired, InputRequired, Length, NumberRange, Optional

from app.models.event import CashEntryType, EventStatus, ExpenseType

_STATUS_CHOICES = [
    (EventStatus.PLANNED.value, "Planifié"),
    (EventStatus.IN_PROGRESS.value, "En cours"),
    (EventStatus.COMPLETED.value, "Terminé"),
    (EventStatus.CANCELLED.value, "Annulé"),
]

_EXPENSE_TYPE_CHOICES = [
    (ExpenseType.TRAVEL.value, "Transport"),
    (ExpenseType.SUPPLY.value, "Matériel / fournitures"),
    (ExpenseType.OTHER.value, "Autre"),
]

_CASH_ENTRY_TYPE_CHOICES = [
    (CashEntryType.DONATION.value, "Don"),
    (CashEntryType.SALE.value, "Vente"),
    (CashEntryType.OTHER.value, "Autre"),
]


class EventForm(FlaskForm):
    """Form for creating or editing an event (bureau only)."""

    title = StringField(
        "Titre",
        validators=[DataRequired(message="Le titre est obligatoire."), Length(max=200)],
    )
    description = TextAreaField(
        "Description",
        validators=[Optional(), Length(max=2000)],
    )
    status = SelectField(
        "Statut",
        choices=_STATUS_CHOICES,
        default=EventStatus.PLANNED.value,
    )
    event_date = DateTimeLocalField(
        "Date et heure de début",
        format="%Y-%m-%dT%H:%M",
        validators=[DataRequired(message="La date est obligatoire.")],
    )
    end_date = DateTimeLocalField(
        "Date et heure de fin",
        format="%Y-%m-%dT%H:%M",
        validators=[Optional()],
    )
    location = StringField(
        "Lieu",
        validators=[Optional(), Length(max=200)],
    )
    website = StringField(
        "Site web (lien)",
        validators=[Optional(), Length(max=500)],
    )


class ExpenseForm(FlaskForm):
    """Form for submitting a reimbursable expense (any member)."""

    type = SelectField(
        "Type",
        choices=_EXPENSE_TYPE_CHOICES,
        default=ExpenseType.OTHER.value,
    )
    amount = DecimalField(
        "Montant (€)",
        places=2,
        validators=[
            Optional(),
            NumberRange(min=0.01, message="Le montant doit être positif."),
        ],
    )
    distance_km = DecimalField(
        "Distance (km)",
        places=1,
        validators=[
            Optional(),
            NumberRange(min=0.1, message="La distance doit être positive."),
        ],
    )
    description = TextAreaField(
        "Description",
        validators=[
            DataRequired(message="La description est obligatoire."),
            Length(
                max=1000,
                message="La description ne doit pas dépasser 1 000 caractères.",
            ),
        ],
    )


class AttendanceForm(FlaskForm):
    """Bare form used solely for CSRF protection on the attendance checkbox list."""


class CashBoxOpenForm(FlaskForm):
    """Form for opening a cash box at the start of an event (bureau only)."""

    opening_amount = DecimalField(
        "Fond de caisse (€)",
        places=2,
        validators=[
            InputRequired(message="Le fond de caisse est obligatoire."),
            NumberRange(min=0, message="Le montant ne peut pas être négatif."),
        ],
    )


class CashEntryForm(FlaskForm):
    """Form for recording a cash transaction (bureau only)."""

    type = SelectField(
        "Type",
        choices=_CASH_ENTRY_TYPE_CHOICES,
        default=CashEntryType.OTHER.value,
    )
    amount = DecimalField(
        "Montant (€)",
        places=2,
        validators=[DataRequired(message="Le montant est obligatoire.")],
    )
    note = StringField(
        "Note",
        validators=[Optional(), Length(max=500)],
    )


class CashBoxCloseForm(FlaskForm):
    """Form for closing and reconciling a cash box (bureau only)."""

    closing_amount = DecimalField(
        "Montant compté (€)",
        places=2,
        validators=[
            InputRequired(message="Le montant de clôture est obligatoire."),
            NumberRange(min=0, message="Le montant ne peut pas être négatif."),
        ],
    )
    reconciliation_note = TextAreaField(
        "Note de réconciliation",
        validators=[Optional(), Length(max=2000)],
    )


class EventMachineForm(FlaskForm):
    """Form for linking a machine to an event (bureau only)."""

    machine_id = SelectField("Machine", coerce=int, validators=[DataRequired()])
    comment = TextAreaField("Commentaire", validators=[Optional(), Length(max=500)])


class EventSlotForm(FlaskForm):
    """Form for adding a time slot to an event (bureau only)."""

    slot_date = DateField(
        "Date",
        validators=[DataRequired(message="La date est obligatoire.")],
    )
    start_time = TimeField(
        "Heure de début",
        validators=[Optional()],
    )
    end_time = TimeField(
        "Heure de fin",
        validators=[Optional()],
    )
    label = StringField(
        "Étiquette",
        validators=[Optional(), Length(max=100)],
    )


class AvailabilityForm(FlaskForm):
    """Form for a member to declare availability on a slot."""

    status = SelectField(
        "Disponibilité",
        choices=[
            ("present", "Présent"),
            ("maybe", "Peut-être"),
            ("absent", "Absent"),
        ],
        validators=[DataRequired()],
    )


class VolunteerIdentityForm(FlaskForm):
    """Public form for a volunteer to register with name + email."""

    class Meta:
        csrf = False

    name = StringField(
        "Votre nom complet",
        validators=[DataRequired(message="Le nom est obligatoire."), Length(max=100)],
    )
    email = StringField(
        "Votre adresse email",
        validators=[DataRequired(message="L'email est obligatoire."), Length(max=254)],
    )
    website = HiddenField(validators=[Optional()])


class VolunteerAvailabilityForm(FlaskForm):
    """Public form for a volunteer to register on a slot."""

    class Meta:
        csrf = False

    status = SelectField(
        "Disponibilité",
        choices=[
            ("present", "Présent"),
            ("maybe", "Peut-être"),
            ("absent", "Absent"),
        ],
        validators=[DataRequired()],
    )
