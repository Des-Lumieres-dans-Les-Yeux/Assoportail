"""Celery tasks for event management."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from celery import shared_task
from sqlalchemy import func

from app.extensions import db
from app.models.event import Event, EventStatus
from app.tasks import celery

logger = logging.getLogger(__name__)


@celery.task(name="tasks.update_event_statuses", time_limit=600)
def update_event_statuses() -> None:
    """Automatically update event statuses based on current date.

    - PLANNED -> IN_PROGRESS when event_date is today or in the past.
    - IN_PROGRESS -> COMPLETED 3 days after end_date (or event_date if end_date is null).
    """
    now = datetime.now(UTC)
    today = now.date()
    three_days_ago = today - timedelta(days=3)

    # 1. PLANNED -> IN_PROGRESS
    # We check if the event_date (date part) is <= today
    to_in_progress = db.session.scalars(
        db.select(Event).where(
            Event.status == EventStatus.PLANNED.value,
            func.date(Event.event_date) <= today,
        )
    ).all()

    for event in to_in_progress:
        event.status = EventStatus.IN_PROGRESS.value
        logger.info("Event #%d (%s) set to IN_PROGRESS", event.id, event.title)

    # 2. IN_PROGRESS -> COMPLETED
    # We check if (end_date or event_date) < three_days_ago
    # Using coalesce to handle null end_date
    to_completed = db.session.scalars(
        db.select(Event).where(
            Event.status == EventStatus.IN_PROGRESS.value,
            func.date(func.coalesce(Event.end_date, Event.event_date)) < three_days_ago,
        )
    ).all()

    for event in to_completed:
        event.status = EventStatus.COMPLETED.value
        logger.info("Event #%d (%s) set to COMPLETED", event.id, event.title)

    db.session.commit()


@shared_task(
    name="tasks.send_volunteer_confirmation",
    bind=True,
    max_retries=2,
    time_limit=60,
    soft_time_limit=50,
)
def send_volunteer_confirmation(self, volunteer_id: int) -> dict:
    """Send confirmation email to a newly registered event volunteer."""
    from app.models.event import EventVolunteer
    from app.services.mailer import _deliver
    from app.tasks.utils import public_url

    volunteer = db.session.get(EventVolunteer, volunteer_id)
    if volunteer is None:
        return {"status": "error", "error": f"EventVolunteer {volunteer_id} introuvable."}

    event = db.session.get(Event, volunteer.event_id)
    if event is None:
        return {"status": "error", "error": f"Event {volunteer.event_id} introuvable."}

    link = public_url("events.volunteer_confirm", personal_token=volunteer.personal_token)
    subject = f"Confirmez votre inscription — {event.title}"
    body = (
        f"Bonjour {volunteer.name},\n\n"
        f"Vous vous êtes inscrit(e) comme bénévole pour l'événement « {event.title} ».\n\n"
        f"Cliquez sur le lien ci-dessous pour confirmer votre inscription "
        f"et accéder à votre portail personnel :\n\n"
        f"{link}\n\n"
        f"Ce lien est permanent — conservez-le pour revenir sur vos choix de créneaux.\n\n"
        f"Merci !\n"
        f"— Assoportail"
    )
    try:
        _deliver(to_email=volunteer.email, subject=subject, body=body)
    except Exception as exc:
        logger.exception("Failed to send volunteer confirmation to volunteer #%d", volunteer_id)
        raise self.retry(exc=exc, countdown=30) from exc

    return {"status": "ok", "volunteer_id": volunteer_id}


@shared_task(
    name="tasks.email_event_participants",
    bind=True,
    max_retries=0,
    time_limit=300,
    soft_time_limit=270,
)
def email_event_participants(self, event_id: int, subject: str, body: str) -> dict:
    """Send a free-form email to all attendees and confirmed volunteers of an event."""
    from sqlalchemy.orm import selectinload

    from app.services.mailer import _deliver

    event = db.session.get(
        Event,
        event_id,
        options=[selectinload(Event.attendees), selectinload(Event.volunteers)],
    )
    if event is None:
        return {"status": "error", "error": f"Event {event_id} introuvable."}

    recipients: set[str] = set()
    for u in event.attendees:
        if u.email:
            recipients.add(u.email)
    for v in event.volunteers:
        if v.confirmed and v.email:
            recipients.add(v.email)

    sent = 0
    failed = 0
    for addr in recipients:
        try:
            _deliver(to_email=addr, subject=subject, body=body)
            sent += 1
        except Exception:
            logger.exception("Failed to email participant %s for event #%d", addr, event_id)
            failed += 1

    return {"status": "ok", "sent": sent, "failed": failed}
