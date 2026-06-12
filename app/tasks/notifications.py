"""Celery tasks — transactional notification emails."""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    name="tasks.send_welcome_email",
    bind=True,
    max_retries=2,
    time_limit=60,
    soft_time_limit=50,
)
def send_welcome_email_task(self, user_id: int, temp_password: str) -> dict:
    """Send welcome email with temporary credentials to a newly created user."""
    from flask import url_for

    from app.extensions import db
    from app.models.user import User
    from app.services.mailer import send_welcome_email

    user = db.session.get(User, user_id)
    if user is None:
        return {"status": "error", "error": f"User {user_id} introuvable."}

    login_url = url_for("auth.login", _external=True)
    try:
        send_welcome_email(user.email, temp_password, user.full_name, login_url)
    except Exception as exc:
        logger.exception("Failed to send welcome email to user #%d", user_id)
        raise self.retry(exc=exc, countdown=30) from exc

    return {"status": "ok", "user_id": user_id}


@shared_task(
    name="tasks.send_password_reset_email",
    bind=True,
    max_retries=2,
    time_limit=60,
    soft_time_limit=50,
)
def send_password_reset_task(self, user_id: int, reset_url: str) -> dict:
    """Send password reset email with the signed reset link."""
    from app.extensions import db
    from app.models.user import User
    from app.services.mailer import send_password_reset_email

    user = db.session.get(User, user_id)
    if user is None:
        return {"status": "error", "error": f"User {user_id} introuvable."}

    try:
        send_password_reset_email(user.email, user.full_name, reset_url)
    except Exception as exc:
        logger.exception("Failed to send password reset email to user #%d", user_id)
        raise self.retry(exc=exc, countdown=30) from exc

    return {"status": "ok", "user_id": user_id}


@shared_task(
    name="tasks.send_admin_reset_email",
    bind=True,
    max_retries=2,
    time_limit=60,
    soft_time_limit=50,
)
def send_admin_reset_task(self, user_id: int, temp_password: str) -> dict:
    """Send admin-initiated password reset email with new temporary credentials."""
    from flask import url_for

    from app.extensions import db
    from app.models.user import User
    from app.services.mailer import send_admin_reset_email

    user = db.session.get(User, user_id)
    if user is None:
        return {"status": "error", "error": f"User {user_id} introuvable."}

    login_url = url_for("auth.login", _external=True)
    try:
        send_admin_reset_email(user.email, temp_password, user.full_name, login_url)
    except Exception as exc:
        logger.exception("Failed to send admin reset email to user #%d", user_id)
        raise self.retry(exc=exc, countdown=30) from exc

    return {"status": "ok", "user_id": user_id}


@shared_task(
    name="tasks.notify_task_assigned",
    bind=True,
    max_retries=2,
    time_limit=60,
    soft_time_limit=50,
)
def notify_task_assigned(self, task_id: int, assigner_name: str) -> dict:
    """Notify task assignee by email and push notification."""
    from flask import url_for

    from app.extensions import db
    from app.models.task import Task
    from app.models.user import User
    from app.services.mailer import send_task_assigned_email

    task = db.session.get(Task, task_id)
    if task is None:
        return {"status": "error", "error": f"Task {task_id} introuvable."}

    if not task.assigned_to_id:
        return {"status": "skip", "reason": "no assignee"}

    assignee = db.session.get(User, task.assigned_to_id)
    if not assignee or not assignee.email:
        return {"status": "skip", "reason": "no assignee email"}

    portal_url = url_for("tasks.detail", task_id=task.id, _external=True)
    try:
        send_task_assigned_email(
            to_email=assignee.email,
            full_name=assignee.full_name,
            task_title=task.title,
            task_description=task.description,
            assigner_name=assigner_name,
            portal_url=portal_url,
        )
    except Exception as exc:
        logger.exception("Failed to send task assignment email for task #%d", task_id)
        raise self.retry(exc=exc, countdown=30) from exc

    try:
        from app.services.push import send_push_notification

        task_url = url_for("tasks.detail", task_id=task.id)
        send_push_notification(
            user_ids=[task.assigned_to_id],
            title="Tâche assignée",
            body=task.title,
            url=task_url,
        )
    except Exception:
        logger.exception("Failed to send push notification for task #%d", task_id)

    return {"status": "ok", "task_id": task_id}
