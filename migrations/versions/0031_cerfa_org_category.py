"""Add cerfa_org_category to association_config.

Revision ID: 0031
Revises: 0030
Create Date: 2026-06-12

Stores which "Cochez la case concernée" organism category to tick on page 1 of
the CERFA 11580*04 form. Defaults to 'oeuvre' (Oeuvre ou organisme d'intérêt
général). Idempotent ADD COLUMN IF NOT EXISTS so partial reruns are safe.
"""

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "ALTER TABLE association_config "
            "ADD COLUMN IF NOT EXISTS cerfa_org_category VARCHAR(40) NOT NULL DEFAULT 'oeuvre'"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE association_config DROP COLUMN IF EXISTS cerfa_org_category"))
