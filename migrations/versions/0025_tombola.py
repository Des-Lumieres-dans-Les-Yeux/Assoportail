"""Add tombolas and tombola_tickets tables.

Revision ID: 0025
Revises: 5356e317757e
"""

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "5356e317757e"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tombolas",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(32), nullable=True),
        sa.Column("draw_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("range_min", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("range_max", sa.Integer(), nullable=False, server_default="999"),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tombolas_slug", "tombolas", ["slug"], unique=True)
    op.create_index("ix_tombolas_created_by_id", "tombolas", ["created_by_id"])

    op.create_table(
        "tombola_tickets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tombola_id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("last_name", sa.String(100), nullable=True),
        sa.Column("first_name", sa.String(100), nullable=True),
        sa.Column("ticket_number", sa.Integer(), nullable=True),
        sa.Column("order_ref", sa.String(100), nullable=True),
        sa.ForeignKeyConstraint(["tombola_id"], ["tombolas.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tombola_tickets_tombola_id", "tombola_tickets", ["tombola_id"])
    op.create_index("ix_tombola_tickets_email", "tombola_tickets", ["email"])


def downgrade():
    op.drop_index("ix_tombola_tickets_email", table_name="tombola_tickets")
    op.drop_index("ix_tombola_tickets_tombola_id", table_name="tombola_tickets")
    op.drop_table("tombola_tickets")
    op.drop_index("ix_tombolas_created_by_id", table_name="tombolas")
    op.drop_index("ix_tombolas_slug", table_name="tombolas")
    op.drop_table("tombolas")
