"""WTForms form definitions for member management."""

from datetime import date, timedelta

from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    DateField,
    DecimalField,
    PasswordField,
    SelectField,
    SelectMultipleField,
    StringField,
    TextAreaField,
    widgets,
)
from wtforms.validators import DataRequired, Email, Length, NumberRange, Optional, ValidationError

from app.models.user import MEMBER_DEFAULT_PERMISSIONS, UserGender, UserPermission, UserRole

_ROLE_CHOICES = [(r.value, r.value.capitalize()) for r in UserRole]
_GENDER_CHOICES = [
    (UserGender.NOT_SPECIFIED.value, "Non précisé"),
    (UserGender.MALE.value, "Homme"),
    (UserGender.FEMALE.value, "Femme"),
    (UserGender.OTHER.value, "Autre"),
]
_PERMISSION_CHOICES = [(p.value, p.value.capitalize()) for p in UserPermission]


class MultiCheckboxField(SelectMultipleField):
    """A multiple-select, except it displays a list of checkboxes."""

    widget = widgets.ListWidget(prefix_label=False)
    option_widget = widgets.CheckboxInput()


class MemberCreateForm(FlaskForm):
    """Form for creating a new member (bureau only).

    A temporary password is required; the member should change it on first login.
    """

    first_name = StringField(
        "Prénom",
        validators=[DataRequired(message="Le prénom est obligatoire."), Length(max=100)],
    )
    last_name = StringField(
        "Nom",
        validators=[DataRequired(message="Le nom est obligatoire."), Length(max=100)],
    )
    email = StringField(
        "Adresse email",
        validators=[
            DataRequired(message="L'adresse email est obligatoire."),
            Email(message="Adresse email invalide."),
            Length(max=254),
        ],
    )
    password = PasswordField(
        "Mot de passe temporaire",
        validators=[
            DataRequired(message="Un mot de passe temporaire est obligatoire."),
            Length(min=12, message="Le mot de passe doit contenir au moins 12 caractères."),
        ],
    )
    role = SelectField(
        "Rôle",
        choices=_ROLE_CHOICES,
        default=UserRole.MEMBER.value,
    )
    gender = SelectField(
        "Genre",
        choices=_GENDER_CHOICES,
        default=UserGender.NOT_SPECIFIED.value,
    )
    phone = StringField("Téléphone", validators=[Optional(), Length(max=30)])
    address = StringField("Adresse postale", validators=[Optional(), Length(max=255)])
    permissions = MultiCheckboxField(
        "Permissions (pour les membres)",
        choices=_PERMISSION_CHOICES,
        default=list(MEMBER_DEFAULT_PERMISSIONS),
    )


class MemberEditForm(FlaskForm):
    """Form for editing an existing member's profile (bureau only)."""

    first_name = StringField(
        "Prénom",
        validators=[DataRequired(message="Le prénom est obligatoire."), Length(max=100)],
    )
    last_name = StringField(
        "Nom",
        validators=[DataRequired(message="Le nom est obligatoire."), Length(max=100)],
    )
    email = StringField(
        "Adresse email",
        validators=[
            DataRequired(message="L'adresse email est obligatoire."),
            Email(message="Adresse email invalide."),
            Length(max=254),
        ],
    )
    role = SelectField("Rôle", choices=_ROLE_CHOICES)
    gender = SelectField("Genre", choices=_GENDER_CHOICES)
    phone = StringField("Téléphone", validators=[Optional(), Length(max=30)])
    address = StringField("Adresse postale", validators=[Optional(), Length(max=255)])
    permissions = MultiCheckboxField(
        "Permissions (pour les membres)",
        choices=_PERMISSION_CHOICES,
    )
    is_active = BooleanField("Compte actif")


class ProfileEditForm(FlaskForm):
    """Form for members to edit their own personal information."""

    first_name = StringField(
        "Prénom",
        validators=[DataRequired(message="Le prénom est obligatoire."), Length(max=100)],
    )
    last_name = StringField(
        "Nom",
        validators=[DataRequired(message="Le nom est obligatoire."), Length(max=100)],
    )
    email = StringField(
        "Adresse email",
        validators=[
            DataRequired(message="L'adresse email est obligatoire."),
            Email(message="Adresse email invalide."),
            Length(max=254),
        ],
    )
    gender = SelectField("Genre", choices=_GENDER_CHOICES)
    phone = StringField("Téléphone", validators=[Optional(), Length(max=30)])
    address = StringField("Adresse postale", validators=[Optional(), Length(max=255)])


class CashMembershipForm(FlaskForm):
    """Form for recording a cash membership payment (bureau only)."""

    amount = DecimalField(
        "Montant (€)",
        places=2,
        validators=[
            DataRequired(message="Le montant est obligatoire."),
            NumberRange(min=0, message="Le montant doit être positif."),
        ],
    )
    started_at = DateField(
        "Date de début",
        validators=[DataRequired(message="La date de début est obligatoire.")],
        default=date.today,
    )
    expires_at = DateField(
        "Date d'expiration",
        validators=[DataRequired(message="La date d'expiration est obligatoire.")],
        default=lambda: date.today() + timedelta(days=365),
    )
    notes = TextAreaField(
        "Notes",
        validators=[Optional(), Length(max=500)],
    )

    def validate_expires_at(self, field):
        if field.data and self.started_at.data and field.data <= self.started_at.data:
            raise ValidationError("La date d'expiration doit être postérieure à la date de début.")


class HelloAssoMembershipForm(FlaskForm):
    """Form for declaring a HelloAsso membership (no payment entry, just dates)."""

    started_at = DateField(
        "Date de début",
        validators=[DataRequired(message="La date de début est obligatoire.")],
        default=date.today,
    )
    expires_at = DateField(
        "Date d'expiration",
        validators=[DataRequired(message="La date d'expiration est obligatoire.")],
        default=lambda: date.today() + timedelta(days=365),
    )
    notes = TextAreaField(
        "Notes",
        validators=[Optional(), Length(max=500)],
    )

    def validate_expires_at(self, field):
        if field.data and self.started_at.data and field.data <= self.started_at.data:
            raise ValidationError("La date d'expiration doit être postérieure à la date de début.")


class DeleteMemberForm(FlaskForm):
    """CSRF-only confirmation form for member deletion."""


class ApiTokenCreateForm(FlaskForm):
    """Form for generating a new API token."""

    name = StringField(
        "Libellé",
        validators=[DataRequired(message="Le libellé est obligatoire."), Length(max=100)],
        render_kw={"placeholder": "ex. openclaw, automatisation site web"},
    )
    expires_days = SelectField(
        "Expiration",
        validators=[Optional()],
        choices=[("", "Jamais"), ("30", "30 jours"), ("90", "90 jours"), ("365", "1 an")],
        coerce=lambda x: int(x) if x else None,
        default="",
    )


class ApiTokenRevokeForm(FlaskForm):
    """CSRF-only confirmation form for token revocation."""
