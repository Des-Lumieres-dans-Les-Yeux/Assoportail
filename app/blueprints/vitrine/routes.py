"""Vitrine blueprint routes — public association showcase page."""

import json
from datetime import UTC, datetime

from flask import render_template
from sqlalchemy.orm import selectinload

from app.blueprints.vitrine import bp
from app.extensions import db
from app.models.center import Center, CenterStatus
from app.models.event import Event, EventStatus
from app.models.machine import MachineInstallation


@bp.route("/")
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
                "pathology": c.pathology,
                "target_audience": c.target_audience,
            }
        )

    return render_template(
        "vitrine/map.html",
        centers_json=json.dumps(centers_data),
    )
