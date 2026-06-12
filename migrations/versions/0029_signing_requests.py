"""Add signing_requests table for center document signature workflow.

Revision ID: 0029
Revises: 0028
Create Date: 2026-06-12
"""

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create the enum type idempotently — the EXCEPTION block handles the case
    # where a previous failed migration run already created the type.
    op.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE signing_status AS ENUM ('pending', 'completed', 'cancelled');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """))

    # Use sa.String for the status column so SQLAlchemy does not try to
    # (re-)create the enum type during the before_create table event.
    op.create_table(
        "signing_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("center_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=True),
        sa.Column("signed_document_id", sa.Integer(), nullable=True),
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_by_id", sa.Integer(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitter_name", sa.String(100), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["center_id"], ["centers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["signed_document_id"], ["documents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["sent_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token"),
    )

    # Cast the column to the proper enum type now that the table exists.
    op.execute(sa.text(
        "ALTER TABLE signing_requests "
        "ALTER COLUMN status TYPE signing_status "
        "USING status::signing_status"
    ))

    op.create_index("ix_signing_requests_center_id", "signing_requests", ["center_id"])
    op.create_index("ix_signing_requests_token", "signing_requests", ["token"])


def downgrade() -> None:
    op.drop_index("ix_signing_requests_token", "signing_requests")
    op.drop_index("ix_signing_requests_center_id", "signing_requests")
    op.drop_table("signing_requests")
    op.execute(sa.text("DROP TYPE IF EXISTS signing_status"))
