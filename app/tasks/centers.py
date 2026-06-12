"""Celery tasks — center-related notifications."""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    name="tasks.notify_bureau_installation_request",
    bind=True,
    max_retries=0,
    time_limit=60,
    soft_time_limit=50,
)
def notify_bureau_installation_request(self, request_id: int) -> dict:
    """Email all bureau members about a new public installation request."""
    from flask import url_for

    from app.extensions import db
    from app.models.center import InstallationRequest
    from app.models.user import User, UserRole
    from app.services.mailer import send_installation_request_email

    req = db.session.get(InstallationRequest, request_id)
    if req is None:
        return {"status": "error", "error": "InstallationRequest introuvable."}

    bureau_users = db.session.scalars(
        db.select(User).where(User.role == UserRole.BUREAU)
    ).all()

    portal_url = url_for("centers.list_requests", _external=True)
    sent = 0
    failed: list[str] = []
    for bu in bureau_users:
        if not bu.email:
            continue
        try:
            send_installation_request_email(
                to_email=bu.email,
                full_name=bu.full_name,
                center_name=req.center_name,
                contact_name=req.contact_name,
                contact_email=req.contact_email,
                city=req.city,
                portal_url=portal_url,
            )
            sent += 1
        except Exception:
            logger.exception("Failed to send installation request alert to %s", bu.email)
            failed.append(bu.email)

    status = "ok" if not failed else "partial"
    return {"status": status, "sent": sent, "failed": failed}


@shared_task(
    name="tasks.notify_bureau_breakdown",
    bind=True,
    max_retries=0,
    time_limit=60,
    soft_time_limit=50,
)
def notify_bureau_breakdown(self, task_id: int, reporter_name: str) -> dict:
    """Email all bureau members about a new breakdown task."""
    from flask import url_for

    from app.extensions import db
    from app.models.center import Center
    from app.models.task import Task
    from app.models.user import User, UserRole
    from app.services.mailer import send_breakdown_alert_email

    task = db.session.get(Task, task_id)
    if task is None:
        return {"status": "error", "error": f"Task {task_id} introuvable."}

    if not task.source_center_id:
        return {"status": "error", "error": f"Task {task_id} sans source_center_id."}

    center = db.session.get(Center, task.source_center_id)
    if center is None:
        return {"status": "error", "error": f"Center pour task {task_id} introuvable."}

    bureau_users = db.session.scalars(
        db.select(User).where(User.role == UserRole.BUREAU)
    ).all()

    portal_url = url_for("tasks.detail", task_id=task.id, _external=True)
    sent = 0
    failed: list[str] = []
    for bu in bureau_users:
        if not bu.email:
            continue
        try:
            send_breakdown_alert_email(
                to_email=bu.email,
                full_name=bu.full_name,
                center_name=center.name,
                description=task.description or "",
                reporter_name=reporter_name,
                portal_url=portal_url,
            )
            sent += 1
        except Exception:
            logger.exception("Failed to send breakdown alert to %s", bu.email)
            failed.append(bu.email)

    status = "ok" if not failed else "partial"
    return {"status": status, "sent": sent, "failed": failed}
