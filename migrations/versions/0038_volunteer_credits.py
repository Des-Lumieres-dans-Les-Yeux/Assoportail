"""Add volunteer_credits table.

Revision ID: 0038
Revises: 0037
Create Date: 2026-09-09

Time explicitly credited to a member — closing a task, or helping with a
machine installation/move — counted alongside event-slot hours in the
member list.
"""

import sqlalchemy as sa
from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "volunteer_credits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("hours", sa.Numeric(5, 2), nullable=False),
        sa.Column(
            "source",
            sa.Enum("task", "machine_installation", name="volunteer_credit_source"),
            nullable=False,
        ),
        sa.Column(
            "task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "machine_installation_id",
            sa.Integer(),
            sa.ForeignKey("machine_installations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_volunteer_credits_user_id", "volunteer_credits", ["user_id"])
    op.create_index("ix_volunteer_credits_task_id", "volunteer_credits", ["task_id"])
    op.create_index(
        "ix_volunteer_credits_machine_installation_id",
        "volunteer_credits",
        ["machine_installation_id"],
    )


def downgrade() -> None:
    op.drop_table("volunteer_credits")
    op.execute(sa.text("DROP TYPE IF EXISTS volunteer_credit_source"))
