"""Add volunteer access: token on events, volunteer + slot tables.

Revision ID: 0016
Revises: 0015
Create Date: 2026-03-30
"""

from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent: clean up partial previous run if needed
    op.execute("DROP TABLE IF EXISTS volunteer_slot_availabilities CASCADE")
    op.execute("DROP TABLE IF EXISTS event_volunteers CASCADE")
    op.execute("ALTER TABLE events DROP COLUMN IF EXISTS volunteer_token")

    # 1. Add volunteer_token to events
    op.add_column("events", sa.Column("volunteer_token", sa.String(64), nullable=True))
    op.create_unique_constraint("uq_events_volunteer_token", "events", ["volunteer_token"])

    # 2. Create event_volunteers table
    op.create_table(
        "event_volunteers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "event_id",
            sa.Integer,
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 3. Create volunteer_slot_availabilities with VARCHAR, then cast to existing enum
    op.create_table(
        "volunteer_slot_availabilities",
        sa.Column(
            "slot_id",
            sa.Integer,
            sa.ForeignKey("event_slots.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "volunteer_id",
            sa.Integer,
            sa.ForeignKey("event_volunteers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(
        "ALTER TABLE volunteer_slot_availabilities "
        "ALTER COLUMN status TYPE slot_availability_status "
        "USING status::slot_availability_status"
    )


def downgrade() -> None:
    op.drop_table("volunteer_slot_availabilities")
    op.drop_table("event_volunteers")
    op.drop_constraint("uq_events_volunteer_token", "events", type_="unique")
    op.drop_column("events", "volunteer_token")
