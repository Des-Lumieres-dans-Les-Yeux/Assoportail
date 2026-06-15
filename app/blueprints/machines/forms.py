"""WTForms form definitions for machine management."""

from datetime import date

from flask_wtf import FlaskForm
from wtforms import DateField, DecimalField, IntegerField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, NumberRange, Optional

from app.models.machine import MachineStatus

_STATUS_CHOICES = [
    (MachineStatus.STOCK.value, "En stock"),
    (MachineStatus.INSTALLED.value, "Installée"),
    (MachineStatus.MAINTENANCE.value, "En maintenance"),
    (MachineStatus.RETIRED.value, "Retirée"),
]

_LOCATION_TYPE_CHOICES = [
    ("center", "Centre"),
    ("member", "Membre"),
]


class MachineForm(FlaskForm):
    """Form for creating or editing a machine record."""

    internal_number = StringField(
        "Numéro interne",
        validators=[Optional(), Length(max=50)],
    )
    model = StringField(
        "Modèle",
        validators=[DataRequired(message="Le modèle est obligatoire."), Length(max=100)],
    )
    manufacturer = StringField(
        "Fabricant",
        validators=[DataRequired(message="Le fabricant est obligatoire."), Length(max=100)],
    )
    serial_number = StringField(
        "Numéro de série",
        validators=[Optional(), Length(max=100)],
    )
    year = IntegerField(
        "Année",
        validators=[
            Optional(),
            NumberRange(min=1950, max=2100, message="Année invalide."),
        ],
    )
    status = SelectField(
        "Statut",
        choices=_STATUS_CHOICES,
        default=MachineStatus.STOCK.value,
    )
    purchase_date = DateField(
        "Date d'achat",
        validators=[Optional()],
    )
    purchase_price = DecimalField(
        "Montant d'achat (€)",
        places=2,
        validators=[Optional(), NumberRange(min=0, message="Le montant doit être positif.")],
    )
    estimated_value = DecimalField(
        "Valeur estimée actuelle (€)",
        places=2,
        validators=[Optional(), NumberRange(min=0, message="Le montant doit être positif.")],
    )
    notes = TextAreaField("Notes", validators=[Optional(), Length(max=2000)])


class InstallMachineForm(FlaskForm):
    """Form for recording a new machine installation at a center or member."""

    location_type = SelectField(
        "Type d'emplacement",
        choices=_LOCATION_TYPE_CHOICES,
        default="center",
    )
    center_id = SelectField(
        "Centre", coerce=lambda v: int(v) if v else None, validators=[Optional()]
    )
    hosted_by_id = SelectField(
        "Membre hébergeant", coerce=lambda v: int(v) if v else None, validators=[Optional()]
    )
    installed_at = DateField(
        "Date d'installation",
        validators=[DataRequired(message="La date d'installation est obligatoire.")],
        default=date.today,
    )
    notes = TextAreaField("Notes", validators=[Optional(), Length(max=500)])


class RemoveInstallationForm(FlaskForm):
    """Form for recording the retrieval of a machine."""

    removed_at = DateField(
        "Date de récupération",
        validators=[DataRequired(message="La date de récupération est obligatoire.")],
        default=date.today,
    )
    move_to_member_id = SelectField(
        "Déplacer chez un membre",
        coerce=lambda v: int(v) if v else None,
        validators=[Optional()],
    )


class MaintenanceRecordForm(FlaskForm):
    """Form for logging a maintenance operation on a machine."""

    date = DateField(
        "Date",
        validators=[DataRequired(message="La date est obligatoire.")],
        default=date.today,
    )
    description = TextAreaField(
        "Description",
        validators=[
            DataRequired(message="La description est obligatoire."),
            Length(max=2000),
        ],
    )
    cost = DecimalField(
        "Coût (€)",
        places=2,
        validators=[Optional(), NumberRange(min=0, message="Le coût doit être positif.")],
    )
    maintainer_name = StringField(
        "Intervenant",
        validators=[
            DataRequired(message="Le nom de l'intervenant est obligatoire."),
            Length(max=100),
        ],
    )
    source_task_id = IntegerField(
        "ID de la tâche liée",
        validators=[Optional()],
    )


class ResolveMaintenanceForm(FlaskForm):
    """Form for resolving an open maintenance record."""

    resolved_at = DateField(
        "Date de résolution",
        validators=[DataRequired(message="La date de résolution est obligatoire.")],
        default=date.today,
    )
    resolution_comment = TextAreaField(
        "Commentaire de résolution",
        validators=[Optional(), Length(max=2000)],
    )


class PublicBreakdownForm(FlaskForm):
    """Public form for a center to report a machine breakdown (no auth)."""

    class Meta:
        csrf = False  # Public form — protected by token in URL

    reporter_name = StringField(
        "Votre nom",
        validators=[DataRequired(message="Le nom est requis."), Length(max=100)],
    )
    description = TextAreaField(
        "Description du problème",
        validators=[
            DataRequired(message="La description est obligatoire."),
            Length(max=2000),
        ],
    )
    machine_id = SelectField("Machine concernée", coerce=int, validators=[DataRequired()])


class PublicMachineBreakdownForm(FlaskForm):
    """Public form to report a breakdown on a specific machine (machine QR code).

    The machine is identified by the token in the URL, so there is no machine
    selector here.
    """

    class Meta:
        csrf = False  # Public form — protected by token in URL

    reporter_name = StringField(
        "Votre nom",
        validators=[DataRequired(message="Le nom est requis."), Length(max=100)],
    )
    description = TextAreaField(
        "Description du problème",
        validators=[
            DataRequired(message="La description est obligatoire."),
            Length(max=2000),
        ],
    )
