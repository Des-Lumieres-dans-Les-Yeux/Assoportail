"""Widen users.totp_secret to 256 chars for Fernet-encrypted values.

Revision ID: 0007
Revises: 0006
Create Date: 2026-03-28
"""

from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "users",
        "totp_secret",
        existing_type=sa.String(64),
        type_=sa.String(256),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "users",
        "totp_secret",
        existing_type=sa.String(256),
        type_=sa.String(64),
        existing_nullable=True,
    )
