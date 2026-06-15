"""Add flipper_card_message to association_config.

Revision ID: 0037
Revises: 0036
Create Date: 2026-06-15

Stores the intro text printed on the QR cards placed inside the pinball
machines (above the breakdown + guestbook QR codes). Idempotent.
"""

import sqlalchemy as sa
from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "ALTER TABLE association_config "
            "ADD COLUMN IF NOT EXISTS flipper_card_message TEXT"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("ALTER TABLE association_config DROP COLUMN IF EXISTS flipper_card_message")
    )
