"""Add sequential receipt number and generation timestamp to transactions.

Revision ID: 0033
Revises: 0032
Create Date: 2026-06-12

``cerfa_number`` holds the per-year sequential receipt number "DON-AAAA-NNNNN"
(assigned on first generation, unique, never reused). ``cerfa_generated_at``
marks when the receipt was first issued/archived — independent of email.
Idempotent so partial reruns are safe.
"""

import sqlalchemy as sa
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS cerfa_number VARCHAR(20)")
    )
    op.execute(
        sa.text(
            "ALTER TABLE transactions "
            "ADD COLUMN IF NOT EXISTS cerfa_generated_at TIMESTAMP WITH TIME ZONE"
        )
    )
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_transactions_cerfa_number "
            "ON transactions (cerfa_number)"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS uq_transactions_cerfa_number"))
    op.execute(sa.text("ALTER TABLE transactions DROP COLUMN IF EXISTS cerfa_generated_at"))
    op.execute(sa.text("ALTER TABLE transactions DROP COLUMN IF EXISTS cerfa_number"))
