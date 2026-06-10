"""Centers blueprint — partner center management, guestbook, breakdown reporting."""

from flask import Blueprint

bp = Blueprint(
    "centers",
    __name__,
    url_prefix="/centers",
    template_folder="templates",
)

from app.blueprints.centers import routes  # noqa: E402, F401
