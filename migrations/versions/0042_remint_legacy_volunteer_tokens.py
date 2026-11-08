"""Remint enumerable legacy volunteer personal tokens.

Revision ID: 0042
Revises: 0041
Create Date: 2026-11-08

Migration 0017 backfilled pre-existing event_volunteers rows with
deterministic credentials ('legacy_<id>') that are sequentially guessable.
Replace every such token with a random value (same secrets.token_urlsafe
convention as every other app token). The old volunteer confirm/portal
links die: the bureau must re-send the links (events → "Générer le lien
bénévoles" / re-registration) for the affected events. Idempotent: a
second run matches no row.
"""

import secrets

from alembic import op
import sqlalchemy as sa

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Tokens are minted in Python, not with pgcrypto's gen_random_bytes:
    # that extension is not guaranteed to exist on target databases.
    bind = op.get_bind()
    legacy_ids = [
        row[0]
        for row in bind.execute(
            sa.text(
                r"SELECT id FROM event_volunteers "
                r"WHERE personal_token LIKE 'legacy\_%' ESCAPE '\'"
            )
        )
    ]
    for volunteer_id in legacy_ids:
        bind.execute(
            sa.text("UPDATE event_volunteers SET personal_token = :token WHERE id = :id"),
            {"token": secrets.token_urlsafe(32), "id": volunteer_id},
        )


def downgrade() -> None:
    # Original random values are gone; nothing to restore.
    pass
