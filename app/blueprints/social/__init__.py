"""Social publishing blueprint — multi-platform blog and social-media posts."""

from flask import Blueprint

bp = Blueprint(
    "social",
    __name__,
    url_prefix="/social",
    template_folder="templates",
)

from app.blueprints.social import routes  # noqa: E402, F401
