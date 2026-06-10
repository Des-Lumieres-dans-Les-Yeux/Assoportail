"""Reset incorrectly marked processed emails.

Emails that were marked processed=True by the buggy auto-sync but have
no category, no linked task, and no linked event should be reset to False.

Revision ID: 0015
Revises: 0014
Create Date: 2026-03-30
"""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE inbound_emails
        SET processed = false
        WHERE processed = true
          AND category IS NULL
          AND generated_task_id IS NULL
          AND event_id IS NULL
        """
    )


def downgrade() -> None:
    pass
