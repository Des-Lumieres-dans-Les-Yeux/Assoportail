"""One-time password-reset tokens: store the hash of the current token.

Revision ID: 0041
Revises: 0040
Create Date: 2026-11-08

Each /forgot-password request records the SHA-256 of the minted token here;
resetting or requesting again invalidates every earlier link.
"""

from alembic import op
import sqlalchemy as sa

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("reset_token_hash", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "reset_token_hash")
