"""Treasury blueprint — financial transaction management."""

from flask import Blueprint

bp = Blueprint(
    "treasury",
    __name__,
    url_prefix="/treasury",
    template_folder="templates",
)

from app.blueprints.treasury import routes  # noqa: E402, F401
