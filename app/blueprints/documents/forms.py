"""WTForms form definitions for document upload."""

from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired
from wtforms import HiddenField, SelectField, StringField, TextAreaField
from wtforms.validators import Length, Optional

from app.models.document import DocumentType

_TYPE_CHOICES = [
    (DocumentType.OTHER.value, "Autre"),
    (DocumentType.PHOTO.value, "Photo"),
    (DocumentType.VIDEO.value, "Vidéo"),
    (DocumentType.INVOICE.value, "Facture"),
    (DocumentType.REPORT.value, "Rapport"),
    (DocumentType.CONTRACT.value, "Contrat"),
]


class DocumentUploadForm(FlaskForm):
    """Form for uploading a document, optionally linked to an entity."""

    file = FileField(
        "Fichier",
        validators=[FileRequired(message="Veuillez sélectionner un fichier.")],
    )
    type = SelectField(
        "Type",
        choices=_TYPE_CHOICES,
        default=DocumentType.OTHER.value,
    )
    description = TextAreaField(
        "Description",
        validators=[Optional(), Length(max=500)],
    )
    category = StringField(
        "Catégorie",
        validators=[Optional(), Length(max=100)],
    )
    # Hidden association fields — set by the page embedding this form
    entity_type = HiddenField()
    entity_id = HiddenField()
