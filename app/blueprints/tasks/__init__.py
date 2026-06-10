"""Tasks blueprint — task board, detail, assignment, and comments."""

from flask import Blueprint

bp = Blueprint(
    "tasks",
    __name__,
    url_prefix="/tasks",
    template_folder="templates",
)

from app.blueprints.tasks import routes  # noqa: E402, F401
