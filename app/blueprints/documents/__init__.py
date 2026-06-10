"""Documents blueprint — file upload, gallery, and download."""

from flask import Blueprint

bp = Blueprint(
    "documents",
    __name__,
    url_prefix="/documents",
    template_folder="templates",
)

from app.blueprints.documents import routes  # noqa: E402, F401
