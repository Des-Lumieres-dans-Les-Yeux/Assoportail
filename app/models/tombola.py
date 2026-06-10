"""Tombola and TombolaTicket models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db

if TYPE_CHECKING:
    from app.models.document import Document
    from app.models.user import User


tombola_documents = Table(
    "tombola_documents",
    db.Model.metadata,
    Column("tombola_id", Integer, ForeignKey("tombolas.id", ondelete="CASCADE"), primary_key=True),
    Column(
        "document_id", Integer, ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    ),
)


class Tombola(db.Model):
    __tablename__ = "tombolas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Public link is generated on demand; null until the bureau creates one.
    slug: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True, index=True)
    draw_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    range_min: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    range_max: Mapped[int] = mapped_column(Integer, nullable=False, default=999)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    created_by_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    anonymized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # use_alter=True defers this FK to ALTER TABLE so SQLAlchemy can sort the
    # circular tombolas↔tombola_tickets dependency for CREATE/DROP ordering.
    winner_ticket_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "tombola_tickets.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_tombolas_winner_ticket_id",
        ),
        nullable=True,
    )

    created_by: Mapped[User] = relationship("User", foreign_keys=[created_by_id])
    tickets: Mapped[list[TombolaTicket]] = relationship(
        "TombolaTicket",
        back_populates="tombola",
        cascade="all, delete-orphan",
        foreign_keys="TombolaTicket.tombola_id",
        order_by="TombolaTicket.id",
    )
    winner: Mapped[TombolaTicket | None] = relationship(
        "TombolaTicket",
        foreign_keys=[winner_ticket_id],
        post_update=True,
    )
    documents: Mapped[list[Document]] = relationship(
        "Document",
        secondary=tombola_documents,
        order_by="Document.uploaded_at",
    )

    @property
    def is_anonymized(self) -> bool:
        return self.anonymized_at is not None

    def __repr__(self) -> str:
        return f"<Tombola {self.name!r}>"


class TombolaTicket(db.Model):
    __tablename__ = "tombola_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tombola_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tombolas.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(254), nullable=False, index=True)
    last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    ticket_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    order_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)

    tombola: Mapped[Tombola] = relationship(
        "Tombola",
        back_populates="tickets",
        foreign_keys="[TombolaTicket.tombola_id]",
    )

    def __repr__(self) -> str:
        return f"<TombolaTicket t={self.tombola_id} email={self.email!r} n={self.ticket_number}>"
