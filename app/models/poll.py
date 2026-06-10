"""Poll model — surveys with options and per-user votes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db

if TYPE_CHECKING:
    from app.models.document import Document
    from app.models.user import User


class Poll(db.Model):
    __tablename__ = "polls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    allows_multiple: Mapped[bool] = mapped_column(Boolean, default=False)
    is_closed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    cover_document_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("documents.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    options: Mapped[list[PollOption]] = relationship(
        "PollOption",
        back_populates="poll",
        cascade="all, delete-orphan",
        order_by="PollOption.order",
    )
    votes: Mapped[list[PollVote]] = relationship(
        "PollVote", back_populates="poll", cascade="all, delete-orphan"
    )
    created_by: Mapped[User] = relationship("User", foreign_keys=[created_by_id])
    cover_document: Mapped[Document | None] = relationship(
        "Document", foreign_keys=[cover_document_id]
    )

    @property
    def is_expired(self) -> bool:
        if self.deadline is None:
            return False
        return datetime.now(UTC) > self.deadline

    @property
    def is_active(self) -> bool:
        return not self.is_closed and not self.is_expired

    def user_votes(self, user_id: int) -> list[PollVote]:
        return [v for v in self.votes if v.user_id == user_id]

    def has_voted(self, user_id: int) -> bool:
        return any(v.user_id == user_id for v in self.votes)

    def voter_count(self) -> int:
        return len({v.user_id for v in self.votes})


class PollOption(db.Model):
    __tablename__ = "poll_options"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    poll_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("polls.id"), nullable=False, index=True
    )
    text: Mapped[str] = mapped_column(String(300), nullable=False)
    order: Mapped[int] = mapped_column(Integer, default=0)

    poll: Mapped[Poll] = relationship("Poll", back_populates="options")
    votes: Mapped[list[PollVote]] = relationship(
        "PollVote", back_populates="option", cascade="all, delete-orphan"
    )

    @property
    def vote_count(self) -> int:
        return len(self.votes)


class PollVote(db.Model):
    __tablename__ = "poll_votes"
    __table_args__ = (UniqueConstraint("poll_id", "user_id", "option_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    poll_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("polls.id"), nullable=False, index=True
    )
    option_id: Mapped[int] = mapped_column(Integer, ForeignKey("poll_options.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    voted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    poll: Mapped[Poll] = relationship("Poll", back_populates="votes")
    option: Mapped[PollOption] = relationship("PollOption", back_populates="votes")
    user: Mapped[User] = relationship("User")
