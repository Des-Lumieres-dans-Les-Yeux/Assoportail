"""Add event_dates table for non-consecutive multi-day events.

Revision ID: 0020
Revises: 0019
Create Date: 2026-03-31
"""

from alembic import op
import sqlalchemy as sa

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "event_dates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "day", name="uq_event_date_day"),
    )
    op.create_index("ix_event_dates_event_id", "event_dates", ["event_id"])


def downgrade() -> None:
    op.drop_index("ix_event_dates_event_id", table_name="event_dates")
    op.drop_table("event_dates")
