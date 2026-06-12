"""Shared utilities for Celery task modules."""

from __future__ import annotations


def public_url(endpoint: str, **values) -> str:
    """Build a public-facing URL using LINKS_EXTERNAL_URL as the host.

    Use this for links sent to external users (public forms: feedback,
    signalement, tombola, volunteer confirmation…).
    Falls back to TASK_BASE_URL if LINKS_EXTERNAL_URL is not set.

    Unlike url_for(_external=True) which uses TASK_BASE_URL (the portal host),
    this function uses the public subdomain so recipients click the right domain.
    """
    from flask import current_app, url_for

    cfg = current_app.config
    base = (cfg.get("LINKS_EXTERNAL_URL") or cfg.get("TASK_BASE_URL", "http://localhost")).rstrip(
        "/"
    )
    return base + url_for(endpoint, **values)
