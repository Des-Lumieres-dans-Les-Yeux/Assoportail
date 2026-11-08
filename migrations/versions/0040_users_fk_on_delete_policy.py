"""ON DELETE policy for FKs into users: SET NULL for business rows.

Deleting a member (RGPD erasure) previously either failed outright on the 13
NO ACTION FKs (PII persisted with a 500) or silently destroyed business
records via ON DELETE CASCADE on events/meetings/tasks. Business rows must
survive the deletion: the author is forgotten (SET NULL), the record is kept.
Personal rows (memberships, attendee rows, tokens, ...) keep CASCADE.

Revision ID: 0040
Revises: 0039
Create Date: 2026-11-08
"""

import sqlalchemy as sa
from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None

# (table, column, original ondelete — None = NO ACTION, which blocked deletes)
SET_NULL_FKS = [
    ("documents", "uploaded_by_id", None),
    ("email_rules", "created_by_id", None),
    ("mailing_campaigns", "created_by_id", None),
    ("transactions", "created_by_id", None),
    ("cashboxes", "reconciled_by_id", None),
    ("event_machines", "added_by_id", None),
    ("expenses", "user_id", None),
    ("expenses", "validated_by_id", None),
    ("cash_entries", "recorded_by_id", None),
    ("social_accounts", "connected_by_id", None),
    ("social_posts", "created_by_id", None),
    ("polls", "created_by_id", None),
    ("poll_votes", "user_id", None),
    ("events", "created_by_id", "CASCADE"),
    ("meetings", "created_by_id", "CASCADE"),
    ("tasks", "created_by_id", "CASCADE"),
    ("task_comments", "author_id", "CASCADE"),
]


def _fk(table: str, column: str) -> str:
    """PostgreSQL's auto-generated name for the unnamed single-column FK."""
    return f"{table}_{column}_fkey"


def upgrade() -> None:
    for table, column, _ in SET_NULL_FKS:
        # SET NULL requires a nullable column (no-op if already nullable).
        op.execute(sa.text(f'ALTER TABLE "{table}" ALTER COLUMN "{column}" DROP NOT NULL'))
        op.execute(sa.text(f'ALTER TABLE "{table}" DROP CONSTRAINT "{_fk(table, column)}"'))
        op.execute(
            sa.text(
                f'ALTER TABLE "{table}" ADD CONSTRAINT "{_fk(table, column)}" '
                f"FOREIGN KEY ({column}) REFERENCES users(id) ON DELETE SET NULL"
            )
        )


def downgrade() -> None:
    # Recreates the original FK policies. Columns stay nullable: rows whose
    # author was deleted during the upgrade cannot re-assert NOT NULL.
    for table, column, original in SET_NULL_FKS:
        op.execute(sa.text(f'ALTER TABLE "{table}" DROP CONSTRAINT "{_fk(table, column)}"'))
        clause = f"FOREIGN KEY ({column}) REFERENCES users(id)"
        if original:
            clause += f" ON DELETE {original}"
        op.execute(sa.text(f'ALTER TABLE "{table}" ADD CONSTRAINT "{_fk(table, column)}" {clause}'))
