"""Add cerfa_drive_file_id and cerfa_drive_web_link to transactions table.

Revision ID: 0005
Revises: 0004
Create Date: 2026-03-28
"""

from alembic import op
import sqlalchemy as sa


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("cerfa_drive_file_id", sa.String(200), nullable=True))
    op.add_column("transactions", sa.Column("cerfa_drive_web_link", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("transactions", "cerfa_drive_web_link")
    op.drop_column("transactions", "cerfa_drive_file_id")
