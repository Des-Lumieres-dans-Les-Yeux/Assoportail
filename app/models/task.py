"""Task and TaskComment models."""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db

if TYPE_CHECKING:
    from app.models.center import Center
    from app.models.document import Document
    from app.models.email import InboundEmail
    from app.models.event import Event
    from app.models.machine import Machine
    from app.models.meeting import Meeting
    from app.models.user import User


class TaskStatus(enum.StrEnum):
    """Task progress state."""

    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"


class TaskPriority(enum.StrEnum):
    """Task urgency level."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class TaskSource(enum.StrEnum):
    """How the task was created."""

    MANUAL = "manual"
    EMAIL = "email"
    MEETING = "meeting"
    CENTER_BREAKDOWN = "center_breakdown"
    EVENT = "event"


class Task(db.Model):
    """A work item that can be assigned and tracked.

    Attributes:
        id: Primary key.
        title: Short description of the work to do.
        description: Optional longer body.
        status: Current progress state.
        priority: Urgency level.
        created_by_id: FK to the User who opened the task.
        assigned_to_id: FK to the User responsible (nullable).
        source: How the task was created.
        source_email_id: FK to InboundEmail if source is EMAIL (nullable).
        source_meeting_id: FK to Meeting if source is MEETING (nullable).
        source_center_id: FK to Center if source is CENTER_BREAKDOWN (nullable).
        source_event_id: FK to Event if source is EVENT (nullable).
        due_date: Deadline (UTC, nullable).
        completed_at: When the task moved to DONE (UTC, nullable).
        created_at: Creation timestamp (UTC).
        updated_at: Last modification timestamp (UTC).
    """

    __tablename__ = "tasks"
    __auditable__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, name="task_status", values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=TaskStatus.OPEN,
    )
    priority: Mapped[TaskPriority] = mapped_column(
        Enum(
            TaskPriority, name="task_priority", values_callable=lambda obj: [e.value for e in obj]
        ),
        nullable=False,
        default=TaskPriority.NORMAL,
    )
    created_by_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assigned_to_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source: Mapped[TaskSource] = mapped_column(
        Enum(TaskSource, name="task_source", values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=TaskSource.MANUAL,
    )
    source_email_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("inbound_emails.id", ondelete="SET NULL"), nullable=True
    )
    source_meeting_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("meetings.id", ondelete="SET NULL"), nullable=True
    )
    source_center_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("centers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_event_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("events.id", ondelete="SET NULL"), nullable=True, index=True
    )
    machine_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("machines.id", ondelete="SET NULL"), nullable=True, index=True
    )
    due_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    created_by: Mapped[User] = relationship("User", foreign_keys=[created_by_id])
    assigned_to: Mapped[User | None] = relationship("User", foreign_keys=[assigned_to_id])
    source_center: Mapped[Center | None] = relationship("Center", foreign_keys=[source_center_id])
    source_email: Mapped[InboundEmail | None] = relationship(
        "InboundEmail", foreign_keys=[source_email_id]
    )
    source_meeting: Mapped[Meeting | None] = relationship(
        "Meeting", foreign_keys=[source_meeting_id]
    )
    source_event: Mapped[Event | None] = relationship("Event", foreign_keys=[source_event_id])
    machine: Mapped[Machine | None] = relationship(
        "Machine", foreign_keys=[machine_id], back_populates="tasks"
    )
    documents: Mapped[list[Document]] = relationship("Document", secondary="task_documents")
    comments: Mapped[list[TaskComment]] = relationship(
        "TaskComment",
        back_populates="task",
        order_by="TaskComment.created_at",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Task {self.title!r} status={self.status.value}>"


class TaskComment(db.Model):
    """A comment thread entry on a Task.

    Attributes:
        id: Primary key.
        task_id: FK to the parent Task.
        author_id: FK to the User who wrote the comment.
        body: Comment text.
        created_at: Creation timestamp (UTC).
    """

    __tablename__ = "task_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    task: Mapped[Task] = relationship("Task", back_populates="comments")
    author: Mapped[User] = relationship("User", foreign_keys=[author_id])

    def __repr__(self) -> str:
        return f"<TaskComment task={self.task_id} author={self.author_id}>"
