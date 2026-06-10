"""Dashboard blueprint routes — KPIs, alerts, and activity feed."""

from __future__ import annotations

import os
import random
from datetime import UTC, date, datetime, timedelta

from flask import Response, current_app, render_template, send_file, url_for
from flask_login import current_user, login_required

from app.blueprints.dashboard import bp
from app.extensions import db


@bp.route("/")
@login_required
def index():
    """Render the main dashboard with KPIs, alerts, and activity feed.

    All authenticated users see the KPI counters and upcoming events.
    Bureau users additionally see expiring memberships, Gmail health,
    recent unprocessed emails, and the audit log feed.
    """
    from sqlalchemy import and_
    from sqlalchemy.orm import selectinload

    from app.models.event import (
        Event,
        EventSlot,
        EventStatus,
        SlotAvailability,
    )
    from app.models.machine import (
        Machine,
        MachineInstallation,
        MachineStatus,
        MaintenanceRecord,
        MaintenanceStatus,
    )
    from app.models.task import Task, TaskSource, TaskStatus
    from app.models.user import User

    now = datetime.now(UTC)

    # KPIs — all users
    active_members = (
        db.session.scalar(db.select(db.func.count(User.id)).where(User.is_active.is_(True))) or 0
    )
    # Count only machines installed in centers, not at members' homes
    machines_installed = (
        db.session.scalar(
            db.select(db.func.count(Machine.id))
            .join(
                MachineInstallation,
                and_(
                    MachineInstallation.machine_id == Machine.id,
                    MachineInstallation.removed_at.is_(None),  # Active installation
                ),
            )
            .where(
                Machine.status == MachineStatus.INSTALLED.value,
                MachineInstallation.center_id.isnot(None),  # Only in centers
            )
        )
        or 0
    )
    upcoming_events = (
        db.session.scalar(
            db.select(db.func.count(Event.id)).where(
                Event.event_date >= now,
                Event.status.not_in([EventStatus.COMPLETED.value, EventStatus.CANCELLED.value]),
            )
        )
        or 0
    )
    open_tasks = (
        db.session.scalar(
            db.select(db.func.count(Task.id)).where(Task.status == TaskStatus.OPEN.value)
        )
        or 0
    )

    # Upcoming events list (next 5)
    next_events = db.session.scalars(
        db.select(Event)
        .where(
            Event.event_date >= now,
            Event.status.not_in([EventStatus.COMPLETED.value, EventStatus.CANCELLED.value]),
        )
        .order_by(Event.event_date)
        .limit(5)
    ).all()

    # Urgent events: within +/- 3 days of today
    three_days_ago = now - timedelta(days=3)
    three_days_hence = now + timedelta(days=3)
    urgent_events = db.session.scalars(
        db.select(Event)
        .options(
            selectinload(Event.attendees),
            selectinload(Event.volunteers),
            selectinload(Event.slots)
            .selectinload(EventSlot.availabilities)
            .selectinload(SlotAvailability.user),
        )
        .where(
            Event.event_date >= three_days_ago,
            Event.event_date <= three_days_hence,
            Event.status != EventStatus.CANCELLED.value,
        )
        .order_by(Event.event_date)
    ).all()

    # Recent photos for slideshow (up to 8, randomly shuffled)
    from app.models.document import Document, DocumentType

    recent_photos = db.session.scalars(
        db.select(Document)
        .where(Document.type == DocumentType.PHOTO.value)
        .order_by(Document.uploaded_at.desc())
        .limit(20)
    ).all()
    slideshow_photos = random.sample(recent_photos, min(8, len(recent_photos)))

    # Open and in-progress tasks assigned to current user
    my_tasks = db.session.scalars(
        db.select(Task)
        .where(
            Task.assigned_to_id == current_user.id,
            Task.status.in_([TaskStatus.OPEN.value, TaskStatus.IN_PROGRESS.value]),
        )
        .order_by(Task.priority, Task.created_at)
        .limit(5)
    ).all()

    # Machines permission: open maintenance records + breakdown tasks
    open_maintenance = []
    open_breakdowns = []
    if current_user.has_permission("machines"):
        from sqlalchemy.orm import selectinload as _sel

        open_maintenance = db.session.scalars(
            db.select(MaintenanceRecord)
            .options(_sel(MaintenanceRecord.machine))
            .where(MaintenanceRecord.status == MaintenanceStatus.OPEN)
            .order_by(MaintenanceRecord.date.desc())
            .limit(10)
        ).all()

        open_breakdowns = db.session.scalars(
            db.select(Task)
            .where(
                Task.source == TaskSource.CENTER_BREAKDOWN,
                Task.status.in_([TaskStatus.OPEN, TaskStatus.IN_PROGRESS]),
            )
            .order_by(Task.created_at.desc())
            .limit(10)
        ).all()

    # Bureau-only data
    expiring_memberships = []
    gmail_status = {"connected": False, "stale": False}
    recent_emails = []
    audit_feed = []
    unassigned_tasks = []

    if current_user.is_bureau:
        from sqlalchemy.orm import selectinload

        from app.audit import AuditLog
        from app.models.email import GmailToken, InboundEmail
        from app.models.member import Membership

        # Memberships expiring in the next 30 days (not already expired)
        today = date.today()
        in_30_days = today + timedelta(days=30)
        expiring_memberships = db.session.scalars(
            db.select(Membership)
            .options(selectinload(Membership.user))
            .where(
                Membership.expires_at > today,
                Membership.expires_at <= in_30_days,
                Membership.is_pending.is_(False),
            )
            .order_by(Membership.expires_at)
        ).all()

        # Gmail token health
        token_row = db.session.get(GmailToken, 1)
        if token_row is not None:
            gmail_status["connected"] = True
            # Flag if token hasn't been refreshed in 7+ days
            if (now - token_row.updated_at).days > 7:
                gmail_status["stale"] = True

        # Recent unprocessed inbound emails
        recent_emails = db.session.scalars(
            db.select(InboundEmail)
            .where(InboundEmail.processed.is_(False))
            .order_by(InboundEmail.received_at.desc())
            .limit(5)
        ).all()

        # Recent audit log entries
        audit_feed = db.session.scalars(
            db.select(AuditLog).order_by(AuditLog.timestamp.desc()).limit(15)
        ).all()

        # Tasks with no assignee yet
        unassigned_tasks = db.session.scalars(
            db.select(Task)
            .where(
                Task.assigned_to_id.is_(None),
                Task.status.in_([TaskStatus.OPEN.value, TaskStatus.IN_PROGRESS.value]),
            )
            .order_by(Task.priority, Task.created_at)
            .limit(10)
        ).all()

    return render_template(
        "dashboard/index.html",
        active_members=active_members,
        machines_installed=machines_installed,
        upcoming_events=upcoming_events,
        open_tasks=open_tasks,
        next_events=next_events,
        urgent_events=urgent_events,
        my_tasks=my_tasks,
        expiring_memberships=expiring_memberships,
        gmail_status=gmail_status,
        recent_emails=recent_emails,
        audit_feed=audit_feed,
        unassigned_tasks=unassigned_tasks,
        slideshow_photos=slideshow_photos,
        open_maintenance=open_maintenance,
        open_breakdowns=open_breakdowns,
        now=now,
        today=date.today(),
    )


