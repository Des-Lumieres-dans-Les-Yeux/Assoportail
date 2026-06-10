"""Meetings blueprint — bureau and general assembly meetings."""

from flask import Blueprint

bp = Blueprint(
    "meetings",
    __name__,
    url_prefix="/meetings",
    template_folder="templates",
)

from app.blueprints.meetings import routes  # noqa: E402, F401
