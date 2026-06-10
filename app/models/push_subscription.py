"""PushSubscription model — stores Web Push endpoint/key pairs per user."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db


class PushSubscription(db.Model):
    """A browser's Web Push subscription linked to a portal user.

    Each row represents one browser/device where the user has granted
    notification permission.  Rows are deleted when the user unsubscribes
    or when a push delivery returns a 410 Gone (subscription expired).

    Attributes:
        id: Primary key.
        user_id: FK to the owning User.
        endpoint: Browser push service URL (unique per subscription).
        p256dh: Client's public EC Diffie-Hellman key (base64url).
        auth: Authentication secret (base64url).
        created_at: UTC timestamp of subscription creation.
    """

    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    endpoint: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    p256dh: Mapped[str] = mapped_column(Text, nullable=False)
    auth: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
