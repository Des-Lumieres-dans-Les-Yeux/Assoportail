"""WTForms form definitions for treasury management."""

from flask_wtf import FlaskForm
from wtforms import (
    DateField,
    DecimalField,
    EmailField,
    SelectField,
    SelectMultipleField,
    StringField,
    TextAreaField,
)
from wtforms.validators import DataRequired, Email, Length, NumberRange, Optional, Regexp

from app.models.treasury import TransactionSource, TransactionType

_TYPE_CHOICES = [
    (TransactionType.INCOME.value, "Recette"),
    (TransactionType.EXPENSE.value, "Dépense"),
]

_SOURCE_CHOICES = [
    (TransactionSource.MANUAL.value, "Saisie manuelle"),
    (TransactionSource.DONATION.value, "Don"),
    (TransactionSource.MEMBERSHIP.value, "Adhésion"),
]

_CGI_CHOICES = [
    ("200", "Art. 200 CGI — particuliers (réduction IR 66%)"),
    ("238 bis", "Art. 238 bis CGI — entreprises (mécénat, réduction IS 60%)"),
]

# CERFA page-1 "Cochez la case concernée" — verbatim official categories.
# Keys MUST match app.services.cerfa.ORG_CATEGORY_FIELDS (1 case cochée = 1 clé).
_CERFA_ORG_CATEGORY_CHOICES = [
    ("oeuvre", "Œuvre ou organisme d'intérêt général"),
    (
        "rup",
        "Association/fondation reconnue d'utilité publique (ou mission RUP en Alsace-Moselle)",
    ),
    ("alsace_moselle", "Association cultuelle ou de bienfaisance reconnue d'Alsace-Moselle"),
    (
        "aide_alimentaire",
        "Aide alimentaire / soins médicaux / logement de personnes en difficulté",
    ),
    ("fondation_entreprise", "Fondation d'entreprise"),
    ("fondation_universitaire", "Fondation universitaire ou partenariale (L. 719-12 / L. 719-13)"),
    ("fondation_patrimoine", "Fondation du patrimoine (ou association affectant les dons à celle-ci)"),
    ("musee", "Musée de France"),
    ("enseignement_sup", "Établissement d'enseignement supérieur ou artistique d'intérêt général"),
    ("creation_entreprises", "Organisme de financement de la création d'entreprises"),
    ("festivals", "Organisme dont l'activité principale est l'organisation de festivals"),
    ("recherche", "Établissement de recherche d'intérêt général à but non lucratif"),
    ("recherche_agree", "Société ou organisme agréé de recherche scientifique ou technique"),
    ("anr", "Agence nationale de la recherche (ANR)"),
    ("entreprise_insertion", "Entreprise d'insertion ou de travail temporaire d'insertion"),
    ("association_intermediaire", "Association intermédiaire (L. 5132-7 du code du travail)"),
    ("ateliers_insertion", "Ateliers et chantiers d'insertion (L. 5132-15 du code du travail)"),
    ("entreprises_adaptees", "Entreprises adaptées (L. 5213-13 du code du travail)"),
    ("autres", "Autres organismes"),
]


