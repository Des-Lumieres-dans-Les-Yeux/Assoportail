"""Authentication forms — login, user creation (admin only), and password change."""

from flask_wtf import FlaskForm
from wtforms import BooleanField, EmailField, PasswordField, SelectField, StringField
from wtforms.validators import DataRequired, Email, EqualTo, Length


class LoginForm(FlaskForm):
    """Form for authenticating an existing user."""

    email = EmailField(
        "Adresse email",
        validators=[
            DataRequired(message="L'adresse email est requise."),
            Email(message="Adresse email invalide."),
        ],
    )
    password = PasswordField(
        "Mot de passe",
        validators=[DataRequired(message="Le mot de passe est requis.")],
    )
    remember_me = BooleanField("Se souvenir de moi")


class CreateUserForm(FlaskForm):
    """Form for creating a new user account (bureau admins only).

    A temporary password is generated server-side and sent by email.
    No password fields are shown to the admin.
    """

    first_name = StringField(
        "Prénom",
        validators=[
            DataRequired(message="Le prénom est requis."),
            Length(min=1, max=100, message="Le prénom ne peut pas dépasser 100 caractères."),
        ],
    )
    last_name = StringField(
        "Nom",
        validators=[
            DataRequired(message="Le nom est requis."),
            Length(min=1, max=100, message="Le nom ne peut pas dépasser 100 caractères."),
        ],
    )
    email = EmailField(
        "Adresse email",
        validators=[
            DataRequired(message="L'adresse email est requise."),
            Email(message="Adresse email invalide."),
        ],
    )
    role = SelectField(
        "Rôle",
        choices=[("member", "Membre"), ("bureau", "Bureau")],
        default="member",
    )


class TotpCodeForm(FlaskForm):
    """Form for entering a 6-digit TOTP code (setup confirmation and login verification)."""

    code = StringField(
        "Code à 6 chiffres",
        validators=[
            DataRequired(message="Le code est requis."),
            Length(min=6, max=6, message="Le code doit contenir exactement 6 chiffres."),
        ],
    )


class ForgotPasswordForm(FlaskForm):
    """Form for requesting a password reset link."""

    email = EmailField(
        "Adresse email",
        validators=[
            DataRequired(message="L'adresse email est requise."),
            Email(message="Adresse email invalide."),
        ],
    )


class ResetPasswordForm(FlaskForm):
    """Form for setting a new password via a reset token."""

    new_password = PasswordField(
        "Nouveau mot de passe",
        validators=[
            DataRequired(message="Le nouveau mot de passe est requis."),
            Length(min=12, message="Le mot de passe doit contenir au moins 12 caractères."),
        ],
    )
    new_password_confirm = PasswordField(
        "Confirmer le nouveau mot de passe",
        validators=[
            DataRequired(message="Veuillez confirmer votre mot de passe."),
            EqualTo("new_password", message="Les mots de passe ne correspondent pas."),
        ],
    )


class ChangePasswordForm(FlaskForm):
    """Form for changing password (forced on first login)."""

    current_password = PasswordField(
        "Mot de passe actuel",
        validators=[DataRequired(message="Le mot de passe actuel est requis.")],
    )
    new_password = PasswordField(
        "Nouveau mot de passe",
        validators=[
            DataRequired(message="Le nouveau mot de passe est requis."),
            Length(min=12, message="Le mot de passe doit contenir au moins 12 caractères."),
        ],
    )
    new_password_confirm = PasswordField(
        "Confirmer le nouveau mot de passe",
        validators=[
            DataRequired(message="Veuillez confirmer votre mot de passe."),
            EqualTo("new_password", message="Les mots de passe ne correspondent pas."),
        ],
    )
