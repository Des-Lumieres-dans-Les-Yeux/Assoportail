"""WTForms definitions for the tombola blueprint."""

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileRequired
from wtforms import DateField, IntegerField, StringField
from wtforms.validators import DataRequired, Email, Length, NumberRange, Optional


class CreateTombolaForm(FlaskForm):
    name = StringField("Nom de la tombola", validators=[DataRequired(), Length(max=200)])
    draw_date = DateField("Date du tirage", validators=[Optional()])


# Reused for editing — same fields, different route.
EditTombolaForm = CreateTombolaForm


class MediaUploadForm(FlaskForm):
    file = FileField(
        "Photo ou vidéo",
        validators=[
            FileRequired(message="Veuillez sélectionner un fichier."),
            FileAllowed(
                ["jpg", "jpeg", "png", "gif", "webp", "mp4", "webm"],
                "Image ou vidéo uniquement.",
            ),
        ],
    )


class UploadTicketsForm(FlaskForm):
    file = FileField(
        "Fichier (CSV ou XLSX)",
        validators=[
            FileRequired(message="Veuillez sélectionner un fichier."),
            FileAllowed(["csv", "xlsx"], "CSV ou XLSX uniquement."),
        ],
    )


class AssignNumbersForm(FlaskForm):
    # Defaults come from the Tombola instance via ``form = AssignNumbersForm(obj=tombola)``.
    range_min = IntegerField("Numéro minimum", validators=[NumberRange(min=0)])
    range_max = IntegerField("Numéro maximum", validators=[NumberRange(min=0)])


class PublicLookupForm(FlaskForm):
    email = StringField(
        "Votre adresse e-mail", validators=[DataRequired(), Email(), Length(max=254)]
    )
