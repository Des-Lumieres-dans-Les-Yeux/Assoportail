"""Add signature image to association_config.

Revision ID: 0032
Revises: 0031
Create Date: 2026-06-12

Optional signature image (transparent PNG recommended) stamped on the
generated CERFA receipts. Idempotent ADD COLUMN IF NOT EXISTS.
"""

import sqlalchemy as sa
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("ALTER TABLE association_config ADD COLUMN IF NOT EXISTS signature BYTEA"))


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE association_config DROP COLUMN IF EXISTS signature"))
