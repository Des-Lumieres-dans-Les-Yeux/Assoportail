"""Celery task — send a mailing campaign via Gmail API.

Workflow
--------
1. Load campaign; bail out if status is not ``draft`` or ``scheduled``.
2. Resolve recipients from ``campaign.recipients_filter`` and persist them.
3. Mark campaign as ``sending``.
4. For each pending recipient, build and send a Gmail message.
5. Mark each recipient ``sent`` or ``bounced`` and increment stats counters.
6. Mark campaign ``sent`` (or ``failed`` if every send failed).
"""

from __future__ import annotations

import base64
import logging
import time
from collections import defaultdict
from datetime import UTC, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import TYPE_CHECKING

from celery import shared_task

from app.tasks.utils import make_qr_img_tag as _make_qr_img_tag

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.models.mailing import MailingCampaign


@shared_task(
    name="tasks.send_campaign",
    bind=True,
    max_retries=2,
    time_limit=3600,
    soft_time_limit=3540,
)
def send_campaign(self, campaign_id: int) -> dict:
    """Send a mailing campaign.

    Args:
        campaign_id: Primary key of the MailingCampaign to send.

    Returns:
        Dict with ``sent`` and ``bounced`` counts, or ``skipped: True``.
    """
    from app.extensions import db
    from app.models.mailing import CampaignStatus, MailingCampaign

    campaign = db.session.get(MailingCampaign, campaign_id)
    if campaign is None:
        logger.error("Campaign %d not found", campaign_id)
        return {"skipped": True, "reason": "not_found"}
    if campaign.status not in (CampaignStatus.DRAFT.value, CampaignStatus.SCHEDULED.value):
        logger.info("Campaign %d already in status %r, skipping", campaign_id, campaign.status)
        return {"skipped": True, "reason": campaign.status}

    _resolve_recipients(campaign)
    campaign.status = CampaignStatus.SENDING.value
    db.session.commit()

    try:
        from app.services.gmail import GmailClient

        client = GmailClient.from_db()
    except RuntimeError as exc:
        logger.warning("Cannot send campaign %d — Gmail not configured: %s", campaign_id, exc)
        campaign = db.session.get(MailingCampaign, campaign_id)
        campaign.status = CampaignStatus.FAILED.value
        db.session.commit()
        return {"skipped": True, "reason": "gmail_not_configured"}

    from flask import current_app

    rate_limit = current_app.config.get("MAILING_RATE_LIMIT", 100)
    return _send_recipients(campaign_id, client, rate_limit=rate_limit)


