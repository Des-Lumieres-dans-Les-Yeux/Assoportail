"""Meeting model and attendee association table."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Table, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db

if TYPE_CHECKING:
    from app.models.document import Document
    from app.models.task import Task
    from app.models.user import User

# Many-to-many: Meeting ↔ User (attendees)
meeting_attendees = Table(
    "meeting_attendees",
    db.Model.metadata,
    Column("meeting_id", Integer, ForeignKey("meetings.id", ondelete="CASCADE"), primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
)

# Many-to-many: Meeting ↔ Task (tasks created/discussed in meeting)
meeting_tasks = Table(
    "meeting_tasks",
    db.Model.metadata,
    Column("meeting_id", Integer, ForeignKey("meetings.id", ondelete="CASCADE"), primary_key=True),
    Column("task_id", Integer, ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True),
)


class Meeting(db.Model):
    """A bureau or general assembly meeting.

    Attributes:
        id: Primary key.
        title: Meeting title / subject.
        date: When the meeting takes place (UTC).
        location: Physical or virtual location (nullable).
        minutes: Full meeting minutes text (nullable, edited after the meeting).
        created_by_id: FK to the User who created the record.
        created_at: Record creation timestamp (UTC).
    """

    __tablename__ = "meetings"
    __auditable__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    minutes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    created_by: Mapped[User] = relationship("User", foreign_keys=[created_by_id])
    attendees: Mapped[list[User]] = relationship(
        "User", secondary=meeting_attendees, order_by="User.last_name"
    )
    tasks: Mapped[list[Task]] = relationship(
        "Task", secondary=meeting_tasks, order_by="Task.created_at"
    )
    documents: Mapped[list[Document]] = relationship("Document", secondary="meeting_documents")

    def __repr__(self) -> str:
        return f"<Meeting {self.title!r} date={self.date.date()}>"
