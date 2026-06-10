"""Add CERFA support: donor fields on transactions, association_config table.

Revision ID: 0002
Revises: 0001
Create Date: 2026-03-28
"""

from alembic import op
import sqlalchemy as sa


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Association configuration singleton
    op.create_table(
        "association_config",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False, server_default=""),
        sa.Column("address", sa.String(255), nullable=True),
        sa.Column("zip_code", sa.String(10), nullable=True),
        sa.Column("city", sa.String(100), nullable=True),
        sa.Column("siret", sa.String(14), nullable=True),
        sa.Column("rna", sa.String(10), nullable=True),
        sa.Column(
            "legal_form", sa.String(100), nullable=True, server_default="Association loi 1901"
        ),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("cgi_article", sa.String(20), nullable=False, server_default="200"),
        sa.Column("representative_name", sa.String(200), nullable=True),
        sa.Column("representative_title", sa.String(100), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # Donor fields on transactions
    op.add_column("transactions", sa.Column("donor_name", sa.String(200), nullable=True))
    op.add_column("transactions", sa.Column("donor_address", sa.String(255), nullable=True))
    op.add_column("transactions", sa.Column("donor_zip", sa.String(10), nullable=True))
    op.add_column("transactions", sa.Column("donor_city", sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_column("transactions", "donor_city")
    op.drop_column("transactions", "donor_zip")
    op.drop_column("transactions", "donor_address")
    op.drop_column("transactions", "donor_name")
    op.drop_table("association_config")
