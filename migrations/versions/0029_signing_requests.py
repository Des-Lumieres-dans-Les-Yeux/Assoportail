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

signing_status = sa.Enum("pending", "completed", "cancelled", name="signing_status")


def upgrade() -> None:
    signing_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "signing_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("center_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=True),
        sa.Column("signed_document_id", sa.Integer(), nullable=True),
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column("status", signing_status, nullable=False, server_default="pending"),
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
    op.create_index("ix_signing_requests_center_id", "signing_requests", ["center_id"])
    op.create_index("ix_signing_requests_token", "signing_requests", ["token"])


def downgrade() -> None:
    op.drop_index("ix_signing_requests_token", "signing_requests")
    op.drop_index("ix_signing_requests_center_id", "signing_requests")
    op.drop_table("signing_requests")
    signing_status.drop(op.get_bind(), checkfirst=True)
