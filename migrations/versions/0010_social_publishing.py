"""Social publishing module — accounts, posts, images, logs.

Revision ID: 0010
Revises: 0009
Create Date: 2026-03-29
"""

from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "social_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("platform", sa.String(20), nullable=False, unique=True),
        sa.Column("credentials_encrypted", sa.Text(), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("connected_by_id", sa.Integer(), nullable=False),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["connected_by_id"], ["users.id"]),
    )
    op.create_index("ix_social_accounts_connected_by_id", "social_accounts", ["connected_by_id"])

    op.create_table(
        "social_posts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("platforms", sa.JSON(), nullable=False),
        sa.Column("instagram_format", sa.String(20), nullable=False, server_default="square"),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
    )
    op.create_index("ix_social_posts_status", "social_posts", ["status"])
    op.create_index("ix_social_posts_created_by_id", "social_posts", ["created_by_id"])

    op.create_table(
        "social_post_images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("post_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=True),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("stored_filename", sa.String(255), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_featured", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("crop_data", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["post_id"], ["social_posts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_social_post_images_post_id", "social_post_images", ["post_id"])
    op.create_index("ix_social_post_images_document_id", "social_post_images", ["document_id"])

    op.create_table(
        "social_post_processed_images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("post_image_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("stored_filename", sa.String(255), nullable=False, unique=True),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("mime_type", sa.String(50), nullable=False),
        sa.ForeignKeyConstraint(["post_image_id"], ["social_post_images.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_social_post_processed_images_post_image_id",
        "social_post_processed_images",
        ["post_image_id"],
    )

    op.create_table(
        "social_publish_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("post_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("remote_id", sa.String(500), nullable=True),
        sa.Column("remote_url", sa.String(1000), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["post_id"], ["social_posts.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_social_publish_logs_post_id", "social_publish_logs", ["post_id"])


def downgrade() -> None:
    op.drop_table("social_publish_logs")
    op.drop_table("social_post_processed_images")
    op.drop_table("social_post_images")
    op.drop_table("social_posts")
    op.drop_table("social_accounts")
