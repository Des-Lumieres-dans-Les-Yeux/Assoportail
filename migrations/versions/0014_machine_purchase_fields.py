"""Add purchase_date, purchase_price, estimated_value to machines.

Revision ID: 0014
Revises: 0013
Create Date: 2026-03-30
"""

from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("machines", sa.Column("purchase_date", sa.Date(), nullable=True))
    op.add_column("machines", sa.Column("purchase_price", sa.Numeric(10, 2), nullable=True))
    op.add_column("machines", sa.Column("estimated_value", sa.Numeric(10, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("machines", "estimated_value")
    op.drop_column("machines", "purchase_price")
    op.drop_column("machines", "purchase_date")
