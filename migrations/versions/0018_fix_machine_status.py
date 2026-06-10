"""Fix machine status: set INSTALLED for machines with active installations.

Machines that have an installation record without removed_at should be INSTALLED,
not STOCK. This corrects historical data inconsistencies.

Revision ID: 0018
Revises: 0017
Create Date: 2026-03-30
"""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE machines
        SET status = 'installed'
        WHERE status = 'stock'
          AND id IN (
            SELECT DISTINCT machine_id
            FROM machine_installations
            WHERE removed_at IS NULL
          )
        """
    )


def downgrade() -> None:
    pass
