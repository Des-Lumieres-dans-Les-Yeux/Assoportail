"""API token model — Bearer token authentication for the REST API."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db

if TYPE_CHECKING:
    from app.models.user import User


class ApiToken(db.Model):
    """A long-lived Bearer token tied to a User account.

    Only the SHA-256 hash of the token is stored; the plaintext is shown
    once at creation time (via ``flask api-token``) and never persisted.

    Attributes:
        id: Primary key.
        name: Human-readable label (e.g. "Automation GitHub Actions").
        token_prefix: First 12 characters of the plaintext token — used to
            identify the token in a UI without revealing the secret.
        token_hash: SHA-256 hex digest of the full plaintext token (unique).
        user_id: FK to the User who owns this token.
        created_at: UTC creation timestamp.
        last_used_at: UTC timestamp of the most recent authenticated request.
        expires_at: Optional expiry datetime; None means never expires.
        revoked: When True the token is permanently rejected.
    """

    __tablename__ = "api_tokens"
    __auditable__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    user: Mapped[User] = relationship("User", foreign_keys=[user_id])

    # ------------------------------------------------------------------
    # Class-level helpers
    # ------------------------------------------------------------------

    @classmethod
    def generate(
        cls, name: str, user_id: int, expires_at: datetime | None = None
    ) -> tuple[str, ApiToken]:
        """Generate a new token and return ``(plaintext, unsaved_instance)``.

        The returned instance is **not** added to the session; the caller
        must do ``db.session.add(token); db.session.commit()``.

        Args:
            name: Human-readable label for this token.
            user_id: ID of the owner User.
            expires_at: Optional expiry datetime (UTC-aware).

        Returns:
            Tuple of (plaintext_token, ApiToken_instance).
        """
        plaintext = "dldly_" + secrets.token_urlsafe(32)
        token_hash = cls.hash_token(plaintext)
        prefix = plaintext[:12]
        instance = cls(
            name=name,
            token_prefix=prefix,
            token_hash=token_hash,
            user_id=user_id,
            expires_at=expires_at,
        )
        return plaintext, instance

    @staticmethod
    def hash_token(plaintext: str) -> str:
        """Return the SHA-256 hex digest of *plaintext*.

        Args:
            plaintext: The full plaintext token string.

        Returns:
            64-character lowercase hex string.
        """
        return hashlib.sha256(plaintext.encode()).hexdigest()

    # ------------------------------------------------------------------
    # Instance helpers
    # ------------------------------------------------------------------

    @property
    def is_valid(self) -> bool:
        """True if the token is neither revoked nor expired."""
        if self.revoked:
            return False
        if self.expires_at is not None and datetime.now(UTC) >= self.expires_at:
            return False
        return True

    def __repr__(self) -> str:
        return f"<ApiToken {self.token_prefix!r} user={self.user_id} valid={self.is_valid}>"
