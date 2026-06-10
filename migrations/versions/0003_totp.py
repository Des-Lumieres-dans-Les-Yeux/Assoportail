"""Add TOTP 2FA support: totp_secret column on users table.

Revision ID: 0003
Revises: 0002
Create Date: 2026-03-28
"""

from alembic import op
import sqlalchemy as sa


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("totp_secret", sa.String(64), nullable=True, server_default=None),
    )


def downgrade() -> None:
    op.drop_column("users", "totp_secret")
