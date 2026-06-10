"""WTForms form definitions for social publishing."""

from flask_wtf import FlaskForm
from wtforms import (
    DateTimeLocalField,
    FileField,
    SelectField,
    SelectMultipleField,
    StringField,
    TextAreaField,
)
from wtforms.validators import DataRequired, Length, Optional
from wtforms.widgets import CheckboxInput, ListWidget


class SocialPostForm(FlaskForm):
    """Form for creating or editing a social post."""

    title = StringField(
        "Titre",
        validators=[DataRequired(message="Le titre est obligatoire."), Length(max=300)],
    )
    body_html = TextAreaField(
        "Contenu",
        validators=[DataRequired(message="Le contenu est obligatoire.")],
    )
    platforms = SelectMultipleField(
        "Plateformes",
        choices=[],  # Populated dynamically from active SocialAccount rows
        widget=ListWidget(prefix_label=False),
        option_widget=CheckboxInput(),
    )
    instagram_format = SelectField(
        "Format Instagram",
        choices=[
            ("square", "Carré (1:1)"),
            ("portrait", "Portrait (4:5)"),
            ("landscape", "Paysage (1.91:1)"),
        ],
        default="square",
    )
    scheduled_at = DateTimeLocalField(
        "Publication planifiée",
        format="%Y-%m-%dT%H:%M",
        validators=[Optional()],
    )


class SocialAccountForm(FlaskForm):
    """Form for connecting a social account."""

    platform = SelectField(
        "Plateforme",
        choices=[
            ("wordpress", "WordPress"),
            ("facebook", "Facebook"),
            ("instagram", "Instagram"),
            ("linkedin", "LinkedIn"),
        ],
    )
    display_name = StringField(
        "Nom d'affichage",
        validators=[DataRequired(message="Le nom est obligatoire."), Length(max=200)],
    )
    # WordPress-specific fields
    site_url = StringField("URL du site WordPress", validators=[Optional(), Length(max=500)])
    username = StringField("Nom d'utilisateur", validators=[Optional(), Length(max=100)])
    app_password = StringField(
        "Mot de passe d'application",
        validators=[Optional(), Length(max=200)],
    )


class ImageUploadForm(FlaskForm):
    """Form for uploading an image to a social post."""

    file = FileField("Image", validators=[DataRequired()])


class ImageCropForm(FlaskForm):
    """Form for saving crop coordinates (HTMX/JSON)."""

    class Meta:
        csrf = False  # Called via HTMX with X-CSRFToken header

    crop_data = TextAreaField("Crop data JSON", validators=[DataRequired()])
