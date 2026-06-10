"""Mailbox blueprint — inbound email viewer and rule management."""

from flask import Blueprint

bp = Blueprint(
    "mailbox",
    __name__,
    url_prefix="/mailbox",
    template_folder="templates",
)

from app.blueprints.mailbox import routes  # noqa: E402, F401
