"""Center and CenterFeedback models."""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, SmallInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db

if TYPE_CHECKING:
    from app.models.document import Document
    from app.models.machine import MachineInstallation, MaintenanceRecord
    from app.models.user import User


class CenterStatus(enum.StrEnum):
    """Partnership status with a healthcare center."""

    PROSPECT = "prospect"
    TO_INSTALL = "to_install"
    ACTIVE = "active"
    INACTIVE = "inactive"
    LOST = "lost"


class Center(db.Model):
    """A healthcare center that hosts pinball machines.

    Attributes:
        id: Primary key.
        name: Official name of the center.
        address: Street address (nullable).
        city: City.
        zip_code: Postal code.
        status: Partnership status.
        notes: Internal notes (nullable).
        created_at: Record creation timestamp (UTC).
    """

    __tablename__ = "centers"
    __auditable__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    zip_code: Mapped[str] = mapped_column(String(10), nullable=False)
    latitude: Mapped[float | None] = mapped_column(db.Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(db.Float, nullable=True)
    pathology: Mapped[str | None] = mapped_column(String(100), nullable=True)
    target_audience: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[CenterStatus] = mapped_column(
        Enum(
            CenterStatus, name="center_status", values_callable=lambda obj: [e.value for e in obj]
        ),
        nullable=False,
        default=CenterStatus.PROSPECT,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    convention_document_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    feedback_token: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    breakdown_token: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    contacts: Mapped[list[CenterContact]] = relationship(
        "CenterContact",
        back_populates="center",
        order_by="CenterContact.created_at",
        cascade="all, delete-orphan",
    )
    installations: Mapped[list[MachineInstallation]] = relationship(
        "MachineInstallation",
        back_populates="center",
        order_by="MachineInstallation.installed_at.desc()",
    )
    convention_document: Mapped[Document | None] = relationship(
        "Document", foreign_keys=[convention_document_id]
    )
    documents: Mapped[list[Document]] = relationship("Document", secondary="center_documents")
    maintenance_records: Mapped[list[MaintenanceRecord]] = relationship(
        "MaintenanceRecord",
        back_populates="center",
        order_by="MaintenanceRecord.date.desc()",
    )
    feedbacks: Mapped[list[CenterFeedback]] = relationship(
        "CenterFeedback",
        back_populates="center",
        order_by="CenterFeedback.submitted_at.desc()",
        cascade="all, delete-orphan",
    )
    installation_requests: Mapped[list[InstallationRequest]] = relationship(
        "InstallationRequest",
        back_populates="created_center",
        foreign_keys="[InstallationRequest.created_center_id]",
    )

    @property
    def active_installations(self) -> list[MachineInstallation]:
        """Return only current (non-removed) installations."""
        return [i for i in self.installations if i.removed_at is None]

    def __repr__(self) -> str:
        return f"<Center {self.name!r} status={self.status.value}>"


class CenterContact(db.Model):
    """A contact person for a partner center.

    Attributes:
        id: Primary key.
        center_id: FK to the Center.
        name: Full name of the contact.
        role: Optional role / title (e.g. "Directeur", "Animateur").
        email: Contact email (nullable).
        phone: Contact phone (nullable).
        created_at: Record creation timestamp (UTC).
    """

    __tablename__ = "center_contacts"
    __auditable__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    center_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("centers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str | None] = mapped_column(String(100), nullable=True)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    center: Mapped[Center] = relationship("Center", back_populates="contacts")

    def __repr__(self) -> str:
        return f"<CenterContact {self.name!r} center={self.center_id}>"


class CenterFeedback(db.Model):
    """A guestbook entry submitted by a center contact via signed URL.

    Attributes:
        id: Primary key.
        center_id: FK to the Center.
        submitted_by: Free-text name of the person who submitted.
        submitted_at: Submission timestamp (UTC).
        content: Testimonial text.
        rating: Optional 1–5 score.
        is_published: Whether this entry appears in the public guestbook.
        published_by_id: FK to the bureau User who approved it (nullable).
        published_at: When it was published (nullable).
    """

    __tablename__ = "center_feedbacks"
    __auditable__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    center_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("centers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    submitted_by: Mapped[str] = mapped_column(String(100), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    rating: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    published_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    center: Mapped[Center] = relationship("Center", back_populates="feedbacks")
    published_by: Mapped[User | None] = relationship("User", foreign_keys=[published_by_id])
    documents: Mapped[list[Document]] = relationship(
        "Document", secondary="center_feedback_documents"
    )

    def __repr__(self) -> str:
        return f"<CenterFeedback center={self.center_id} published={self.is_published}>"


class InstallationRequest(db.Model):
    """An installation request submitted by a prospective center.

    Attributes:
        id: Primary key.
        center_name: Name of the proposed center.
        address: Street address (nullable).
        city: City.
        zip_code: Postal code.
        contact_name: Contact person's name.
        contact_role: Contact person's role (nullable).
        contact_email: Contact person's email.
        contact_phone: Contact person's phone (nullable).
        motivation: Statement explaining why they want a machine.
        status: Current status of the request (e.g. pending, approved, rejected).
        created_at: Submission timestamp (UTC).
        processed_at: When the request was processed (nullable).
        processed_by_id: FK to the user who processed it (nullable).
        created_center_id: FK to the Center created from this request (nullable).
    """

    __tablename__ = "installation_requests"
    __auditable__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    center_name: Mapped[str] = mapped_column(String(200), nullable=False)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    zip_code: Mapped[str] = mapped_column(String(10), nullable=False)

    contact_name: Mapped[str] = mapped_column(String(100), nullable=False)
    contact_role: Mapped[str | None] = mapped_column(String(100), nullable=True)
    contact_email: Mapped[str] = mapped_column(String(254), nullable=False)
    contact_phone: Mapped[str | None] = mapped_column(String(30), nullable=True)

    motivation: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processed_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_center_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("centers.id", ondelete="SET NULL"), nullable=True
    )

    processed_by: Mapped[User | None] = relationship("User", foreign_keys=[processed_by_id])
    created_center: Mapped[Center | None] = relationship(
        "Center",
        foreign_keys=[created_center_id],
        back_populates="installation_requests",
    )

    def __repr__(self) -> str:
        return f"<InstallationRequest center={self.center_name!r} status={self.status}>"
