"""Dashboard blueprint — main landing page after login."""

from flask import Blueprint

bp = Blueprint(
    "dashboard",
    __name__,
    url_prefix="/",
    template_folder="templates",
)

from app.blueprints.dashboard import routes  # noqa: E402, F401
