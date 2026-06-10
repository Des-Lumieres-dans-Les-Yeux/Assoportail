"""Allow mailing to center contacts (non-portal users).

- mailing_recipients.user_id becomes nullable
- mailing_recipients.recipient_name added for display when user_id is null

Revision ID: 0009
Revises: 0008
Create Date: 2026-03-29
"""

from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "mailing_recipients",
        "user_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.add_column(
        "mailing_recipients",
        sa.Column("recipient_name", sa.String(200), nullable=True),
    )


def downgrade() -> None:
    # Remove rows without a user before making user_id NOT NULL again
    op.execute("DELETE FROM mailing_recipients WHERE user_id IS NULL")
    op.drop_column("mailing_recipients", "recipient_name")
    op.alter_column(
        "mailing_recipients",
        "user_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
