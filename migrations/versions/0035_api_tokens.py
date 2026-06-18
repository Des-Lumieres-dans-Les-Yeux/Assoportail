"""Create api_tokens table for Bearer token authentication.

Revision ID: 0035
Revises: 0034
Create Date: 2026-06-14

Stores hashed API tokens tied to User accounts. Only the SHA-256 digest
and a short plaintext prefix are persisted — the full token is shown once
at creation time and never stored.
Idempotent (CREATE TABLE IF NOT EXISTS) so partial reruns are safe.
"""

import sqlalchemy as sa
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text("""
            CREATE TABLE IF NOT EXISTS api_tokens (
                id          SERIAL PRIMARY KEY,
                name        VARCHAR(100)  NOT NULL,
                token_prefix VARCHAR(16)  NOT NULL,
                token_hash  VARCHAR(64)   NOT NULL UNIQUE,
                user_id     INTEGER       NOT NULL
                    REFERENCES users(id) ON DELETE CASCADE,
                created_at  TIMESTAMP WITH TIME ZONE NOT NULL
                    DEFAULT NOW(),
                last_used_at TIMESTAMP WITH TIME ZONE,
                expires_at  TIMESTAMP WITH TIME ZONE,
                revoked     BOOLEAN NOT NULL DEFAULT FALSE
            )
        """)
    )
    op.execute(
        sa.text("CREATE INDEX IF NOT EXISTS ix_api_tokens_token_hash ON api_tokens (token_hash)")
    )
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_api_tokens_user_id ON api_tokens (user_id)"))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS api_tokens"))
