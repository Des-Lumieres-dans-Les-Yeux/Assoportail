"""Add member hosting, last_checked_at, and public breakdown token.

- machine_installations.hosted_by_id — FK to users (machine at a member's home)
- machine_installations.center_id — becomes nullable (null when hosted by member)
- machines.last_checked_at — operational check date
- centers.breakdown_token — public link for centers to report breakdowns

Revision ID: 0008
Revises: 0007
Create Date: 2026-03-29
"""

from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Machine: operational check date
    op.add_column("machines", sa.Column("last_checked_at", sa.Date(), nullable=True))

    # MachineInstallation: allow center_id to be NULL (member hosting)
    op.alter_column(
        "machine_installations",
        "center_id",
        existing_type=sa.Integer(),
        nullable=True,
    )

    # MachineInstallation: member who hosts the machine
    op.add_column(
        "machine_installations",
        sa.Column("hosted_by_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_machine_installations_hosted_by_id",
        "machine_installations",
        "users",
        ["hosted_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_machine_installations_hosted_by_id",
        "machine_installations",
        ["hosted_by_id"],
    )

    # Centers: public breakdown report token
    op.add_column(
        "centers",
        sa.Column("breakdown_token", sa.String(64), nullable=True, unique=True),
    )


def downgrade() -> None:
    op.drop_column("centers", "breakdown_token")
    op.drop_index("ix_machine_installations_hosted_by_id", table_name="machine_installations")
    op.drop_constraint(
        "fk_machine_installations_hosted_by_id",
        "machine_installations",
        type_="foreignkey",
    )
    op.drop_column("machine_installations", "hosted_by_id")
    op.alter_column(
        "machine_installations",
        "center_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.drop_column("machines", "last_checked_at")
