"""Add email, personal_token, confirmed to event_volunteers.

Revision ID: 0017
Revises: 0016
Create Date: 2026-03-30
"""

from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("event_volunteers", sa.Column("email", sa.String(254), nullable=True))
    op.add_column("event_volunteers", sa.Column("personal_token", sa.String(64), nullable=True))
    op.add_column(
        "event_volunteers",
        sa.Column("confirmed", sa.Boolean(), nullable=True, server_default="false"),
    )

    # Backfill existing rows with a generated token
    op.execute(
        "UPDATE event_volunteers SET email = 'unknown@placeholder', "
        "personal_token = 'legacy_' || id::text, confirmed = true "
        "WHERE personal_token IS NULL"
    )

    op.alter_column("event_volunteers", "email", nullable=False)
    op.alter_column("event_volunteers", "personal_token", nullable=False)
    op.alter_column("event_volunteers", "confirmed", nullable=False)
    op.create_unique_constraint(
        "uq_volunteer_personal_token", "event_volunteers", ["personal_token"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_volunteer_personal_token", "event_volunteers", type_="unique")
    op.drop_column("event_volunteers", "confirmed")
    op.drop_column("event_volunteers", "personal_token")
    op.drop_column("event_volunteers", "email")
