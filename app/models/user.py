"""User model — authentication, roles, and profile data."""

from __future__ import annotations

import enum
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import bcrypt
from flask_login import UserMixin
from sqlalchemy import JSON, Boolean, DateTime, Enum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.models.member import Membership


class UserPermission(enum.StrEnum):
    """Granular permissions for portal modules."""

    MACHINES = "machines"
    EVENTS = "events"
    CENTERS = "centers"
    TASKS = "tasks"
    MEETINGS = "meetings"
    DOCUMENTS = "documents"
    MEMBERS = "members"
    TREASURY = "treasury"
    MAILBOX = "mailbox"
    MAILING = "mailing"
    SOCIAL = "social"
    POLLS = "polls"


BUREAU_DEFAULT_PERMISSIONS: frozenset[str] = frozenset(p.value for p in UserPermission)
_MEMBER_EXCLUDED = {UserPermission.TREASURY, UserPermission.MEMBERS, UserPermission.MAILING}
MEMBER_DEFAULT_PERMISSIONS: frozenset[str] = frozenset(
    p.value for p in UserPermission if p not in _MEMBER_EXCLUDED
)


class UserRole(enum.StrEnum):
    """User access role within the portal."""

    MEMBER = "member"
    BUREAU = "bureau"


class UserGender(enum.StrEnum):
    """User gender identity options."""

    MALE = "male"
    FEMALE = "female"
    OTHER = "other"
    NOT_SPECIFIED = "not_specified"


class User(UserMixin, db.Model):
    """An association member who can authenticate to the portal.

    Attributes:
        id: Primary key.
        email: Unique login email, stored lowercase.
        password_hash: Bcrypt hash of the user's password. Never stored in plain text.
        first_name: User's given name.
        last_name: User's family name.
        role: Access level — ``member`` or ``bureau``.
        gender: Self-reported gender identity.
        phone: Optional contact phone number.
        address: Optional postal address.
        is_active: False for suspended or deleted accounts.
        must_change_password: True until the user sets a personal password
            (e.g. after account creation).
        created_at: UTC timestamp of account creation.
        updated_at: UTC timestamp of last modification.
    """

    __tablename__ = "users"
    __auditable__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=UserRole.MEMBER,
    )
    gender: Mapped[UserGender] = mapped_column(
        Enum(UserGender, name="user_gender", values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=UserGender.NOT_SPECIFIED,
    )
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    totp_secret_encrypted: Mapped[str | None] = mapped_column(
        "totp_secret", String(256), nullable=True, default=None
    )
    permissions: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
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
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    memberships: Mapped[list[Membership]] = relationship(
        "Membership",
        back_populates="user",
        order_by="Membership.created_at.desc()",
        cascade="all, delete-orphan",
    )

    # ------------------------------------------------------------------
    # TOTP secret — encrypted at rest with Fernet
    # ------------------------------------------------------------------

    @property
    def totp_secret(self) -> str | None:
        """Decrypt and return the TOTP secret, or None."""
        raw = self.totp_secret_encrypted
        if not raw:
            return None
        try:
            from app.services.gmail import decrypt_token

            data = decrypt_token(raw)
            return data.get("totp")
        except Exception:
            # Backwards compat: if stored unencrypted (pre-migration), return as-is
            if len(raw) <= 64 and raw.isalnum():
                return raw
            logger.warning("Failed to decrypt TOTP secret for user %s", self.email)
            return None

    @totp_secret.setter
    def totp_secret(self, value: str | None) -> None:
        """Encrypt and store the TOTP secret, or clear it."""
        if value is None:
            self.totp_secret_encrypted = None
            return
        try:
            from app.services.gmail import encrypt_token

            self.totp_secret_encrypted = encrypt_token({"totp": value})
        except Exception:
            logger.warning("ENCRYPTION_KEYS not configured; storing TOTP secret as-is")
            self.totp_secret_encrypted = value

    # ------------------------------------------------------------------
    # Password management
    # ------------------------------------------------------------------

    def set_password(self, password: str) -> None:
        """Hash and store a plaintext password using bcrypt.

        Args:
            password: The plaintext password to hash.
        """
        self.password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode(
            "utf-8"
        )

    def check_password(self, password: str) -> bool:
        """Verify a plaintext password against the stored bcrypt hash.

        Args:
            password: The plaintext password to verify.

        Returns:
            True if the password is correct, False otherwise.
        """
        return bcrypt.checkpw(password.encode("utf-8"), self.password_hash.encode("utf-8"))

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def full_name(self) -> str:
        """User's full display name (first + last)."""
        return f"{self.first_name} {self.last_name}"

    @property
    def is_bureau(self) -> bool:
        """True if the user holds bureau-level access."""
        return self.role == UserRole.BUREAU

    def has_permission(self, permission: str | UserPermission) -> bool:
        """Check if the user has a specific granular permission.

        Bureau members have all permissions by default.
        Active members have only explicitly granted permissions.

        Args:
            permission: The permission name or UserPermission member.

        Returns:
            True if access is granted, False otherwise.
        """
        if self.is_bureau:
            return True
        perm_val = permission.value if isinstance(permission, UserPermission) else permission
        return perm_val in (self.permissions or [])

    def __repr__(self) -> str:
        return f"<User {self.email} role={self.role.value}>"
