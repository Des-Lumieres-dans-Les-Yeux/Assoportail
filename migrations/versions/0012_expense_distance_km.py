"""Add distance_km column to expenses for travel reimbursement.

Revision ID: 0012
Revises: 0011
Create Date: 2026-03-29
"""

from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("expenses", sa.Column("distance_km", sa.Numeric(10, 1), nullable=True))


def downgrade() -> None:
    op.drop_column("expenses", "distance_km")
