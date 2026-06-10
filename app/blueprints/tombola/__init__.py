"""Tombola blueprint — tombola management and public ticket lookup."""

from flask import Blueprint

bp = Blueprint(
    "tombola",
    __name__,
    url_prefix="/tombola",
    template_folder="templates",
)

from app.blueprints.tombola import routes  # noqa: E402, F401
