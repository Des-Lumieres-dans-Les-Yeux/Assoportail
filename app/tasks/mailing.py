"""Celery task — send a mailing campaign via Gmail API.

Workflow
--------
1. Load campaign. On the first run (``draft``/``scheduled``) resolve recipients
   and switch to ``sending``; a ``sending`` campaign is treated as a *resume*.
2. Send a small batch of pending recipients back-to-back (no blocking sleep),
   marking each ``sent`` or ``bounced``.
3. If recipients remain, reschedule the task with a ``countdown`` derived from
   the rate limit — the worker is never blocked, so the task can never hit its
   time limit mid-send and die silently.
4. Once no pending recipient remains, finalise the campaign: recompute stats
   from the recipients' real statuses and mark it ``sent`` (or ``failed``).
"""

from __future__ import annotations

import base64
import logging
from collections import defaultdict
from datetime import UTC, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import TYPE_CHECKING

from celery import shared_task

from app.tasks.utils import make_qr_img_tag as _make_qr_img_tag

logger = logging.getLogger(__name__)

# Namespace for the per-campaign PostgreSQL advisory lock (two-int form), so the
# campaign-id key can never collide with an advisory lock taken elsewhere.
_LOCK_NAMESPACE = 0x4D41  # "MA" (mailing)

if TYPE_CHECKING:
    from app.models.mailing import MailingCampaign


@shared_task(
    name="tasks.send_campaign",
    bind=True,
    max_retries=2,
    time_limit=600,
    soft_time_limit=540,
)
def send_campaign(self, campaign_id: int) -> dict:
    """Send (or resume sending) a mailing campaign, one batch per invocation.

    On the first run the campaign is in ``draft``/``scheduled``: recipients are
    resolved and the status flips to ``sending``. A ``sending`` campaign is a
    *resume* — recipients are not re-resolved. Each invocation sends at most
    ``MAILING_BATCH_SIZE`` emails then, if any remain, reschedules itself with a
    ``countdown`` so the rate limit is honoured without ever blocking the worker.

    Args:
        campaign_id: Primary key of the MailingCampaign to send.

    Returns:
        Dict with this batch's ``sent``/``bounced`` counts and ``remaining``
        pending recipients, or ``{"skipped": True, ...}``.
    """
    from app.extensions import db
    from app.models.mailing import CampaignStatus, MailingCampaign

    campaign = db.session.get(MailingCampaign, campaign_id)
    if campaign is None:
        logger.error("Campaign %d not found", campaign_id)
        return {"skipped": True, "reason": "not_found"}

    # Serialise per campaign across workers: only one instance may resolve/send a
    # given campaign at a time, otherwise two concurrent runs (a reschedule plus a
    # manual resume, with --concurrency=2) could fetch the same pending recipients
    # and send duplicates. A PostgreSQL session-level advisory lock on a dedicated
    # connection is released explicitly below, and automatically by the server if
    # this worker dies (the connection drops) — so it can never deadlock a resume.
    lock_conn = db.engine.connect()
    got_lock = lock_conn.exec_driver_sql(
        "SELECT pg_try_advisory_lock(%s, %s)", (_LOCK_NAMESPACE, campaign_id)
    ).scalar()
    if not got_lock:
        lock_conn.close()
        logger.info("Campaign %d already being sent by another worker — skipping", campaign_id)
        return {"skipped": True, "reason": "locked"}

    try:
        if campaign.status in (CampaignStatus.DRAFT.value, CampaignStatus.SCHEDULED.value):
            # First run — resolve the audience and commit to sending.
            _resolve_recipients(campaign)
            campaign.status = CampaignStatus.SENDING.value
            db.session.commit()
        elif campaign.status != CampaignStatus.SENDING.value:
            # sent / failed — nothing to do.
            logger.info("Campaign %d already in status %r, skipping", campaign_id, campaign.status)
            return {"skipped": True, "reason": campaign.status}

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
        # rate_limit == 0 means "no throttling" (tests / unlimited): send everything
        # in a single pass. Otherwise process a bounded batch and reschedule.
        batch_size = (
            current_app.config.get("MAILING_BATCH_SIZE", 10) if rate_limit > 0 else None
        )

        result = _send_recipients(campaign_id, client, batch_size=batch_size)
    finally:
        lock_conn.exec_driver_sql(
            "SELECT pg_advisory_unlock(%s, %s)", (_LOCK_NAMESPACE, campaign_id)
        )
        lock_conn.close()

    if result.get("remaining", 0) > 0:
        delay = 3600.0 / rate_limit if rate_limit > 0 else 0
        countdown = (batch_size or 0) * delay
        logger.info(
            "Campaign %d: batch sent=%d bounced=%d, %d remaining — rescheduling in %.0fs",
            campaign_id,
            result["sent"],
            result["bounced"],
            result["remaining"],
            countdown,
        )
        send_campaign.apply_async((campaign_id,), countdown=countdown)

    return result


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


