"""Add case-insensitive unique index on users.email.

The existing unique index is case-sensitive in PostgreSQL, so
``Test@example.com`` and ``test@example.com`` could coexist.  This
migration replaces it with a functional index on ``LOWER(email)``.

Revision ID: 0006
Revises: 0005
Create Date: 2026-03-28
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_users_email", table_name="users")
    op.execute("CREATE UNIQUE INDEX ix_users_email_lower ON users (LOWER(email))")


def downgrade() -> None:
    op.execute("DROP INDEX ix_users_email_lower")
    op.create_index("ix_users_email", "users", ["email"], unique=True)
