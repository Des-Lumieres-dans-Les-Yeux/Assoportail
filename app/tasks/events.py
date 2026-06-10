"""Celery tasks for event management."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

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
