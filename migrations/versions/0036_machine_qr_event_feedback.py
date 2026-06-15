"""Machine QR codes and event (center-less) guestbook entries.

Revision ID: 0036
Revises: 0035
Create Date: 2026-06-15

Adds ``machines.public_token`` — a permanent secret used by the breakdown and
guestbook QR codes stuck physically on a machine. The token encodes only the
machine, never the center, so the printed sticker survives the machine moving
between centers (the current center is resolved at scan time).

Also relaxes ``center_feedbacks.center_id`` to nullable and adds
``center_feedbacks.machine_id`` so a testimonial left via a machine QR while the
machine is not installed anywhere (e.g. during an event) can still be stored.

Idempotent: uses ADD COLUMN IF NOT EXISTS / conditional NOT NULL drop so partial
reruns are safe. Existing machines are backfilled with a random token.
"""

import sqlalchemy as sa
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- machines.public_token --------------------------------------------
    op.execute(
        sa.text("ALTER TABLE machines ADD COLUMN IF NOT EXISTS public_token VARCHAR(64)")
    )
    # Backfill existing rows with a unique random token (32 hex chars).
    # md5()/random() are available on every PostgreSQL version; mixing in the row
    # id guarantees uniqueness even if random() collides.
    op.execute(
        sa.text(
            "UPDATE machines SET public_token = "
            "md5(random()::text || clock_timestamp()::text || id::text) "
            "WHERE public_token IS NULL"
        )
    )
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_machines_public_token "
            "ON machines (public_token)"
        )
    )

    # --- center_feedbacks.center_id -> nullable ---------------------------
    op.execute(sa.text("ALTER TABLE center_feedbacks ALTER COLUMN center_id DROP NOT NULL"))

    # --- center_feedbacks.machine_id --------------------------------------
    op.execute(
        sa.text("ALTER TABLE center_feedbacks ADD COLUMN IF NOT EXISTS machine_id INTEGER")
    )
    op.execute(
        sa.text(
            "DO $$ BEGIN "
            "IF NOT EXISTS (SELECT 1 FROM pg_constraint "
            "WHERE conname = 'fk_center_feedbacks_machine_id') THEN "
            "ALTER TABLE center_feedbacks ADD CONSTRAINT fk_center_feedbacks_machine_id "
            "FOREIGN KEY (machine_id) REFERENCES machines(id) ON DELETE SET NULL; "
            "END IF; END $$;"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_center_feedbacks_machine_id "
            "ON center_feedbacks (machine_id)"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_center_feedbacks_machine_id"))
    op.execute(
        sa.text(
            "ALTER TABLE center_feedbacks DROP CONSTRAINT IF EXISTS "
            "fk_center_feedbacks_machine_id"
        )
    )
    op.execute(sa.text("ALTER TABLE center_feedbacks DROP COLUMN IF EXISTS machine_id"))
    # NOTE: center_id is left nullable on downgrade — re-adding NOT NULL would
    # fail if any event (center-less) entries exist.

    op.execute(sa.text("DROP INDEX IF EXISTS uq_machines_public_token"))
    op.execute(sa.text("ALTER TABLE machines DROP COLUMN IF EXISTS public_token"))
