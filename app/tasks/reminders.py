"""Celery periodic tasks — membership expiry reminders and event J-3 notifications."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from app.tasks import celery

logger = logging.getLogger(__name__)


@celery.task(name="tasks.check_membership_expiry", time_limit=300, soft_time_limit=270)
def check_membership_expiry() -> None:
    """Send reminder emails to members whose membership expires in 30 or 7 days.

    Runs daily. Targets the exact day-delta so each member receives at most
    one email per threshold (30-day notice and 7-day notice).
    """
    from flask import url_for

    from app.extensions import db
    from app.models.member import Membership
    from app.models.user import User
    from app.services.mailer import send_membership_expiry_email

    today = date.today()
    thresholds = [30, 7]

    for days in thresholds:
        target_date = today + timedelta(days=days)
        memberships = db.session.scalars(
            db.select(Membership)
            .join(Membership.user)
            .where(
                Membership.expires_at == target_date,
                Membership.is_pending.is_(False),
            )
        ).all()

        for membership in memberships:
            user: User = membership.user
            if not user.email:
                continue
            try:
                portal_url = url_for("members.list_members", _external=True)
                send_membership_expiry_email(
                    to_email=user.email,
                    full_name=user.full_name,
                    expires_at=membership.expires_at,
                    days_remaining=days,
                    portal_url=portal_url,
                )
                logger.info(
                    "Membership expiry reminder sent to %s (expires %s, %d days)",
                    user.email,
                    membership.expires_at,
                    days,
                )
            except Exception:
                logger.exception("Failed to send expiry reminder to %s", user.email)


@celery.task(name="tasks.send_event_reminders", time_limit=300, soft_time_limit=270)
def send_event_reminders() -> None:
    """Send J-3 reminder emails to attendees of events happening in exactly 3 days.

    Runs daily. Only targets non-cancelled events.
    """
    from flask import url_for

    from app.extensions import db
    from app.models.event import Event, EventStatus
    from app.services.mailer import send_event_reminder_email

    now = datetime.now(UTC)
    target_start = now + timedelta(days=3)
    # Window: events that start within the next 3 days ± 12 hours
    window_start = target_start - timedelta(hours=12)
    window_end = target_start + timedelta(hours=12)

    events = db.session.scalars(
        db.select(Event).where(
            Event.event_date >= window_start,
            Event.event_date <= window_end,
            Event.status != EventStatus.CANCELLED.value,
        )
    ).all()

    for event in events:
        for attendee in event.attendees:
            if not attendee.email:
                continue
            try:
                portal_url = url_for("events.detail", event_id=event.id, _external=True)
                send_event_reminder_email(
                    to_email=attendee.email,
                    full_name=attendee.full_name,
                    event_title=event.title,
                    event_date=event.event_date,
                    event_location=event.location,
                    portal_url=portal_url,
                )
                logger.info("Event reminder sent to %s for event #%d", attendee.email, event.id)
            except Exception:
                logger.exception(
                    "Failed to send event reminder to %s for event #%d",
                    attendee.email,
                    event.id,
                )
