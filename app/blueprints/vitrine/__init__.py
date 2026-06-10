"""Vitrine blueprint — public-facing association page (no authentication required)."""

from flask import Blueprint

bp = Blueprint("vitrine", __name__, url_prefix="/vitrine", template_folder="templates")

from app.blueprints.vitrine import routes  # noqa: E402, F401
