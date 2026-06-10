"""Add km_rate column to association_config.

Revision ID: 0013
Revises: 0012
Create Date: 2026-03-29
"""

from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "association_config",
        sa.Column("km_rate", sa.Numeric(6, 3), nullable=True, server_default="0.603"),
    )


def downgrade() -> None:
    op.drop_column("association_config", "km_rate")
