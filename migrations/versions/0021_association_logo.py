"""Add logo column to association_config.

Revision ID: 0021
Revises: 0020
"""

from alembic import op
import sqlalchemy as sa

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("association_config", sa.Column("logo", sa.LargeBinary(), nullable=True))


def downgrade():
    op.drop_column("association_config", "logo")
