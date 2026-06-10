"""Machines blueprint — machine inventory and maintenance tracking."""

from flask import Blueprint

bp = Blueprint(
    "machines",
    __name__,
    url_prefix="/machines",
    template_folder="templates",
)

from app.blueprints.machines import routes  # noqa: E402, F401
