"""Add permanent blog_link to centers.

Revision ID: 0043
Revises: 0042
Create Date: 2026-11-08

MachineInstallation.web_link is tied to an active installation: it disappears
from the vitrine map when the machine is moved or removed. Centers that were
hosted historically (or whose blog post concerns the center itself, not a
specific installation) need a permanent link.
"""

from alembic import op
import sqlalchemy as sa

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("centers", sa.Column("blog_link", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("centers", "blog_link")
