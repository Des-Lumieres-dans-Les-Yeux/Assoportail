"""Squashed schema — complete baseline.

Revision ID: 0001
Revises:
Create Date: 2026-03-28

Combines:
  8a8fdb2d3b74 — Initial schema
  a1b2c3d4e5f6 — Center contacts
  b2c3d4e5f6a7 — Google credentials and drive columns
  c3d4e5f6a7b8 — Improvements v2 (maintenance lifecycle, event machines, permissions, feedback token)
  d4e5f6a7b8c9 — Event slots and member availability
  0002         — Convention document on centers, comment on slot_availabilities
"""

from alembic import op
import sqlalchemy as sa


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Enum types are created automatically by SQLAlchemy when op.create_table
    # processes the first sa.Enum() column that references each named type.

    # ------------------------------------------------------------------ #
    # Tables with no foreign-key dependencies                             #
    # ------------------------------------------------------------------ #

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entity_type", sa.String(length=100), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column(
            "action", sa.Enum("create", "update", "delete", name="auditaction"), nullable=False
        ),
        sa.Column("changes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_entity_id", "audit_logs", ["entity_id"])
    op.create_index("ix_audit_logs_entity_type", "audit_logs", ["entity_type"])
    op.create_index("ix_audit_logs_timestamp", "audit_logs", ["timestamp"])
    op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"])

    # centers — convention_document_id FK added after documents table exists (use_alter)
    op.create_table(
        "centers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("address", sa.String(length=255), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=False),
        sa.Column("zip_code", sa.String(length=10), nullable=False),
        sa.Column(
            "status",
            sa.Enum("prospect", "active", "inactive", "lost", name="center_status"),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("convention_document_id", sa.Integer(), nullable=True),
        sa.Column("feedback_token", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("feedback_token", name="uq_centers_feedback_token"),
    )

    op.create_table(
        "gmail_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token_encrypted", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "google_app_credentials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("credentials_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "tags",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=50), nullable=False),
        sa.Column("color", sa.String(length=7), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("label"),
    )

    # users — includes permissions (JSON)
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("password_hash", sa.String(length=128), nullable=False),
        sa.Column("first_name", sa.String(length=100), nullable=False),
        sa.Column("last_name", sa.String(length=100), nullable=False),
        sa.Column("role", sa.Enum("member", "bureau", name="user_role"), nullable=False),
        sa.Column(
            "gender",
            sa.Enum("male", "female", "other", "not_specified", name="user_gender"),
            nullable=False,
        ),
        sa.Column("phone", sa.String(length=30), nullable=True),
        sa.Column("address", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("must_change_password", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("permissions", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # machines — includes internal_number
    op.create_table(
        "machines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("manufacturer", sa.String(length=100), nullable=False),
        sa.Column("serial_number", sa.String(length=100), nullable=True),
        sa.Column("internal_number", sa.String(length=20), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("stock", "installed", "maintenance", "retired", name="machine_status"),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("serial_number"),
        sa.UniqueConstraint("internal_number", name="uq_machines_internal_number"),
    )

    # ------------------------------------------------------------------ #
    # Tables depending only on centers / users / machines                 #
    # ------------------------------------------------------------------ #

    op.create_table(
        "center_contacts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("center_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("role", sa.String(length=100), nullable=True),
        sa.Column("email", sa.String(length=254), nullable=True),
        sa.Column("phone", sa.String(length=30), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["center_id"], ["centers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_center_contacts_center_id", "center_contacts", ["center_id"])

    op.create_table(
        "center_feedbacks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("center_id", sa.Integer(), nullable=False),
        sa.Column("submitted_by", sa.String(length=100), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("rating", sa.SmallInteger(), nullable=True),
        sa.Column("is_published", sa.Boolean(), nullable=False),
        sa.Column("published_by_id", sa.Integer(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["center_id"], ["centers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["published_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_center_feedbacks_center_id", "center_feedbacks", ["center_id"])

    # documents — includes drive columns
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("stored_filename", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("uploaded_by_id", sa.Integer(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("drive_file_id", sa.String(length=200), nullable=True),
        sa.Column("drive_web_link", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(["uploaded_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stored_filename"),
    )
    op.create_index("ix_documents_uploaded_by_id", "documents", ["uploaded_by_id"])

    # FK from centers.convention_document_id → documents.id (added after documents exists)
    op.create_foreign_key(
        "fk_centers_convention_document_id",
        "centers",
        "documents",
        ["convention_document_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "email_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("match_mode", sa.String(length=10), nullable=False),
        sa.Column("conditions", sa.JSON(), nullable=False),
        sa.Column("actions", sa.JSON(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_email_rules_created_by_id", "email_rules", ["created_by_id"])

    # events — includes end_date
    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("event_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("location", sa.String(length=200), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_events_created_by_id", "events", ["created_by_id"])

    # inbound_emails — FK to tasks uses use_alter (circular dep resolved after tasks)
    op.create_table(
        "inbound_emails",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("gmail_message_id", sa.String(length=200), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("sender", sa.String(length=500), nullable=False),
        sa.Column("recipients", sa.Text(), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("body_html", sa.Text(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("processed", sa.Boolean(), nullable=False),
        sa.Column("generated_task_id", sa.Integer(), nullable=True),
        sa.Column("event_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["generated_task_id"],
            ["tasks.id"],
            ondelete="SET NULL",
            use_alter=True,
            name="fk_inbound_emails_generated_task_id",
        ),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_inbound_emails_gmail_message_id", "inbound_emails", ["gmail_message_id"], unique=True
    )
    op.create_index("ix_inbound_emails_event_id", "inbound_emails", ["event_id"])

    op.create_table(
        "machine_installations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("machine_id", sa.Integer(), nullable=False),
        sa.Column("center_id", sa.Integer(), nullable=False),
        sa.Column("installed_at", sa.Date(), nullable=False),
        sa.Column("removed_at", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["center_id"], ["centers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["machine_id"], ["machines.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_machine_installations_center_id", "machine_installations", ["center_id"])
    op.create_index("ix_machine_installations_machine_id", "machine_installations", ["machine_id"])
    op.create_index("ix_machine_installations_removed_at", "machine_installations", ["removed_at"])
    op.create_index(
        "uq_machine_active_installation",
        "machine_installations",
        ["machine_id"],
        unique=True,
        postgresql_where="removed_at IS NULL",
    )

    op.create_table(
        "mailing_campaigns",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recipients_filter", sa.JSON(), nullable=False),
        sa.Column("stats_sent", sa.Integer(), nullable=False),
        sa.Column("stats_bounced", sa.Integer(), nullable=False),
        sa.Column("stats_opened", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_mailing_campaigns_created_by_id", "mailing_campaigns", ["created_by_id"])
    op.create_index("ix_mailing_campaigns_status", "mailing_campaigns", ["status"])

    op.create_table(
        "meetings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("location", sa.String(length=200), nullable=True),
        sa.Column("minutes", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_meetings_created_by_id", "meetings", ["created_by_id"])

    op.create_table(
        "memberships",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.Enum("helloasso", "cash", name="membership_source"), nullable=False),
        sa.Column("amount", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("started_at", sa.Date(), nullable=False),
        sa.Column("expires_at", sa.Date(), nullable=False),
        sa.Column("renewed_at", sa.Date(), nullable=True),
        sa.Column("helloasso_order_id", sa.String(length=100), nullable=True),
        sa.Column("is_pending", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("helloasso_order_id"),
    )
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"])

    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=10), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_transactions_created_by_id", "transactions", ["created_by_id"])

    # ------------------------------------------------------------------ #
    # Tables depending on events / documents / meetings                   #
    # ------------------------------------------------------------------ #

    op.create_table(
        "cashboxes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opening_amount", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("closing_amount", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("reconciled_by_id", sa.Integer(), nullable=True),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reconciliation_note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reconciled_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_cashbox_event"),
    )
    op.create_index("ix_cashboxes_event_id", "cashboxes", ["event_id"])

    op.create_table(
        "center_documents",
        sa.Column("center_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["center_id"], ["centers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("center_id", "document_id"),
    )

    op.create_table(
        "center_feedback_documents",
        sa.Column("center_feedback_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["center_feedback_id"], ["center_feedbacks.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("center_feedback_id", "document_id"),
    )

    op.create_table(
        "email_rule_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=False),
        sa.Column("email_id", sa.Integer(), nullable=False),
        sa.Column("actions_triggered", sa.JSON(), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["email_id"], ["inbound_emails.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["rule_id"], ["email_rules.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_email_rule_logs_email_id", "email_rule_logs", ["email_id"])
    op.create_index("ix_email_rule_logs_rule_id", "email_rule_logs", ["rule_id"])

    op.create_table(
        "event_attendees",
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("event_id", "user_id"),
    )

    op.create_table(
        "event_documents",
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("event_id", "document_id"),
    )

    # event_machines — includes comment, added_by_id, added_at
    op.create_table(
        "event_machines",
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("machine_id", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("added_by_id", sa.Integer(), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["machine_id"], ["machines.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["added_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("event_id", "machine_id"),
    )
    op.create_index("ix_event_machines_added_by_id", "event_machines", ["added_by_id"])

    op.create_table(
        "event_slots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("slot_date", sa.Date(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=True),
        sa.Column("end_time", sa.Time(), nullable=True),
        sa.Column("label", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_event_slots_event_id", "event_slots", ["event_id"])

    op.create_table(
        "expenses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("amount", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("validated_by_id", sa.Integer(), nullable=True),
        sa.Column("receipt_document_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["receipt_document_id"], ["documents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["validated_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_expenses_event_id", "expenses", ["event_id"])
    op.create_index("ix_expenses_user_id", "expenses", ["user_id"])

    op.create_table(
        "inbound_email_attachments",
        sa.Column("inbound_email_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["inbound_email_id"], ["inbound_emails.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("inbound_email_id", "document_id"),
    )

    op.create_table(
        "machine_documents",
        sa.Column("machine_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["machine_id"], ["machines.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("machine_id", "document_id"),
    )

    op.create_table(
        "mailing_recipients",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("campaign_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bounced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bounce_type", sa.String(length=10), nullable=True),
        sa.ForeignKeyConstraint(["campaign_id"], ["mailing_campaigns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_mailing_recipients_campaign_id", "mailing_recipients", ["campaign_id"])
    op.create_index("ix_mailing_recipients_user_id", "mailing_recipients", ["user_id"])

    op.create_table(
        "meeting_attendees",
        sa.Column("meeting_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("meeting_id", "user_id"),
    )

    op.create_table(
        "meeting_documents",
        sa.Column("meeting_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("meeting_id", "document_id"),
    )

    op.create_table(
        "transaction_tags",
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("transaction_id", "tag_id"),
    )

    # ------------------------------------------------------------------ #
    # tasks — depends on users, centers, inbound_emails, meetings, events #
    # ------------------------------------------------------------------ #

    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("open", "in_progress", "done", "cancelled", name="task_status"),
            nullable=False,
        ),
        sa.Column(
            "priority",
            sa.Enum("low", "normal", "high", "urgent", name="task_priority"),
            nullable=False,
        ),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("assigned_to_id", sa.Integer(), nullable=True),
        sa.Column(
            "source",
            sa.Enum("manual", "email", "meeting", "center_breakdown", "event", name="task_source"),
            nullable=False,
        ),
        sa.Column("source_email_id", sa.Integer(), nullable=True),
        sa.Column("source_meeting_id", sa.Integer(), nullable=True),
        sa.Column("source_center_id", sa.Integer(), nullable=True),
        sa.Column("source_event_id", sa.Integer(), nullable=True),
        sa.Column("machine_id", sa.Integer(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["assigned_to_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["machine_id"], ["machines.id"], ondelete="SET NULL", name="fk_tasks_machine_id"
        ),
        sa.ForeignKeyConstraint(["source_center_id"], ["centers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_email_id"], ["inbound_emails.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["source_event_id"], ["events.id"], ondelete="SET NULL", name="fk_tasks_source_event_id"
        ),
        sa.ForeignKeyConstraint(["source_meeting_id"], ["meetings.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tasks_assigned_to_id", "tasks", ["assigned_to_id"])
    op.create_index("ix_tasks_created_by_id", "tasks", ["created_by_id"])
    op.create_index("ix_tasks_machine_id", "tasks", ["machine_id"])
    op.create_index("ix_tasks_source_center_id", "tasks", ["source_center_id"])
    op.create_index("ix_tasks_source_event_id", "tasks", ["source_event_id"])

    # ------------------------------------------------------------------ #
    # Tables depending on tasks                                           #
    # ------------------------------------------------------------------ #

    op.create_table(
        "cash_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("cashbox_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("amount", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("recorded_by_id", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["cashbox_id"], ["cashboxes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recorded_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cash_entries_cashbox_id", "cash_entries", ["cashbox_id"])

    op.create_table(
        "expense_documents",
        sa.Column("expense_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["expense_id"], ["expenses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("expense_id", "document_id"),
    )

    # maintenance_records — includes status, resolved_*, center_id
    op.create_table(
        "maintenance_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("machine_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("cost", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("maintainer_name", sa.String(length=100), nullable=False),
        sa.Column("maintainer_user_id", sa.Integer(), nullable=True),
        sa.Column("source_task_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Enum("open", "resolved", name="maintenance_status"), nullable=False),
        sa.Column("resolved_at", sa.Date(), nullable=True),
        sa.Column("resolved_by_id", sa.Integer(), nullable=True),
        sa.Column("center_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["center_id"],
            ["centers.id"],
            ondelete="SET NULL",
            name="fk_maintenance_records_center_id",
        ),
        sa.ForeignKeyConstraint(["machine_id"], ["machines.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["maintainer_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["resolved_by_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_maintenance_records_resolved_by_id",
        ),
        sa.ForeignKeyConstraint(["source_task_id"], ["tasks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_maintenance_records_center_id", "maintenance_records", ["center_id"])
    op.create_index("ix_maintenance_records_machine_id", "maintenance_records", ["machine_id"])
    op.create_index(
        "ix_maintenance_records_maintainer_user_id", "maintenance_records", ["maintainer_user_id"]
    )
    op.create_index(
        "ix_maintenance_records_resolved_by_id", "maintenance_records", ["resolved_by_id"]
    )
    op.create_index(
        "ix_maintenance_records_source_task_id", "maintenance_records", ["source_task_id"]
    )

    op.create_table(
        "meeting_tasks",
        sa.Column("meeting_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("meeting_id", "task_id"),
    )

    # slot_availabilities — includes comment
    op.create_table(
        "slot_availabilities",
        sa.Column("slot_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("present", "absent", "maybe", name="slot_availability_status"),
            nullable=False,
        ),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["slot_id"], ["event_slots.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("slot_id", "user_id"),
    )

    op.create_table(
        "task_comments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_task_comments_author_id", "task_comments", ["author_id"])
    op.create_index("ix_task_comments_task_id", "task_comments", ["task_id"])

    op.create_table(
        "task_documents",
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("task_id", "document_id"),
    )

    op.create_table(
        "maintenance_documents",
        sa.Column("maintenance_record_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["maintenance_record_id"], ["maintenance_records.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("maintenance_record_id", "document_id"),
    )


def downgrade():
    # Leaf tables first, then walk up the dependency tree
    op.drop_table("maintenance_documents")
    op.drop_table("task_documents")
    op.drop_index("ix_task_comments_task_id", table_name="task_comments")
    op.drop_index("ix_task_comments_author_id", table_name="task_comments")
    op.drop_table("task_comments")
    op.drop_table("slot_availabilities")
    op.drop_table("meeting_tasks")
    op.drop_index("ix_maintenance_records_source_task_id", table_name="maintenance_records")
    op.drop_index("ix_maintenance_records_resolved_by_id", table_name="maintenance_records")
    op.drop_index("ix_maintenance_records_maintainer_user_id", table_name="maintenance_records")
    op.drop_index("ix_maintenance_records_machine_id", table_name="maintenance_records")
    op.drop_index("ix_maintenance_records_center_id", table_name="maintenance_records")
    op.drop_table("maintenance_records")
    op.drop_table("expense_documents")
    op.drop_index("ix_cash_entries_cashbox_id", table_name="cash_entries")
    op.drop_table("cash_entries")
    op.drop_index("ix_tasks_source_event_id", table_name="tasks")
    op.drop_index("ix_tasks_source_center_id", table_name="tasks")
    op.drop_index("ix_tasks_machine_id", table_name="tasks")
    op.drop_index("ix_tasks_created_by_id", table_name="tasks")
    op.drop_index("ix_tasks_assigned_to_id", table_name="tasks")
    op.drop_table("tasks")
    op.drop_table("transaction_tags")
    op.drop_table("meeting_documents")
    op.drop_table("meeting_attendees")
    op.drop_index("ix_mailing_recipients_user_id", table_name="mailing_recipients")
    op.drop_index("ix_mailing_recipients_campaign_id", table_name="mailing_recipients")
    op.drop_table("mailing_recipients")
    op.drop_table("machine_documents")
    op.drop_table("inbound_email_attachments")
    op.drop_table("expenses")
    op.drop_index("ix_event_slots_event_id", table_name="event_slots")
    op.drop_table("event_slots")
    op.drop_index("ix_event_machines_added_by_id", table_name="event_machines")
    op.drop_table("event_machines")
    op.drop_table("event_documents")
    op.drop_table("event_attendees")
    op.drop_index("ix_email_rule_logs_rule_id", table_name="email_rule_logs")
    op.drop_index("ix_email_rule_logs_email_id", table_name="email_rule_logs")
    op.drop_table("email_rule_logs")
    op.drop_table("center_feedback_documents")
    op.drop_table("center_documents")
    op.drop_index("ix_cashboxes_event_id", table_name="cashboxes")
    op.drop_table("cashboxes")
    op.drop_table("transactions")
    op.drop_index("ix_memberships_user_id", table_name="memberships")
    op.drop_table("memberships")
    op.drop_index("ix_meetings_created_by_id", table_name="meetings")
    op.drop_table("meetings")
    op.drop_index("ix_mailing_campaigns_status", table_name="mailing_campaigns")
    op.drop_index("ix_mailing_campaigns_created_by_id", table_name="mailing_campaigns")
    op.drop_table("mailing_campaigns")
    op.drop_index(
        "uq_machine_active_installation",
        table_name="machine_installations",
        postgresql_where="removed_at IS NULL",
    )
    op.drop_index("ix_machine_installations_removed_at", table_name="machine_installations")
    op.drop_index("ix_machine_installations_machine_id", table_name="machine_installations")
    op.drop_index("ix_machine_installations_center_id", table_name="machine_installations")
    op.drop_table("machine_installations")
    op.drop_index("ix_inbound_emails_event_id", table_name="inbound_emails")
    op.drop_index("ix_inbound_emails_gmail_message_id", table_name="inbound_emails")
    op.drop_table("inbound_emails")
    op.drop_index("ix_events_created_by_id", table_name="events")
    op.drop_table("events")
    op.drop_index("ix_email_rules_created_by_id", table_name="email_rules")
    op.drop_table("email_rules")
    # Drop FK before dropping documents (centers references it)
    op.drop_constraint("fk_centers_convention_document_id", "centers", type_="foreignkey")
    op.drop_index("ix_documents_uploaded_by_id", table_name="documents")
    op.drop_table("documents")
    op.drop_index("ix_center_feedbacks_center_id", table_name="center_feedbacks")
    op.drop_table("center_feedbacks")
    op.drop_index("ix_center_contacts_center_id", table_name="center_contacts")
    op.drop_table("center_contacts")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
    op.drop_table("tags")
    op.drop_table("machines")
    op.drop_table("google_app_credentials")
    op.drop_table("gmail_tokens")
    op.drop_table("centers")
    op.drop_index("ix_audit_logs_user_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_timestamp", table_name="audit_logs")
    op.drop_index("ix_audit_logs_entity_type", table_name="audit_logs")
    op.drop_index("ix_audit_logs_entity_id", table_name="audit_logs")
    op.drop_table("audit_logs")

    # Drop custom enum types
    op.execute("DROP TYPE IF EXISTS slot_availability_status")
    op.execute("DROP TYPE IF EXISTS maintenance_status")
    op.execute("DROP TYPE IF EXISTS task_source")
    op.execute("DROP TYPE IF EXISTS task_priority")
    op.execute("DROP TYPE IF EXISTS task_status")
    op.execute("DROP TYPE IF EXISTS membership_source")
    op.execute("DROP TYPE IF EXISTS user_gender")
    op.execute("DROP TYPE IF EXISTS user_role")
    op.execute("DROP TYPE IF EXISTS machine_status")
    op.execute("DROP TYPE IF EXISTS center_status")
    op.execute("DROP TYPE IF EXISTS auditaction")