class TransactionForm(FlaskForm):
    """Form for creating or editing a financial transaction (bureau only)."""

    type = SelectField(
        "Type",
        choices=_TYPE_CHOICES,
        default=TransactionType.EXPENSE.value,
    )
    amount = DecimalField(
        "Montant (€)",
        places=2,
        validators=[
            DataRequired(message="Le montant est obligatoire."),
            NumberRange(min=0.01, message="Le montant doit être positif."),
        ],
    )
    date = DateField(
        "Date",
        validators=[DataRequired(message="La date est obligatoire.")],
    )
    description = TextAreaField(
        "Description",
        validators=[
            DataRequired(message="La description est obligatoire."),
            Length(max=1000),
        ],
    )
    category = StringField(
        "Catégorie",
        validators=[Optional(), Length(max=100)],
    )
    source = SelectField(
        "Source",
        choices=_SOURCE_CHOICES,
        default=TransactionSource.MANUAL.value,
    )
    tag_ids = SelectMultipleField(
        "Étiquettes",
        coerce=int,
        validators=[Optional()],
    )
    # Donor fields — displayed/required only when source = donation
    donation_type = SelectField(
        "Type de don",
        choices=[
            ("particulier", "Don de particulier"),
            ("mecena", "Mécénat entreprise"),
            ("nature", "Don en nature"),
        ],
        default="particulier",
        validate_choice=False,
    )
    donor_first_name = StringField(
        "Prénom",
        validators=[Optional(), Length(max=100)],
    )
    donor_name = StringField(
        "Nom / Raison sociale",
        validators=[Optional(), Length(max=200)],
    )
    donor_address = StringField(
        "Adresse",
        validators=[Optional(), Length(max=255)],
    )
    donor_zip = StringField(
        "Code postal",
        validators=[Optional(), Length(max=10)],
    )
    donor_city = StringField(
        "Ville",
        validators=[Optional(), Length(max=100)],
    )
    donor_email = EmailField(
        "Email du donateur",
        validators=[Optional(), Email(), Length(max=255)],
    )
    donor_description = TextAreaField(
        "Description du don (don en nature)",
        validators=[Optional(), Length(max=1000)],
    )


class TagForm(FlaskForm):
    """Form for creating a tag (bureau only)."""

    label = StringField(
        "Libellé",
        validators=[
            DataRequired(message="Le libellé est obligatoire."),
            Length(max=50),
        ],
    )
    color = StringField(
        "Couleur",
        default="#6c757d",
        validators=[
            DataRequired(message="La couleur est obligatoire."),
            Regexp(
                r"^#[0-9A-Fa-f]{6}$",
                message="La couleur doit être au format hexadécimal (#RRGGBB).",
            ),
        ],
    )


class AssociationConfigForm(FlaskForm):
    """Form for editing the association's legal identity (bureau only)."""

    name = StringField(
        "Nom de l'association",
        validators=[DataRequired(message="Le nom est obligatoire."), Length(max=200)],
    )
    address = StringField("Adresse", validators=[Optional(), Length(max=255)])
    zip_code = StringField("Code postal", validators=[Optional(), Length(max=10)])
    city = StringField("Ville", validators=[Optional(), Length(max=100)])
    siret = StringField(
        "N° SIRET",
        validators=[
            Optional(),
            Regexp(r"^\d{14}$", message="Le SIRET doit contenir exactement 14 chiffres."),
        ],
    )
    rna = StringField(
        "N° RNA",
        validators=[
            Optional(),
            Regexp(r"^W\d{9}$", message="Le RNA doit être au format W + 9 chiffres."),
        ],
    )
    legal_form = StringField(
        "Forme juridique",
        validators=[Optional(), Length(max=100)],
        default="Association loi 1901",
    )
    purpose = TextAreaField(
        "Objet de l'association",
        validators=[Optional(), Length(max=500)],
    )
    cgi_article = SelectField(
        "Article CGI applicable",
        choices=_CGI_CHOICES,
        default="200",
    )
    cerfa_org_category = SelectField(
        "Catégorie de l'organisme (case cochée sur le CERFA)",
        choices=_CERFA_ORG_CATEGORY_CHOICES,
        default="oeuvre",
    )
    representative_name = StringField(
        "Nom du représentant légal",
        validators=[Optional(), Length(max=200)],
    )
    representative_title = StringField(
        "Titre / fonction",
        validators=[Optional(), Length(max=100)],
    )
    km_rate = DecimalField(
        "Indemnité kilométrique (€/km)",
        places=3,
        validators=[
            Optional(),
            NumberRange(min=0.001, message="Le taux doit être positif."),
        ],
        default=0.603,
    )
