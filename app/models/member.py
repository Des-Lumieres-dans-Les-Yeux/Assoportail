"""Membership model — annual association memberships."""

from __future__ import annotations

import enum
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    case,
)
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.extensions import db

if TYPE_CHECKING:
    from app.models.user import User


class MembershipSource(enum.StrEnum):
    """Origin of a membership subscription."""

    HELLOASSO = "helloasso"
    CASH = "cash"


class MembershipStatus(enum.StrEnum):
    """Computed status of a membership record."""

    PENDING = "pending"
    ACTIVE = "active"
    EXPIRED = "expired"


class Membership(db.Model):
    """Annual association membership record.

    Attributes:
        id: Primary key.
        user_id: FK to the member (User).
        source: Payment channel — HelloAsso or direct cash.
        amount: Amount paid in EUR (2 decimal places).
        started_at: First day the membership is valid.
        expires_at: Last day the membership is valid.
        renewed_at: Date when a previous membership was renewed into this one.
        helloasso_order_id: HelloAsso order reference (unique, nullable).
        is_pending: True while awaiting HelloAsso payment confirmation.
        notes: Optional free-text notes.
        created_at: Record creation timestamp (UTC).
    """

    __tablename__ = "memberships"
    __auditable__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[MembershipSource] = mapped_column(
        Enum(
            MembershipSource,
            name="membership_source",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    started_at: Mapped[date] = mapped_column(Date, nullable=False)
    expires_at: Mapped[date] = mapped_column(Date, nullable=False)
    renewed_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    helloasso_order_id: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True)
    is_pending: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    user: Mapped[User] = relationship("User", back_populates="memberships")

    @hybrid_property
    def status(self) -> MembershipStatus:
        """Compute the current membership status.

        Returns:
            PENDING if awaiting HelloAsso confirmation.
            ACTIVE if expires_at is in the future.
            EXPIRED otherwise.
        """
        if self.is_pending:
            return MembershipStatus.PENDING
        if self.expires_at > date.today():
            return MembershipStatus.ACTIVE
        return MembershipStatus.EXPIRED

    @status.expression  # type: ignore[no-redef]
    @classmethod
    def status(cls):
        """SQL expression for server-side filtering on membership status."""
        today = func.current_date()
        return case(
            (cls.is_pending.is_(True), MembershipStatus.PENDING.value),
            (cls.expires_at > today, MembershipStatus.ACTIVE.value),
            else_=MembershipStatus.EXPIRED.value,
        )

    def __repr__(self) -> str:
        return (
            f"<Membership user_id={self.user_id} "
            f"source={self.source.value} expires={self.expires_at}>"
        )