def _send_recipients(campaign_id: int, client, *, batch_size: int | None = None) -> dict:
    """Send emails for up to ``batch_size`` pending recipients.

    When no pending recipient remains after this batch, the campaign is
    finalised: its stats are recomputed from the recipients' real statuses and
    it is marked ``sent`` (or ``failed`` if nothing was ever sent).

    Args:
        campaign_id: The campaign to process.
        client: An authenticated GmailClient instance.
        batch_size: Max recipients to process this call; ``None`` means all.

    Returns:
        Dict with this batch's ``sent``/``bounced`` counts and the number of
        still-``remaining`` pending recipients.
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

    # 1. Fetch campaign details needed for all emails
    campaign = db.session.get(MailingCampaign, campaign_id)
    if not campaign:
        logger.error("Campaign %d not found in _send_recipients", campaign_id)
        return {"sent": 0, "bounced": 0, "remaining": 0}

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

    # 2. Fetch IDs of pending recipients to process (bounded by batch_size).
    #    Ordered by id so successive batches advance deterministically.
    stmt = (
        db.select(MailingRecipient.id)
        .where(MailingRecipient.campaign_id == campaign_id)
        .where(MailingRecipient.status == RecipientStatus.PENDING.value)
        .order_by(MailingRecipient.id)
    )
    if batch_size:
        stmt = stmt.limit(batch_size)
    recipient_ids = db.session.scalars(stmt).all()

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

        # Commit after each recipient to save progress and avoid long-running
        # transactions. Throttling between sends is handled by the caller
        # rescheduling the next batch with a countdown — never by blocking here.
        db.session.commit()

    # 3. Count still-pending recipients. If any remain, the caller reschedules;
    #    we leave the campaign in ``sending``.
    remaining = (
        db.session.scalar(
            db.select(db.func.count(MailingRecipient.id))
            .where(MailingRecipient.campaign_id == campaign_id)
            .where(MailingRecipient.status == RecipientStatus.PENDING.value)
        )
        or 0
    )

    # 4. No pending left → finalise. Recompute stats from the recipients' real
    #    statuses so counts are correct across all the batches that ran.
    if remaining == 0:
        campaign = db.session.get(MailingCampaign, campaign_id)
        if campaign:
            total_sent = (
                db.session.scalar(
                    db.select(db.func.count(MailingRecipient.id))
                    .where(MailingRecipient.campaign_id == campaign_id)
                    .where(MailingRecipient.status == RecipientStatus.SENT.value)
                )
                or 0
            )
            total_bounced = (
                db.session.scalar(
                    db.select(db.func.count(MailingRecipient.id))
                    .where(MailingRecipient.campaign_id == campaign_id)
                    .where(MailingRecipient.status == RecipientStatus.BOUNCED.value)
                )
                or 0
            )
            campaign.stats_sent = total_sent
            campaign.stats_bounced = total_bounced
            campaign.sent_at = datetime.now(UTC)
            # FAILED only when there were recipients and every one bounced. An
            # empty audience (0 sent, 0 bounced) is a vacuous success, not a
            # failure.
            campaign.status = (
                CampaignStatus.FAILED.value
                if total_sent == 0 and total_bounced > 0
                else CampaignStatus.SENT.value
            )
            db.session.commit()

    return {"sent": sent, "bounced": bounced, "remaining": remaining}


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
