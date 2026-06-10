"""Document model and junction table factory for file attachments.

All uploaded files are stored outside the webroot under
``UPLOAD_FOLDER/<type>s/<stored_filename>`` and accessed only through the
documents blueprint (which validates authentication before serving).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Table, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db

if TYPE_CHECKING:
    from app.models.user import User


class DocumentType(StrEnum):
    INVOICE = "invoice"
    PHOTO = "photo"
    VIDEO = "video"
    REPORT = "report"
    CONTRACT = "contract"
    CERFA = "cerfa"
    MACHINE = "machine"
    RECEIPT = "receipt"
    OTHER = "other"


# ---------------------------------------------------------------------------
# Junction table factory
# ---------------------------------------------------------------------------


def make_document_junction_table(
    table_name: str,
    fk_col: str,
    fk_table: str,
) -> Table:
    """Return a many-to-many junction table linking an entity to Documents.

    Args:
        table_name: SQL table name (e.g. ``"event_documents"``).
        fk_col: Column name for the entity FK (e.g. ``"event_id"``).
        fk_table: Referenced table name (e.g. ``"events"``).

    Returns:
        A SQLAlchemy :class:`~sqlalchemy.Table` instance registered on the
        shared metadata.
    """
    return Table(
        table_name,
        db.Model.metadata,
        Column(
            fk_col,
            Integer,
            ForeignKey(f"{fk_table}.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        Column(
            "document_id",
            Integer,
            ForeignKey("documents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )


# ---------------------------------------------------------------------------
# Junction tables — one per associated entity
# ---------------------------------------------------------------------------

event_documents = make_document_junction_table("event_documents", "event_id", "events")
machine_documents = make_document_junction_table("machine_documents", "machine_id", "machines")
center_documents = make_document_junction_table("center_documents", "center_id", "centers")
meeting_documents = make_document_junction_table("meeting_documents", "meeting_id", "meetings")
expense_documents = make_document_junction_table("expense_documents", "expense_id", "expenses")
maintenance_documents = make_document_junction_table(
    "maintenance_documents", "maintenance_record_id", "maintenance_records"
)
center_feedback_documents = make_document_junction_table(
    "center_feedback_documents", "center_feedback_id", "center_feedbacks"
)
task_documents = make_document_junction_table("task_documents", "task_id", "tasks")


# ---------------------------------------------------------------------------
# Document model
# ---------------------------------------------------------------------------


class Document(db.Model):
    """An uploaded file (image, video, PDF, contract, etc.).

    Attributes:
        id: Primary key.
        original_filename: The user-supplied filename (display only, not used for storage).
        stored_filename: The sanitised filename on disk (convention: YYYY-MM-DD_<type>_<slug>.ext).
        type: Document category enum.
        category: Optional free-text category tag.
        mime_type: Detected MIME type.
        size_bytes: File size in bytes.
        uploaded_by_id: FK to the User who uploaded the file.
        uploaded_at: Upload timestamp (UTC).
        description: Optional description.
    """

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    type: Mapped[str] = mapped_column(String(20), nullable=False, default=DocumentType.OTHER.value)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    uploaded_by_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    drive_file_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    drive_web_link: Mapped[str | None] = mapped_column(String(500), nullable=True)

    uploaded_by: Mapped[User] = relationship("User", foreign_keys=[uploaded_by_id])

    @property
    def subdir(self) -> str:
        """Subdirectory under UPLOAD_FOLDER where this file is stored."""
        return {
            DocumentType.PHOTO.value: "photos",
            DocumentType.VIDEO.value: "videos",
            DocumentType.INVOICE.value: "invoices",
            DocumentType.REPORT.value: "reports",
            DocumentType.CONTRACT.value: "contracts",
            DocumentType.CERFA.value: "cerfas",
            DocumentType.MACHINE.value: "machines",
            DocumentType.RECEIPT.value: "receipts",
            DocumentType.OTHER.value: "documents",
        }.get(self.type, "documents")

    def __repr__(self) -> str:
        return f"<Document {self.original_filename!r} type={self.type}>"
