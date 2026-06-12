"""Add signing_requests table for center document signature workflow.

Revision ID: 0029
Revises: 0028
Create Date: 2026-06-12

This migration has been hardened to survive partial previous runs:
- The signing_status enum type may already exist (run 1 created it before failing).
- The signing_requests table may already exist as VARCHAR(20) (run 3 created it before failing on the ALTER).
Every step is guarded with IF NOT EXISTS / EXCEPTION blocks.
"""

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1 — Create enum type (idempotent)
    op.execute(
        sa.text("""
        DO $$ BEGIN
            CREATE TYPE signing_status AS ENUM ('pending', 'completed', 'cancelled');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """)
    )

    # 2 — Create table with a plain VARCHAR for status so SQLAlchemy never
    #     tries to (re-)create the enum type during the before_create event.
    #     IF NOT EXISTS handles the case where a previous run already created it.
    op.execute(
        sa.text("""
        CREATE TABLE IF NOT EXISTS signing_requests (
            id              SERIAL          NOT NULL,
            center_id       INTEGER         NOT NULL
                                REFERENCES centers(id) ON DELETE CASCADE,
            document_id     INTEGER
                                REFERENCES documents(id) ON DELETE SET NULL,
            signed_document_id INTEGER
                                REFERENCES documents(id) ON DELETE SET NULL,
            token           VARCHAR(64)     NOT NULL UNIQUE,
            status          VARCHAR(20)     NOT NULL DEFAULT 'pending',
            sent_at         TIMESTAMPTZ     NOT NULL,
            sent_by_id      INTEGER
                                REFERENCES users(id) ON DELETE SET NULL,
            submitted_at    TIMESTAMPTZ,
            submitter_name  VARCHAR(100),
            notes           TEXT,
            PRIMARY KEY (id)
        )
    """)
    )

    # 3 — Cast status column to the proper enum type.
    #     Guard with an information_schema check so re-runs are safe.
    op.execute(
        sa.text("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name  = 'signing_requests'
                  AND column_name = 'status'
                  AND data_type   = 'character varying'
            ) THEN
                ALTER TABLE signing_requests ALTER COLUMN status DROP DEFAULT;
                ALTER TABLE signing_requests
                    ALTER COLUMN status TYPE signing_status
                    USING status::signing_status;
                ALTER TABLE signing_requests
                    ALTER COLUMN status SET DEFAULT 'pending';
            END IF;
        END $$
    """)
    )

    # 4 — Indexes (IF NOT EXISTS → safe to re-run)
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_signing_requests_center_id "
            "ON signing_requests (center_id)"
        )
    )
    op.execute(
        sa.text("CREATE INDEX IF NOT EXISTS ix_signing_requests_token ON signing_requests (token)")
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_signing_requests_token"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_signing_requests_center_id"))
    op.execute(sa.text("DROP TABLE IF EXISTS signing_requests"))
    op.execute(sa.text("DROP TYPE IF EXISTS signing_status"))
