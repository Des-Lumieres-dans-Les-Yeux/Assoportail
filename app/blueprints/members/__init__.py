"""Members blueprint — member management and profile views."""

from flask import Blueprint

bp = Blueprint(
    "members",
    __name__,
    url_prefix="/members",
    template_folder="templates",
)

from app.blueprints.members import routes  # noqa: E402, F401
