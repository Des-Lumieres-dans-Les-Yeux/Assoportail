"""Remint enumerable legacy volunteer personal tokens.

Revision ID: 0042
Revises: 0041
Create Date: 2026-11-08

Migration 0017 backfilled pre-existing event_volunteers rows with
deterministic credentials ('legacy_<id>') that are sequentially guessable.
Replace every such token with a random 256-bit value. The old volunteer
confirm/portal links die: the bureau must re-send the links
(events → "Générer le lien bénévoles" / re-registration) for the affected
events. Idempotent: a second run matches no row.
"""

from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE event_volunteers "
        "SET personal_token = encode(gen_random_bytes(32), 'hex') "
        r"WHERE personal_token LIKE 'legacy\_%' ESCAPE '\' "
    )


def downgrade() -> None:
    # Original random values are gone; nothing to restore.
    pass