# ---------------------------------------------------------------------------
# PWA icon — serves the association logo if uploaded, else static fallback
# ---------------------------------------------------------------------------


@bp.route("/pwa-icon.png")
def pwa_icon():
    """Serve the association logo as the PWA icon (fallback: static file)."""
    from app.models.config import AssociationConfig

    cfg = AssociationConfig.get()
    if cfg.logo:
        headers = {"Cache-Control": "public, max-age=86400"}
        return Response(cfg.logo, mimetype="image/png", headers=headers)
    # Fallback to the bundled static icon
    static_path = os.path.join(current_app.root_path, "static", "icons", "icon-192.png")
    return send_file(static_path, mimetype="image/png")


# ---------------------------------------------------------------------------
# Dynamic manifest — injects the pwa_icon URL
# ---------------------------------------------------------------------------


@bp.route("/manifest.json")
def manifest():
    """Serve the PWA manifest with the dynamic icon URL."""
    icon_url = url_for("dashboard.pwa_icon", _external=False)
    data = {
        "name": "Assoportail",
        "short_name": "Assoportail",
        "description": "Portail de gestion associatif",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait-primary",
        "background_color": "#ffffff",
        "theme_color": "#2563EB",
        "prefer_related_applications": False,
        "lang": "fr",
        "icons": [
            {"src": icon_url, "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": icon_url, "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
    }
    import json

    return Response(
        json.dumps(data),
        mimetype="application/manifest+json",
        headers={"Cache-Control": "public, max-age=3600"},
    )