def _resolve_recipients(campaign: MailingCampaign) -> None:
    """Resolve and persist MailingRecipient rows for a campaign.

    Existing recipients are cleared and re-resolved.
    Must be called inside an open transaction.

    Supports three audiences via ``recipients_filter["audience"]``:
    - ``"members"`` — portal users filtered by membership/role.
    - ``"center_contacts"`` — contacts of active centers that host at least one machine.
    - ``"both"`` — both of the above.

    Args:
        campaign: The campaign whose filter to apply.
    """
    from app.extensions import db
    from app.models.mailing import MailingRecipient, RecipientStatus
    from app.models.member import Membership
    from app.models.user import User, UserRole

    # Delete any stale recipients
    db.session.execute(
        db.delete(MailingRecipient).where(MailingRecipient.campaign_id == campaign.id)
    )

    f = campaign.recipients_filter or {}
    audience = f.get("audience", "members")

    seen_emails: set[str] = set()

    # ── Members ──────────────────────────────────────────────────────
    if audience in ("members", "both"):
        membership_status = f.get("membership_status", "active")
        role_filter = f.get("role", "all")

        stmt = db.select(User).where(User.is_active.is_(True))

        if role_filter == "bureau":
            stmt = stmt.where(User.role == UserRole.BUREAU.value)
        elif role_filter == "member":
            stmt = stmt.where(User.role == UserRole.MEMBER.value)

        if membership_status == "active":
            now = datetime.now(UTC)
            active_user_ids = db.session.scalars(
                db.select(Membership.user_id)
                .where(Membership.expires_at > now)
                .where(Membership.is_pending.is_(False))
            ).all()
            stmt = stmt.where(User.id.in_(active_user_ids))

        users = db.session.scalars(stmt).all()
        for user in users:
            email_lower = user.email.lower()
            if email_lower in seen_emails:
                continue
            seen_emails.add(email_lower)
            db.session.add(
                MailingRecipient(
                    campaign_id=campaign.id,
                    user_id=user.id,
                    email=user.email,
                    status=RecipientStatus.PENDING.value,
                )
            )

    # ── Tombola participants ──────────────────────────────────────────
    if audience == "tombola":
        from app.models.tombola import TombolaTicket

        tombola_id = f.get("tombola_id")
        if tombola_id:
            tickets = db.session.scalars(
                db.select(TombolaTicket)
                .where(
                    TombolaTicket.tombola_id == tombola_id,
                    TombolaTicket.ticket_number.isnot(None),
                    TombolaTicket.email != "[anonymisé]",
                )
                .order_by(TombolaTicket.email, TombolaTicket.ticket_number)
            ).all()

            for ticket in tickets:
                email_lower = ticket.email.lower()
                if email_lower in seen_emails:
                    continue
                seen_emails.add(email_lower)
                name = f"{ticket.first_name or ''} {ticket.last_name or ''}".strip() or None
                db.session.add(
                    MailingRecipient(
                        campaign_id=campaign.id,
                        recipient_name=name,
                        email=ticket.email,
                        status=RecipientStatus.PENDING.value,
                    )
                )

    # ── Center contacts (active centers with at least one machine) ───
    if audience in ("center_contacts", "both"):
        from app.models.center import Center, CenterContact, CenterStatus
        from app.models.machine import MachineInstallation

        # IDs of centers that have at least one active installation
        center_ids_with_machine = db.session.scalars(
            db.select(MachineInstallation.center_id)
            .where(MachineInstallation.removed_at.is_(None))
            .where(MachineInstallation.center_id.is_not(None))
            .distinct()
        ).all()

        if center_ids_with_machine:
            contacts = db.session.scalars(
                db.select(CenterContact)
                .join(CenterContact.center)
                .where(
                    Center.status == CenterStatus.ACTIVE.value,
                    Center.id.in_(center_ids_with_machine),
                    CenterContact.email.is_not(None),
                    CenterContact.email != "",
                )
            ).all()

            for contact in contacts:
                email_lower = contact.email.lower()
                if email_lower in seen_emails:
                    continue
                seen_emails.add(email_lower)
                db.session.add(
                    MailingRecipient(
                        campaign_id=campaign.id,
                        user_id=None,
                        center_id=contact.center.id,
                        recipient_name=f"{contact.name} ({contact.center.name})",
                        email=contact.email,
                        status=RecipientStatus.PENDING.value,
                    )
                )


