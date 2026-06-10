"""Social publishing models — posts, images, accounts, and publish logs."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.extensions import db

if TYPE_CHECKING:
    from app.models.document import Document
    from app.models.user import User


class SocialPlatform(StrEnum):
    """Supported social-media / blog platforms."""

    WORDPRESS = "wordpress"
    FACEBOOK = "facebook"
    INSTAGRAM = "instagram"
    LINKEDIN = "linkedin"


class SocialPostStatus(StrEnum):
    """Lifecycle state of a social post."""

    DRAFT = "draft"
    SCHEDULED = "scheduled"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    PARTIAL = "partial"
    FAILED = "failed"


class PublishLogStatus(StrEnum):
    """Per-platform publish outcome."""

    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Social Account — encrypted platform credentials
# ---------------------------------------------------------------------------


class SocialAccount(db.Model):
    """A connected social-media or blog account.

    Credentials are stored encrypted with MultiFernet, following the same
    pattern as ``GmailToken``.

    Attributes:
        id: Primary key.
        platform: Target platform identifier.
        credentials_encrypted: Fernet-encrypted JSON blob with tokens / passwords.
        display_name: Human-readable label (page name, blog URL, etc.).
        is_active: Toggle without deleting the row.
        connected_by_id: Bureau member who configured this account.
        connected_at: When the account was connected.
        updated_at: Last credential refresh.
    """

    __tablename__ = "social_accounts"
    __auditable__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    credentials_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    connected_by_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    connected_by: Mapped[User] = relationship("User", foreign_keys=[connected_by_id])

    def __repr__(self) -> str:
        return f"<SocialAccount {self.platform} active={self.is_active}>"


# ---------------------------------------------------------------------------
# Social Post
# ---------------------------------------------------------------------------


class SocialPost(db.Model):
    """A post to be published across one or more social platforms.

    Attributes:
        id: Primary key.
        title: Post title (used as WordPress title / first line on social).
        body_html: Rich-text HTML content.
        body_text: Auto-generated plain-text fallback.
        status: Current lifecycle state.
        platforms: JSON list of target platform strings.
        scheduled_at: Optional deferred publish datetime.
        created_by_id: Bureau member who authored the post.
        instagram_format: Chosen IG aspect ratio (square / portrait / landscape).
    """

    __tablename__ = "social_posts"
    __auditable__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body_html: Mapped[str] = mapped_column(Text, nullable=False)
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=SocialPostStatus.DRAFT.value,
        index=True,
    )
    platforms: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    instagram_format: Mapped[str] = mapped_column(String(20), nullable=False, default="square")
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    created_by: Mapped[User] = relationship("User", foreign_keys=[created_by_id])
    images: Mapped[list[SocialPostImage]] = relationship(
        "SocialPostImage",
        back_populates="post",
        order_by="SocialPostImage.position",
        cascade="all, delete-orphan",
    )
    publish_logs: Mapped[list[SocialPublishLog]] = relationship(
        "SocialPublishLog",
        back_populates="post",
        order_by="SocialPublishLog.attempted_at.desc()",
        cascade="all, delete-orphan",
    )

    @property
    def is_editable(self) -> bool:
        """True if the post can still be edited."""
        return self.status in (
            SocialPostStatus.DRAFT.value,
            SocialPostStatus.SCHEDULED.value,
        )

    @property
    def featured_image(self) -> SocialPostImage | None:
        """Return the image marked as featured, or None."""
        return next((i for i in self.images if i.is_featured), None)

    def __repr__(self) -> str:
        return f"<SocialPost {self.title!r} status={self.status}>"


# ---------------------------------------------------------------------------
# Social Post Image — original + crop data
# ---------------------------------------------------------------------------


class SocialPostImage(db.Model):
    """An image attached to a social post.

    Stores the original file reference and per-platform crop coordinates.
    The actual cropped/resized files are in ``SocialPostProcessedImage``.

    Attributes:
        id: Primary key.
        post_id: FK to the parent SocialPost.
        document_id: FK to an existing Document (gallery pick), nullable.
        original_filename: Original upload filename.
        stored_filename: Server-side filename of the original image.
        position: Display order.
        is_featured: True if this is the WordPress featured image.
        crop_data: JSON dict mapping platform to crop coordinates.
    """

    __tablename__ = "social_post_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    post_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("social_posts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_featured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    crop_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    post: Mapped[SocialPost] = relationship("SocialPost", back_populates="images")
    document: Mapped[Document | None] = relationship("Document", foreign_keys=[document_id])
    processed_images: Mapped[list[SocialPostProcessedImage]] = relationship(
        "SocialPostProcessedImage",
        back_populates="source_image",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<SocialPostImage post={self.post_id} file={self.original_filename!r}>"


# ---------------------------------------------------------------------------
# Processed Image — per-platform cropped/resized variant
# ---------------------------------------------------------------------------


class SocialPostProcessedImage(db.Model):
    """A cropped and resized image variant for a specific platform.

    Generated server-side by the Celery image-processing task.

    Attributes:
        id: Primary key.
        post_image_id: FK to the source SocialPostImage.
        platform: Target platform this variant was generated for.
        stored_filename: Server-side filename of the processed file.
        width: Pixel width.
        height: Pixel height.
        size_bytes: File size.
        mime_type: Output MIME type (image/jpeg, image/webp, etc.).
    """

    __tablename__ = "social_post_processed_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    post_image_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("social_post_images.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(50), nullable=False)

    source_image: Mapped[SocialPostImage] = relationship(
        "SocialPostImage", back_populates="processed_images"
    )

    def __repr__(self) -> str:
        return f"<SocialPostProcessedImage {self.platform} {self.width}x{self.height}>"


# ---------------------------------------------------------------------------
# Publish Log — per-platform result
# ---------------------------------------------------------------------------


class SocialPublishLog(db.Model):
    """Records the result of publishing a post to a specific platform.

    Attributes:
        id: Primary key.
        post_id: FK to the SocialPost.
        platform: Target platform.
        status: Outcome (pending / success / failed).
        remote_id: Platform's post identifier.
        remote_url: Direct URL to the published content.
        error_message: Failure reason, if any.
        attempted_at: When the publish was attempted.
        retry_count: Number of retries so far.
    """

    __tablename__ = "social_publish_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    post_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("social_posts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=PublishLogStatus.PENDING.value,
    )
    remote_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    remote_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    post: Mapped[SocialPost] = relationship("SocialPost", back_populates="publish_logs")

    def __repr__(self) -> str:
        return f"<SocialPublishLog {self.platform} status={self.status}>"
