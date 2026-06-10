"""Add resolution_comment column to maintenance_records.

Revision ID: 0024
Revises: 0023
"""

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "maintenance_records",
        sa.Column("resolution_comment", sa.Text(), nullable=True),
    )


def downgrade():
    op.drop_column("maintenance_records", "resolution_comment")
