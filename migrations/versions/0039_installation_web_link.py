"""Add web_link to machine_installations.

Revision ID: 0039
Revises: 0038
Create Date: 2026-11-08

Optional public link (e.g. blog post) about an installation, shown in the
Leaflet popup on the centers map. Idempotent ADD COLUMN IF NOT EXISTS so
partial reruns are safe.
"""

import sqlalchemy as sa
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("ALTER TABLE machine_installations ADD COLUMN IF NOT EXISTS web_link VARCHAR(500)"))


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE machine_installations DROP COLUMN IF EXISTS web_link"))
