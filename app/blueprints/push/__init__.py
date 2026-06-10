"""Push notifications blueprint."""

from flask import Blueprint

bp = Blueprint("push", __name__, url_prefix="/push")

from app.blueprints.push import routes  # noqa: F401, E402
