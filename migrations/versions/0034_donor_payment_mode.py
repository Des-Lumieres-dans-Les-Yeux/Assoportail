"""Add donor payment mode (mode de versement) to transactions.

Revision ID: 0034
Revises: 0033
Create Date: 2026-06-14

``donor_payment_mode`` records how a cash donation was paid — "especes",
"cheque" or "virement" (virement/prélèvement/carte bancaire) — so the matching
"mode de versement" checkbox can be ticked on the CERFA 11580*04 form.
Idempotent so partial reruns are safe.
"""

import sqlalchemy as sa
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS donor_payment_mode VARCHAR(20)")
    )


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE transactions DROP COLUMN IF EXISTS donor_payment_mode"))
