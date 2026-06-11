"""Add 'to_install' value to center_status enum.

Revision ID: 0028
Revises: 0027
Create Date: 2026-06-11

Note: ALTER TYPE ... ADD VALUE cannot run inside a transaction block on
PostgreSQL. This migration uses autocommit mode to work around that constraint.
Downgrade is intentionally a no-op: PostgreSQL does not support DROP VALUE from
an enum — if a rollback is needed, UPDATE centers SET status='prospect'
WHERE status='to_install' before reverting the application code.
"""

import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ALTER TYPE ADD VALUE cannot run inside a transaction block on PostgreSQL.
    # Issuing COMMIT first closes the implicit transaction Alembic opened,
    # allowing the DDL to execute without the isolation_level workaround.
    op.execute(sa.text("COMMIT"))
    op.execute(
        sa.text("ALTER TYPE center_status ADD VALUE IF NOT EXISTS 'to_install' AFTER 'prospect'")
    )


def downgrade() -> None:
    # PostgreSQL does not support removing enum values natively.
    # Remap any 'to_install' rows to 'prospect' before rolling back code.
    pass
