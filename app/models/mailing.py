"""Mailing campaign and recipient models."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.extensions import db

if TYPE_CHECKING:
    from app.models.center import Center
    from app.models.user import User


class CampaignStatus(StrEnum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"


class RecipientStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    BOUNCED = "bounced"
    OPENED = "opened"


class BounceType(StrEnum):
    HARD = "hard"
    SOFT = "soft"


class MailingCampaign(db.Model):
    """An email campaign sent to a filtered subset of users.

    Attributes:
        id: Primary key.
        name: Human-readable campaign name.
        subject: Email subject line.
        body_html: HTML body of the email.
        status: Current campaign lifecycle state.
        scheduled_at: When the campaign is scheduled to send (nullable).
        sent_at: When the campaign finished sending (nullable).
        created_by_id: FK to the bureau user who created it.
        created_at: Creation timestamp.
        recipients_filter: JSON dict of filter criteria applied when resolving
            recipients (e.g. ``{"membership_status": "active", "role": "all"}``).
        stats_sent: Count of successfully sent recipients.
        stats_bounced: Count of bounced recipients.
    """

    __tablename__ = "mailing_campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    body_html: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CampaignStatus.DRAFT.value, index=True
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    recipients_filter: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=lambda: {"membership_status": "active", "role": "all"}
    )
    stats_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stats_bounced: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stats_opened: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_by: Mapped[User] = relationship("User", foreign_keys=[created_by_id])
    recipients: Mapped[list[MailingRecipient]] = relationship(
        "MailingRecipient", back_populates="campaign", cascade="all, delete-orphan"
    )

    @property
    def is_editable(self) -> bool:
        """True if the campaign can still be edited (draft or scheduled)."""
        return self.status in (CampaignStatus.DRAFT.value, CampaignStatus.SCHEDULED.value)

    def __repr__(self) -> str:
        return f"<MailingCampaign {self.name!r} status={self.status}>"


class MailingRecipient(db.Model):
    """One recipient entry for a mailing campaign.

    Attributes:
        id: Primary key.
        campaign_id: FK to the parent MailingCampaign.
        user_id: FK to the User.
        email: Snapshot of the recipient's email address at resolution time.
        status: Delivery status.
        sent_at: When the email was sent (nullable).
        bounced_at: When a bounce was detected (nullable).
        opened_at: When the email was opened (nullable, future).
        bounce_type: Hard or soft bounce (nullable).
    """

    __tablename__ = "mailing_recipients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("mailing_campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    center_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("centers.id", ondelete="CASCADE"), nullable=True, index=True
    )
    recipient_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    email: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=RecipientStatus.PENDING.value
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bounced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bounce_type: Mapped[str | None] = mapped_column(String(10), nullable=True)

    campaign: Mapped[MailingCampaign] = relationship("MailingCampaign", back_populates="recipients")
    user: Mapped[User | None] = relationship("User", foreign_keys=[user_id])
    center: Mapped[Center | None] = relationship("Center", foreign_keys=[center_id])

    @property
    def display_name(self) -> str:
        """Name to display — from linked User or stored recipient_name."""
        if self.user:
            return self.user.full_name
        return self.recipient_name or self.email

    def __repr__(self) -> str:
        return (
            f"<MailingRecipient campaign={self.campaign_id}"
            f" email={self.email!r} status={self.status}>"
        )
