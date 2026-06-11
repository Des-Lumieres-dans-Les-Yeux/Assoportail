"""WTForms form definitions for center management and feedback."""

from flask_wtf import FlaskForm
from wtforms import (
    DateField,
    FloatField,
    HiddenField,
    SelectField,
    StringField,
    TextAreaField,
)
from wtforms.validators import DataRequired, Email, Length, Optional

from app.models.center import CenterStatus
from app.models.task import TaskPriority

_STATUS_CHOICES = [
    (CenterStatus.PROSPECT.value, "Prospect"),
    (CenterStatus.TO_INSTALL.value, "À installer"),
    (CenterStatus.ACTIVE.value, "Partenaire actif"),
    (CenterStatus.INACTIVE.value, "Inactif"),
    (CenterStatus.LOST.value, "Perdu"),
]

_PATHOLOGY_CHOICES = [
    ("", "Non précisé"),
    ("oncologie", "Oncologie"),
    ("handicap mental", "Handicap mental"),
    ("psychiatrie", "Psychiatrie"),
    ("handicap moteur", "Handicap moteur"),
]

_AUDIENCE_CHOICES = [
    ("", "Non précisé"),
    ("jeunes", "Jeunes"),
    ("adultes", "Adultes"),
]

_PRIORITY_CHOICES = [
    (TaskPriority.HIGH.value, "Haute"),
    (TaskPriority.URGENT.value, "Urgente"),
]

_RATING_CHOICES = [("", "Sans évaluation")] + [(str(i), "★" * i) for i in range(1, 6)]


class CenterForm(FlaskForm):
    """Form for creating or editing a partner center."""

    name = StringField(
        "Nom du centre",
        validators=[DataRequired(message="Le nom est obligatoire."), Length(max=200)],
    )
    address = StringField("Adresse", validators=[Optional(), Length(max=255)])
    city = StringField(
        "Ville",
        validators=[DataRequired(message="La ville est obligatoire."), Length(max=100)],
    )
    zip_code = StringField(
        "Code postal",
        validators=[DataRequired(message="Le code postal est obligatoire."), Length(max=10)],
    )
    status = SelectField("Statut", choices=_STATUS_CHOICES, default=CenterStatus.PROSPECT.value)
    pathology = SelectField("Pathologie", choices=_PATHOLOGY_CHOICES, default="")
    target_audience = SelectField("Catégorie / Public", choices=_AUDIENCE_CHOICES, default="")
    latitude = FloatField("Latitude", validators=[Optional()])
    longitude = FloatField("Longitude", validators=[Optional()])
    notes = TextAreaField("Notes internes", validators=[Optional(), Length(max=2000)])


class CenterContactForm(FlaskForm):
    """Form for adding or editing a contact person for a center (bureau only)."""

    name = StringField(
        "Nom",
        validators=[DataRequired(message="Le nom est obligatoire."), Length(max=100)],
    )
    role = StringField("Rôle / fonction", validators=[Optional(), Length(max=100)])
    email = StringField(
        "Email",
        validators=[Optional(), Email(message="Adresse email invalide."), Length(max=254)],
    )
    phone = StringField("Téléphone", validators=[Optional(), Length(max=30)])


class BreakdownReportForm(FlaskForm):
    """Form for a member to report a machine breakdown at a center."""

    description = TextAreaField(
        "Description du problème",
        validators=[
            DataRequired(message="La description est obligatoire."),
            Length(max=1000),
        ],
    )
    priority = SelectField(
        "Priorité",
        choices=_PRIORITY_CHOICES,
        default=TaskPriority.HIGH.value,
    )


class InstallMachineForm(FlaskForm):
    """Form for installing a machine into a center (bureau only)."""

    machine_id = SelectField("Machine", coerce=int, validators=[DataRequired()])
    installed_at = DateField(
        "Date d'installation",
        validators=[DataRequired(message="La date est obligatoire.")],
    )


class FeedbackForm(FlaskForm):
    """Public feedback / guestbook entry form (signed URL, no auth)."""

    submitted_by = StringField(
        "Votre nom",
        validators=[DataRequired(message="Votre nom est obligatoire."), Length(max=100)],
    )
    content = TextAreaField(
        "Votre témoignage",
        validators=[
            DataRequired(message="Le témoignage est obligatoire."),
            Length(
                min=10,
                max=2000,
                message="Le témoignage doit contenir entre 10 et 2000 caractères.",
            ),
        ],
    )
    rating = SelectField("Évaluation", choices=_RATING_CHOICES, default="")
    # Honeypot — must remain empty; bots fill it in
    website = HiddenField(validators=[Optional()])


class InstallationRequestForm(FlaskForm):
    """Public installation request form."""

    center_name = StringField(
        "Nom de l'établissement / centre",
        validators=[DataRequired(message="Le nom du centre est obligatoire."), Length(max=200)],
    )
    address = StringField("Adresse", validators=[Optional(), Length(max=255)])
    city = StringField(
        "Ville",
        validators=[DataRequired(message="La ville est obligatoire."), Length(max=100)],
    )
    zip_code = StringField(
        "Code postal",
        validators=[DataRequired(message="Le code postal est obligatoire."), Length(max=10)],
    )
    contact_name = StringField(
        "Nom du contact principal",
        validators=[DataRequired(message="Le nom du contact est obligatoire."), Length(max=100)],
    )
    contact_role = StringField(
        "Rôle / fonction du contact", validators=[Optional(), Length(max=100)]
    )
    contact_email = StringField(
        "Email du contact",
        validators=[
            DataRequired(message="L'email est obligatoire."),
            Email(message="Adresse email invalide."),
            Length(max=254),
        ],
    )
    contact_phone = StringField("Téléphone du contact", validators=[Optional(), Length(max=30)])
    motivation = TextAreaField(
        "Motivation pour la demande / présentation du centre.",
        validators=[
            DataRequired(
                message="Veuillez présenter votre centre et motiver votre demande. "
                "Indiquer le nombre de patients, les pathologies présentes.."
            ),
            Length(
                min=10,
                max=4000,
                message="La motivation doit contenir entre 10 et 4000 caractères.",
            ),
        ],
    )
    # Honeypot
    website = HiddenField(validators=[Optional()])
