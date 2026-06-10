"""Store CERFA DOCX templates as binary blobs in association_config.

Revision ID: 0019
Revises: 0018
Create Date: 2026-03-30
"""

from alembic import op
import sqlalchemy as sa

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "association_config", sa.Column("cerfa_tpl_particulier", sa.LargeBinary(), nullable=True)
    )
    op.add_column(
        "association_config", sa.Column("cerfa_tpl_entreprise", sa.LargeBinary(), nullable=True)
    )
    op.add_column(
        "association_config", sa.Column("cerfa_tpl_nature", sa.LargeBinary(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("association_config", "cerfa_tpl_nature")
    op.drop_column("association_config", "cerfa_tpl_entreprise")
    op.drop_column("association_config", "cerfa_tpl_particulier")
