"""WTForms form definitions for mailing campaigns."""

from flask_wtf import FlaskForm
from wtforms import DateTimeLocalField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional


class CampaignForm(FlaskForm):
    """Form for creating or editing a mailing campaign (bureau only)."""

    name = StringField(
        "Nom de la campagne",
        validators=[DataRequired(message="Le nom est obligatoire."), Length(max=200)],
    )
    subject = StringField(
        "Sujet de l'email",
        validators=[DataRequired(message="Le sujet est obligatoire."), Length(max=500)],
    )
    body_html = TextAreaField(
        "Corps de l'email (HTML)",
        validators=[DataRequired(message="Le corps est obligatoire.")],
    )
    scheduled_at = DateTimeLocalField(
        "Envoi planifié",
        format="%Y-%m-%dT%H:%M",
        validators=[Optional()],
    )
    audience = SelectField(
        "Audience",
        choices=[
            ("members", "Membres de l'association"),
            ("center_contacts", "Contacts des centres actifs (avec machine)"),
            ("both", "Membres + contacts des centres actifs"),
            ("tombola", "Participants d'une tombola"),
        ],
        default="members",
    )
    membership_status = SelectField(
        "Destinataires — statut adhésion",
        choices=[
            ("active", "Adhérents actifs uniquement"),
            ("all", "Tous les membres actifs"),
        ],
        default="active",
    )
    role = SelectField(
        "Destinataires — rôle",
        choices=[
            ("all", "Tous"),
            ("member", "Membres seulement"),
            ("bureau", "Bureau seulement"),
        ],
        default="all",
    )
