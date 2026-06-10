"""Audit logging model and SQLAlchemy event listeners.

Models opt in to audit logging by setting ``__auditable__ = True``.
All create / update / delete operations on opted-in models are recorded
in the ``audit_logs`` table, including a JSON diff for updates.

Implementation note
-------------------
The mapper-level events ``after_insert``, ``after_update``, and ``after_delete``
receive the raw DBAPI *connection* in addition to the mapper and target.  We
write audit rows using ``connection.execute()`` directly — **not**
``session.add()`` — because adding to the session during the flush stage is
explicitly unsupported by SQLAlchemy and produces undefined behaviour.

Usage::

    class MyModel(db.Model):
        __auditable__ = True
        ...
"""

import enum
import json
import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

from sqlalchemy import DateTime, Enum, Integer, String, Text, event
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db


class AuditAction(enum.StrEnum):
    """Type of action recorded in an audit log entry."""

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class AuditLog(db.Model):
    """Immutable record of state-changing operations on audited models.

    Attributes:
        id: Primary key.
        user_id: ID of the user who triggered the action; None for system actions
            (Celery tasks, migrations, fixtures).
        timestamp: UTC datetime when the action occurred.
        entity_type: Table name of the affected model (e.g. ``"users"``).
        entity_id: Primary key of the affected record.
        action: The type of operation performed.
        changes: JSON string mapping field names to ``{"old": …, "new": …}`` dicts.
            Only populated for UPDATE actions.
    """

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        index=True,
    )
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    action: Mapped[AuditAction] = mapped_column(
        Enum(AuditAction, values_callable=lambda obj: [e.value for e in obj]), nullable=False
    )
    changes: Mapped[str | None] = mapped_column(Text, nullable=True)

    @property
    def changes_dict(self) -> dict[str, Any]:
        """Parse stored JSON changes into a dictionary.

        Returns:
            Dict of field → {old, new} pairs, or an empty dict if no changes recorded.
        """
        if self.changes:
            return json.loads(self.changes)
        return {}

    def __repr__(self) -> str:
        return f"<AuditLog {self.action.value} {self.entity_type}:{self.entity_id}>"


def _current_user_id() -> int | None:
    """Return the authenticated user's ID, or None.

    Returns None when called outside a request context (Celery tasks,
    fixtures, migrations) to avoid loading a detached User proxy.
    """
    try:
        from flask import has_request_context

        if not has_request_context():
            return None

        from flask_login import current_user

        if current_user and current_user.is_authenticated:
            return int(current_user.id)
    except Exception:
        logger.debug("Could not resolve current user id for audit log", exc_info=True)
    return None


def _insert_log(
    connection: Any,
    entity_type: str,
    entity_id: int,
    action: AuditAction,
    changes: str | None = None,
) -> None:
    """Write one audit log row directly via the DBAPI connection.

    Using ``connection.execute()`` instead of ``session.add()`` is mandatory
    inside SQLAlchemy mapper events, where adding to the session is unsupported.
    """
    connection.execute(
        AuditLog.__table__.insert().values(
            user_id=_current_user_id(),
            timestamp=datetime.now(UTC),
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            changes=changes,
        )
    )


def register_audit_listeners() -> None:
    """Attach SQLAlchemy mapper event listeners that populate ``audit_logs``.

    Must be called after all models are imported so that SQLAlchemy's mapper
    registry is complete. Called once from the application factory.
    """

    def _entity_type(instance: Any) -> str:
        return str(instance.__tablename__)

    def after_insert(mapper: Any, connection: Any, target: Any) -> None:  # noqa: ARG001
        if not getattr(target, "__auditable__", False):
            return
        _insert_log(connection, _entity_type(target), target.id, AuditAction.CREATE)

    def after_update(mapper: Any, connection: Any, target: Any) -> None:  # noqa: ARG001
        if not getattr(target, "__auditable__", False):
            return
        from sqlalchemy import inspect as sa_inspect

        inspector = sa_inspect(target)
        changes: dict[str, Any] = {}
        for attr in inspector.attrs:
            history = attr.history
            if history.has_changes():
                old = history.deleted[0] if history.deleted else None
                new = history.added[0] if history.added else None
                if old != new and attr.key not in {"updated_at", "password_hash"}:
                    changes[attr.key] = {"old": old, "new": new}
        if not changes:
            return
        _insert_log(
            connection,
            _entity_type(target),
            target.id,
            AuditAction.UPDATE,
            json.dumps(changes, default=str),
        )

    def after_delete(mapper: Any, connection: Any, target: Any) -> None:  # noqa: ARG001
        if not getattr(target, "__auditable__", False):
            return
        _insert_log(connection, _entity_type(target), target.id, AuditAction.DELETE)

    event.listen(db.Model, "after_insert", after_insert, propagate=True)
    event.listen(db.Model, "after_update", after_update, propagate=True)
    event.listen(db.Model, "after_delete", after_delete, propagate=True)
