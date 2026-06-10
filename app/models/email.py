"""Inbound email, email rule, rule log, and Gmail token models."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Table, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.extensions import db

if TYPE_CHECKING:
    from app.models.document import Document
    from app.models.event import Event
    from app.models.task import Task
    from app.models.user import User


# Many-to-many: InboundEmail ↔ Document (attachments)
inbound_email_attachments = Table(
    "inbound_email_attachments",
    db.Model.metadata,
    Column(
        "inbound_email_id",
        Integer,
        ForeignKey("inbound_emails.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "document_id",
        Integer,
        ForeignKey("documents.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class MatchMode(StrEnum):
    ALL = "all"
    ANY = "any"


class InboundEmail(db.Model):
    """An email imported from the association's Gmail inbox.

    Attributes:
        id: Primary key.
        gmail_message_id: Unique Gmail message identifier.
        subject: Email subject.
        sender: Sender address.
        recipients: Comma-separated list of recipient addresses.
        body_text: Plain text body (nullable).
        body_html: HTML body (nullable).
        received_at: When the email was received by Gmail.
        imported_at: When the email was imported into the portal.
        category: Optional free-text category assigned by a rule.
        processed: True if rules have been evaluated against this email.
        generated_task_id: FK to a Task created from this email (nullable).
    """

    __tablename__ = "inbound_emails"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    gmail_message_id: Mapped[str] = mapped_column(
        String(200), nullable=False, unique=True, index=True
    )
    subject: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sender: Mapped[str] = mapped_column(String(500), nullable=False)
    recipients: Mapped[str] = mapped_column(Text, nullable=False, default="")
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    processed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    generated_task_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "tasks.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_inbound_emails_generated_task_id",
        ),
        nullable=True,
    )
    event_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("events.id", ondelete="SET NULL"), nullable=True, index=True
    )

    generated_task: Mapped[Task | None] = relationship("Task", foreign_keys=[generated_task_id])
    event: Mapped[Event | None] = relationship("Event", foreign_keys=[event_id])
    documents: Mapped[list[Document]] = relationship(
        "Document", secondary="inbound_email_attachments"
    )

    def __repr__(self) -> str:
        return f"<InboundEmail {self.gmail_message_id!r} from={self.sender!r}>"


class EmailRule(db.Model):
    """A conditional rule that auto-processes inbound emails.

    Conditions and actions are stored as JSON lists.

    Condition format::

        [{"field": "subject", "operator": "contains", "value": "panne"},
         {"field": "sender", "operator": "regex", "value": "^admin@.*"}]

    Operators: ``contains``, ``equals``, ``regex`` (max 500 chars, 1 s timeout).

    Action format::

        [{"type": "create_task", "priority": "urgent"},
         {"type": "categorize", "category": "maintenance"}]

    Action types: ``create_task``, ``categorize``.

    Attributes:
        id: Primary key.
        name: Unique human-readable rule name.
        is_active: Whether this rule is evaluated.
        priority: Evaluation order (lower = first).
        match_mode: ``all`` (all conditions must match) or ``any``.
        conditions: JSON list of condition objects.
        actions: JSON list of action objects.
        created_by_id: FK to the bureau user who created the rule.
        created_at: Creation timestamp (UTC).
    """

    __tablename__ = "email_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    match_mode: Mapped[str] = mapped_column(String(10), nullable=False, default=MatchMode.ALL.value)
    conditions: Mapped[list] = mapped_column(JSON, nullable=False)
    actions: Mapped[list] = mapped_column(JSON, nullable=False)
    created_by_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    created_by: Mapped[User] = relationship("User", foreign_keys=[created_by_id])

    def __repr__(self) -> str:
        return f"<EmailRule {self.name!r} active={self.is_active}>"


class EmailRuleLog(db.Model):
    """Records which rules were applied to an inbound email.

    Attributes:
        id: Primary key.
        rule_id: FK to the EmailRule that was applied.
        email_id: FK to the InboundEmail that triggered the rule.
        actions_triggered: JSON snapshot of the actions that ran.
        applied_at: When the rule was applied.
    """

    __tablename__ = "email_rule_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("email_rules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("inbound_emails.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actions_triggered: Mapped[list] = mapped_column(JSON, nullable=False)
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    rule: Mapped[EmailRule] = relationship("EmailRule", foreign_keys=[rule_id])
    email: Mapped[InboundEmail] = relationship("InboundEmail", foreign_keys=[email_id])


class GoogleAppCredentials(db.Model):
    """Single-row table holding the Google OAuth2 app credentials (client_id/secret).

    The credentials.json content is stored here so the bureau can upload it
    via the portal instead of copying files into the container.

    Only one row (id=1) should ever exist.

    Attributes:
        id: Primary key (always 1).
        credentials_json: Raw JSON string from the downloaded credentials.json.
        updated_at: When the credentials were last updated.
    """

    __tablename__ = "google_app_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    credentials_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    def __repr__(self) -> str:
        return f"<GoogleAppCredentials updated={self.updated_at}>"


class GmailToken(db.Model):
    """Single-row table holding the encrypted Gmail OAuth2 token.

    Only one row (id=1) should ever exist.

    Attributes:
        id: Primary key (always 1).
        token_encrypted: MultiFernet-encrypted JSON token blob.
        updated_at: When the token was last refreshed/updated.
    """

    __tablename__ = "gmail_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    def __repr__(self) -> str:
        return f"<GmailToken updated={self.updated_at}>"
