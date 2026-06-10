"""Celery task — poll Gmail inbox and import new messages.

Scheduled via Celery beat every ``GMAIL_POLL_INTERVAL`` seconds.
Skipped gracefully if the Gmail token is missing or invalid.
"""

from __future__ import annotations

import base64
import email as email_lib
import logging
from datetime import UTC, datetime

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    name="tasks.poll_gmail_inbox",
    bind=True,
    max_retries=3,
    time_limit=300,
    soft_time_limit=270,
)
def poll_gmail_inbox(self) -> dict:
    """Fetch new emails from Gmail and apply email rules.

    Returns:
        Dict with ``imported`` (count of new emails) and ``processed`` (count
        of emails that matched at least one rule).
    """
    from app.extensions import db
    from app.models.email import InboundEmail
    from app.services.gmail import GmailClient
    from app.tasks.email_rules import apply_rules_to_email

    try:
        client = GmailClient.from_db()
    except RuntimeError as exc:
        logger.warning("Gmail polling skipped: %s", exc)
        return {"imported": 0, "processed": 0, "skipped": True}

    # Fetch all currently unread Gmail IDs and cross-check with the DB to
    # import only the ones we haven't seen yet.  Using list_all_unread_ids()
    # instead of a timestamp filter avoids missing old unread messages that
    # pre-date the most recently imported email.
    unread_ids = client.list_all_unread_ids()

    already_imported = {
        row[0]
        for row in db.session.execute(
            db.select(InboundEmail.gmail_message_id).where(
                InboundEmail.gmail_message_id.in_(unread_ids)
            )
        ).all()
    }

    messages = [{"id": mid} for mid in unread_ids if mid not in already_imported]

    imported = 0
    processed = 0

    for meta in messages:
        msg_id = meta["id"]

        try:
            raw = client.get_message(msg_id)
        except Exception:
            logger.exception("Failed to fetch Gmail message %s", msg_id)
            continue

        inbound = _parse_gmail_message(raw)
        if inbound is None:
            continue

        db.session.add(inbound)
        db.session.flush()
        imported += 1

        logs = apply_rules_to_email(inbound)
        if logs:
            processed += 1

    return {"imported": imported, "processed": processed}


def _parse_gmail_message(raw: dict):  # -> InboundEmail | None
    """Convert a raw Gmail API message resource to an InboundEmail instance.

    Args:
        raw: Full Gmail API message resource (``format="full"``).

    Returns:
        An unsaved InboundEmail, or None if parsing fails.
    """
    from app.models.email import InboundEmail

    try:
        headers = {h["name"].lower(): h["value"] for h in raw["payload"]["headers"]}
        subject = headers.get("subject", "")
        sender = headers.get("from", "")
        recipients = headers.get("to", "")
        date_str = headers.get("date", "")

        try:
            received_at = email_lib.utils.parsedate_to_datetime(date_str)
            if received_at.tzinfo is None:
                received_at = received_at.replace(tzinfo=UTC)
        except Exception:
            received_at = datetime.now(UTC)

        body_text = _extract_body(raw["payload"], "text/plain")
        body_html = _extract_body(raw["payload"], "text/html")

        return InboundEmail(
            gmail_message_id=raw["id"],
            subject=subject,
            sender=sender,
            recipients=recipients,
            body_text=body_text,
            body_html=body_html,
            received_at=received_at,
        )
    except Exception:
        logger.exception("Failed to parse Gmail message %s", raw.get("id"))
        return None


def _extract_body(payload: dict, mime_type: str) -> str | None:
    """Recursively extract the body part matching a MIME type.

    Args:
        payload: Gmail API message payload.
        mime_type: Target MIME type (e.g. ``"text/plain"``).

    Returns:
        Decoded body string or None.
    """
    if payload.get("mimeType") == mime_type:
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
        return None

    for part in payload.get("parts", []):
        result = _extract_body(part, mime_type)
        if result:
            return result

    return None
