"""WTForms form definitions for mailbox and email rule management."""

import json

from flask_wtf import FlaskForm
from wtforms import BooleanField, IntegerField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, NumberRange, ValidationError


class EmailRuleForm(FlaskForm):
    """Form for creating or editing an email rule (bureau only).

    Conditions and actions are entered as JSON arrays in text areas.
    """

    name = StringField(
        "Nom de la règle",
        validators=[DataRequired(message="Le nom est obligatoire."), Length(max=100)],
    )
    is_active = BooleanField("Règle active", default=True)
    priority = IntegerField(
        "Priorité (0 = la plus haute)",
        default=10,
        validators=[NumberRange(min=0, max=9999, message="La priorité doit être entre 0 et 9999.")],
    )
    match_mode = SelectField(
        "Mode de correspondance",
        choices=[("all", "Toutes les conditions"), ("any", "Au moins une condition")],
        default="all",
    )
    conditions = TextAreaField(
        "Conditions (JSON)",
        validators=[
            DataRequired(message="Les conditions sont obligatoires."),
            Length(max=5000),
        ],
    )
    actions = TextAreaField(
        "Actions (JSON)",
        validators=[
            DataRequired(message="Les actions sont obligatoires."),
            Length(max=5000),
        ],
    )

    def validate_conditions(self, field: TextAreaField) -> None:
        """Validate that conditions is a non-empty JSON array of condition objects."""
        try:
            data = json.loads(field.data)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValidationError(f"JSON invalide : {exc}") from exc

        if not isinstance(data, list) or not data:
            raise ValidationError("Les conditions doivent être une liste JSON non vide.")

        for cond in data:
            if not isinstance(cond, dict):
                raise ValidationError("Chaque condition doit être un objet JSON.")
            for key in ("field", "operator", "value"):
                if key not in cond:
                    raise ValidationError(f"Clé manquante dans une condition : « {key} ».")
            if cond["field"] not in ("subject", "body", "sender", "recipients"):
                raise ValidationError(
                    f"Champ de condition invalide : « {cond['field']} ». "
                    f"Valeurs acceptées : subject, body, sender, recipients."
                )
            if cond["operator"] not in ("contains", "equals", "regex"):
                raise ValidationError(
                    f"Opérateur invalide : « {cond['operator']} ». "
                    f"Valeurs acceptées : contains, equals, regex."
                )
            if cond["operator"] == "regex" and len(str(cond["value"])) > 500:
                raise ValidationError("Les patterns regex ne peuvent pas dépasser 500 caractères.")

    def validate_actions(self, field: TextAreaField) -> None:
        """Validate that actions is a non-empty JSON array of action objects."""
        try:
            data = json.loads(field.data)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValidationError(f"JSON invalide : {exc}") from exc

        if not isinstance(data, list) or not data:
            raise ValidationError("Les actions doivent être une liste JSON non vide.")

        valid_types = {"create_task", "categorize", "forward_to", "mark_as_read"}
        for action in data:
            if not isinstance(action, dict):
                raise ValidationError("Chaque action doit être un objet JSON.")
            if "type" not in action:
                raise ValidationError("Clé « type » manquante dans une action.")
            if action["type"] not in valid_types:
                raise ValidationError(
                    f"Type d'action inconnu : « {action['type']} ». "
                    f"Valeurs acceptées : {', '.join(sorted(valid_types))}."
                )
            if action["type"] == "forward_to" and not str(action.get("email", "")).strip():
                raise ValidationError("L'action « Transférer à » nécessite une adresse email.")
