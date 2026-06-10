"""Add winner_ticket_id to tombolas and tombola_documents junction table.

Revision ID: 0027
Revises: 0026
"""

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tombola_documents",
        sa.Column("tombola_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tombola_id"], ["tombolas.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tombola_id", "document_id"),
    )

    with op.batch_alter_table("tombolas", schema=None) as batch_op:
        batch_op.add_column(sa.Column("winner_ticket_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_tombolas_winner_ticket_id",
            "tombola_tickets",
            ["winner_ticket_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade():
    with op.batch_alter_table("tombolas", schema=None) as batch_op:
        batch_op.drop_constraint("fk_tombolas_winner_ticket_id", type_="foreignkey")
        batch_op.drop_column("winner_ticket_id")

    op.drop_table("tombola_documents")
