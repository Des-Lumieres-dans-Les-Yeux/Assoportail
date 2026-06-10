"""Mailing blueprint — campaign creation and sending."""

from flask import Blueprint

bp = Blueprint(
    "mailing",
    __name__,
    url_prefix="/mailing",
    template_folder="templates",
)

from app.blueprints.mailing import routes  # noqa: E402, F401
