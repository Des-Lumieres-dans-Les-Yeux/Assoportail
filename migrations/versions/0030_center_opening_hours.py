"""Add opening_hours to centers and installation_requests.

Revision ID: 0030
Revises: 0029
Create Date: 2026-06-12

Free-text opening days/hours, set on the public installation request form and
on the center record. Idempotent ADD COLUMN IF NOT EXISTS so partial reruns are
safe.
"""

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("ALTER TABLE centers ADD COLUMN IF NOT EXISTS opening_hours TEXT"))
    op.execute(
        sa.text("ALTER TABLE installation_requests ADD COLUMN IF NOT EXISTS opening_hours TEXT")
    )


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE installation_requests DROP COLUMN IF EXISTS opening_hours"))
    op.execute(sa.text("ALTER TABLE centers DROP COLUMN IF EXISTS opening_hours"))
