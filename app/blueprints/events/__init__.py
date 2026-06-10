"""Events blueprint — event management, expenses, and cash box."""

from flask import Blueprint

bp = Blueprint(
    "events",
    __name__,
    url_prefix="/events",
    template_folder="templates",
)

from app.blueprints.events import routes  # noqa: E402, F401
