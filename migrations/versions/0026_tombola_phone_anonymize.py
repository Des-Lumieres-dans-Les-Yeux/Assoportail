"""Add phone to tombola_tickets and anonymized_at to tombolas.

Revision ID: 0026
Revises: 0025
"""

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("tombola_tickets", schema=None) as batch_op:
        batch_op.add_column(sa.Column("phone", sa.String(30), nullable=True))

    with op.batch_alter_table("tombolas", schema=None) as batch_op:
        batch_op.add_column(sa.Column("anonymized_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    with op.batch_alter_table("tombolas", schema=None) as batch_op:
        batch_op.drop_column("anonymized_at")

    with op.batch_alter_table("tombola_tickets", schema=None) as batch_op:
        batch_op.drop_column("phone")
