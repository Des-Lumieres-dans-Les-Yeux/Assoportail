"""Treasury models — Transaction and Tag."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, Numeric, String, Table, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db

if TYPE_CHECKING:
    from app.models.user import User


# Many-to-many: Transaction ↔ Tag
transaction_tags = Table(
    "transaction_tags",
    db.Model.metadata,
    Column(
        "transaction_id",
        Integer,
        ForeignKey("transactions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "tag_id",
        Integer,
        ForeignKey("tags.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class TransactionType(StrEnum):
    INCOME = "income"
    EXPENSE = "expense"


class TransactionSource(StrEnum):
    MANUAL = "manual"
    EVENT = "event"
    EXPENSE = "expense"
    DONATION = "donation"
    MEMBERSHIP = "membership"


class Tag(db.Model):
    """A label that can be applied to transactions for categorisation.

    Attributes:
        id: Primary key.
        label: Short display name.
        color: Hex color code (e.g. ``#3498db``).
    """

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    color: Mapped[str] = mapped_column(String(7), nullable=False, default="#6c757d")

    transactions: Mapped[list[Transaction]] = relationship(
        "Transaction", secondary=transaction_tags, back_populates="tags"
    )

    def __repr__(self) -> str:
        return f"<Tag {self.label!r}>"


class Transaction(db.Model):
    """A financial transaction (income or expense).

    Attributes:
        id: Primary key.
        type: ``income`` or ``expense``.
        amount: Absolute amount in euros (always positive; type determines sign).
        date: Transaction date (no time component).
        description: Free-text description.
        category: Optional free-text category (e.g. "loyer", "carburant").
        created_by_id: FK to the User who recorded the transaction.
        source: Where the transaction originates.
        source_id: Optional ID of the originating entity (event, expense, etc.).
        created_at: Record creation timestamp (UTC).
    """

    __tablename__ = "transactions"
    __auditable__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(10), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_by_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default=TransactionSource.MANUAL.value
    )
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    # Donor information — filled when source = DONATION
    donation_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    donor_first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    donor_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    donor_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    donor_zip: Mapped[str | None] = mapped_column(String(10), nullable=True)
    donor_city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    donor_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    donor_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Sequential per-year receipt number "DON-AAAA-NNNNN", assigned on first
    # generation and never reused.
    cerfa_number: Mapped[str | None] = mapped_column(String(20), nullable=True, unique=True)
    cerfa_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cerfa_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cerfa_drive_file_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    cerfa_drive_web_link: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_by: Mapped[User] = relationship("User", foreign_keys=[created_by_id])
    tags: Mapped[list[Tag]] = relationship(
        "Tag", secondary=transaction_tags, back_populates="transactions", order_by="Tag.label"
    )

    @property
    def signed_amount(self) -> Decimal:
        """Amount with sign: positive for income, negative for expense."""
        if self.type == TransactionType.EXPENSE.value:
            return -self.amount
        return self.amount

    def __repr__(self) -> str:
        return f"<Transaction {self.type} {self.amount}€ on {self.date}>"
