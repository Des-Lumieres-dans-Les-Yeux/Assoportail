"""Celery task: periodic HelloAsso membership sync."""

import logging

from celery import shared_task
from flask import current_app

logger = logging.getLogger(__name__)


@shared_task(name="tasks.sync_helloasso")
def sync_helloasso() -> str:
    """Fetch new HelloAsso membership orders and import them.

    Scheduled via Celery beat (interval configurable in config).
    Runs inside the Flask application context so the service can
    access the database via SQLAlchemy.

    Returns:
        Human-readable summary string (logged by Celery).
    """
    from app.services.helloasso import sync_helloasso_memberships

    api_token: str = current_app.config.get("HELLOASSO_API_TOKEN", "")
    org_slug: str = current_app.config.get("HELLOASSO_ORGANIZATION_SLUG", "")

    if not api_token or not org_slug:
        logger.warning("HelloAsso credentials not configured — skipping sync")
        return "skipped: credentials missing"

    try:
        count = sync_helloasso_memberships(api_token, org_slug)
        result = f"imported {count} new membership(s)"
        logger.info("HelloAsso sync: %s", result)
        return result
    except Exception:
        logger.exception("HelloAsso sync failed")
        raise
