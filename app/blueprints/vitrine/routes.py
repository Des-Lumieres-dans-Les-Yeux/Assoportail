"""Vitrine blueprint routes — public association showcase page."""

from datetime import UTC, datetime

from flask import render_template
from sqlalchemy.orm import selectinload

from app.blueprints.vitrine import bp
from app.config import Config
from app.extensions import db, talisman
from app.models.center import Center, CenterStatus
from app.models.event import Event, EventStatus
from app.models.machine import MachineInstallation

# The vitrine pages are the only frameable pages of the app (WordPress
# embed). The view-level CSP replaces the global one, so it must be complete.
_VITRINE_CSP = Config.VITRINE_TALISMAN_CSP


@bp.route("/")
@talisman(content_security_policy=_VITRINE_CSP)
def index():
    """Public-facing association showcase — no authentication required."""
    active_centers = db.session.scalars(
        db.select(Center)
        .where(Center.status == CenterStatus.ACTIVE)
        .order_by(Center.city, Center.name)
    ).all()

    upcoming_events = db.session.scalars(
        db.select(Event)
        .where(
            Event.event_date >= datetime.now(UTC),
            Event.status.notin_([EventStatus.CANCELLED.value]),
        )
        .order_by(Event.event_date)
        .limit(6)
    ).all()

    return render_template(
        "vitrine/index.html",
        active_centers=active_centers,
        upcoming_events=upcoming_events,
    )


@bp.route("/map")
@talisman(content_security_policy=_VITRINE_CSP)
def map_view():
    """Standalone map view for embedding (e.g. in WordPress)."""
    centers = db.session.scalars(
        db.select(Center)
        .options(selectinload(Center.installations).selectinload(MachineInstallation.machine))
        .where(Center.status == CenterStatus.ACTIVE)
    ).all()

    centers_data = []
    for c in centers:
        active = c.active_installations
        machine_name = active[0].machine.display_name if active else "Aucune machine"

        centers_data.append(
            {
                "name": c.name,
                "city": c.city,
                "zip_code": c.zip_code,
                "lat": c.latitude,
                "lng": c.longitude,
                "machine_name": machine_name,
                "web_link": active[0].web_link if active else None,
                "pathology": c.pathology,
                "target_audience": c.target_audience,
            }
        )

    return render_template(
        "vitrine/map.html",
        centers_data=centers_data,
    )
