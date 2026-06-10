"""Add donation_type, donor_first_name, donor_email, donor_description, cerfa_sent_at to transactions.

Revision ID: 0004
Revises: 0003
Create Date: 2026-03-28
"""

from alembic import op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("donation_type", sa.String(20), nullable=True))
    op.add_column("transactions", sa.Column("donor_first_name", sa.String(100), nullable=True))
    op.add_column("transactions", sa.Column("donor_email", sa.String(255), nullable=True))
    op.add_column("transactions", sa.Column("donor_description", sa.Text(), nullable=True))
    op.add_column(
        "transactions",
        sa.Column("cerfa_sent_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transactions", "cerfa_sent_at")
    op.drop_column("transactions", "donor_description")
    op.drop_column("transactions", "donor_email")
    op.drop_column("transactions", "donor_first_name")
    op.drop_column("transactions", "donation_type")
