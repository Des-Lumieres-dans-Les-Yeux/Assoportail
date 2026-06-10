"""Authentication blueprint — login, logout, registration."""

from flask import Blueprint

bp = Blueprint(
    "auth",
    __name__,
    url_prefix="/auth",
    template_folder="templates",
)

from app.blueprints.auth import routes  # noqa: E402, F401