def _send_recipients(campaign_id: int, client, *, rate_limit: int = 100) -> dict:
    """Send individual emails for all pending recipients.

    Args:
        campaign_id: The campaign to process.
        client: An authenticated GmailClient instance.
        rate_limit: Maximum emails per hour; controls inter-send delay.

    Returns:
        Dict with ``sent`` and ``bounced`` counts.
    """
    from app.extensions import db
    from app.models.mailing import (
        CampaignStatus,
        MailingCampaign,
        MailingRecipient,
        RecipientStatus,
    )

    sent = 0
    bounced = 0
    delay = 3600.0 / rate_limit if rate_limit > 0 else 0

    # 1. Fetch campaign details needed for all emails
    campaign = db.session.get(MailingCampaign, campaign_id)
    if not campaign:
        logger.error("Campaign %d not found in _send_recipients", campaign_id)
        return {"sent": 0, "bounced": 0}

    subject = campaign.subject
    body_html = campaign.body_html
    recipients_filter = campaign.recipients_filter or {}

    # Pre-fetch tombola numbers for all recipients in one query (avoids N+1).
    tombola_numbers_by_email: dict[str, str] = {}
    if recipients_filter.get("audience") == "tombola":
        tombola_id = recipients_filter.get("tombola_id")
        if tombola_id:
            from app.models.tombola import TombolaTicket

            rows = db.session.execute(
                db.select(TombolaTicket.email, TombolaTicket.ticket_number)
                .where(
                    TombolaTicket.tombola_id == tombola_id,
                    TombolaTicket.ticket_number.isnot(None),
                )
                .order_by(TombolaTicket.ticket_number)
            ).all()
            by_email: dict = defaultdict(list)
            for email, num in rows:
                by_email[email.lower()].append(f"{num:03d}")
            tombola_numbers_by_email = {e: ", ".join(nums) for e, nums in by_email.items()}

    # 2. Fetch IDs of pending recipients to process
    recipient_ids = db.session.scalars(
        db.select(MailingRecipient.id)
        .where(MailingRecipient.campaign_id == campaign_id)
        .where(MailingRecipient.status == RecipientStatus.PENDING.value)
    ).all()

    # Commit any implicit transaction from fetching metadata
    db.session.commit()

    for rid in recipient_ids:
        recipient = db.session.get(MailingRecipient, rid)
        if not recipient:
            continue

        try:
            recipient_subject = subject
            recipient_body = body_html

            if recipient.center:
                center = recipient.center
                import secrets

                dirty = False
                if not center.feedback_token:
                    center.feedback_token = secrets.token_urlsafe(32)
                    dirty = True
                if not center.breakdown_token:
                    center.breakdown_token = secrets.token_urlsafe(32)
                    dirty = True
                if dirty:
                    db.session.flush()

                from app.tasks.utils import public_url

                breakdown_url = public_url(
                    "machines.public_breakdown", token=center.breakdown_token
                )
                feedback_url = public_url("centers.submit_feedback", token=center.feedback_token)
            else:
                breakdown_url = ""
                feedback_url = ""

            for tag in ["{lien_panne}", "{{lien_panne}}", "[[lien_panne]]"]:
                recipient_subject = recipient_subject.replace(tag, breakdown_url)
                recipient_body = recipient_body.replace(tag, breakdown_url)
            for tag in ["{lien_livre_or}", "{{lien_livre_or}}", "[[lien_livre_or]]"]:
                recipient_subject = recipient_subject.replace(tag, feedback_url)
                recipient_body = recipient_body.replace(tag, feedback_url)

            # QR code tags — body only (no meaning in a subject line).
            # Degrade gracefully: if QR generation fails, replace with the raw URL.
            if "[[qr_panne]]" in recipient_body:
                try:
                    qr_tag = _make_qr_img_tag(breakdown_url) if breakdown_url else ""
                except Exception:
                    logger.exception("QR generation failed for [[qr_panne]]")
                    qr_tag = breakdown_url
                recipient_body = recipient_body.replace("[[qr_panne]]", qr_tag)
            if "[[qr_livre_or]]" in recipient_body:
                try:
                    qr_tag = _make_qr_img_tag(feedback_url) if feedback_url else ""
                except Exception:
                    logger.exception("QR generation failed for [[qr_livre_or]]")
                    qr_tag = feedback_url
                recipient_body = recipient_body.replace("[[qr_livre_or]]", qr_tag)

            # [[numeros_tombola]] — ticket numbers for this recipient (O(1) dict lookup).
            numeros = tombola_numbers_by_email.get(recipient.email.lower(), "")
            for tag in ["{numeros_tombola}", "{{numeros_tombola}}", "[[numeros_tombola]]"]:
                recipient_subject = recipient_subject.replace(tag, numeros)
                recipient_body = recipient_body.replace(tag, numeros)

            raw = _build_raw_message(
                to=recipient.email,
                subject=recipient_subject,
                body_html=recipient_body,
            )
            client.send_message(raw)
            recipient.status = RecipientStatus.SENT.value
            recipient.sent_at = datetime.now(UTC)
            sent += 1

        except Exception:
            logger.exception("Failed to send to %r for campaign %d", recipient.email, campaign_id)
            recipient.status = RecipientStatus.BOUNCED.value
            recipient.bounced_at = datetime.now(UTC)
            bounced += 1

        # Commit after each recipient to save progress and avoid long-running transactions
        db.session.commit()

        if delay > 0:
            time.sleep(delay)

    # 3. Final update for campaign status and stats
    campaign = db.session.get(MailingCampaign, campaign_id)
    if campaign:
        campaign.stats_sent = sent
        campaign.stats_bounced = bounced
        campaign.sent_at = datetime.now(UTC)
        campaign.status = CampaignStatus.SENT.value if sent > 0 else CampaignStatus.FAILED.value
        db.session.commit()

    return {"sent": sent, "bounced": bounced}


def _build_raw_message(to: str, subject: str, body_html: str) -> str:
    """Build a base64url-encoded RFC 2822 message for the Gmail API.

    Args:
        to: Recipient email address.
        subject: Email subject.
        body_html: HTML body content.

    Returns:
        Base64url-encoded string suitable for ``gmail.users.messages.send``.
    """
    msg = MIMEMultipart("alternative")
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body_html, "html", "utf-8"))
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()
