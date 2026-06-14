"""API v1 blueprint — REST endpoints authenticated by Bearer token."""

from flask import Blueprint

bp = Blueprint(
    "api",
    __name__,
    url_prefix="/api/v1",
)

from app.blueprints.api import routes  # noqa: E402, F401
