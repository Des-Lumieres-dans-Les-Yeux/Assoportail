"""Routes de l'API REST v1 — événements et bénévoles."""

from __future__ import annotations

import logging
import secrets as _secrets
from datetime import UTC, date, datetime, time

from flask import g, jsonify, request
from spectree import Response
from sqlalchemy.orm import selectinload

from app.blueprints.api import bp
from app.blueprints.api.auth import api_permission_required
from app.blueprints.api.schemas import (
    AvailabilityIn,
    EventCreateIn,
    EventListOut,
    EventListQuery,
    EventOut,
    MemberOut,
    SlotIn,
    SlotOut,
    VolunteerCreateOut,
    VolunteerIn,
    VolunteerOut,
    VolunteerSlotAvailabilityOut,
)
from app.extensions import db, limiter, spec
from app.models.event import (
    Event,
    EventDate,
    EventSlot,
    EventVolunteer,
    SlotAvailability,
    SlotAvailabilityStatus,
    VolunteerSlotAvailability,
)
from app.models.user import User, UserPermission

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EVENTS_PERM = UserPermission.EVENTS.value


def _parse_date(value: str) -> date:
    """Parse 'YYYY-MM-DD' → datetime.date."""
    return datetime.strptime(value, "%Y-%m-%d").date()


def _parse_time(value: str | None) -> time | None:
    """Parse 'HH:MM' ou 'HH:MM:SS' → datetime.time, ou None."""
    if not value:
        return None
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(value, fmt).time()
        except ValueError:
            continue
    raise ValueError(f"Format d'heure invalide : {value!r} (attendu HH:MM ou HH:MM:SS)")


def _sync_event_dates(event: Event, raw_dates: list[str]) -> None:
    """Synchronise les dates non-consécutives d'un événement.

    Identique à la logique de ``app.blueprints.events.routes._sync_event_dates``.

    Args:
        event: L'événement à mettre à jour.
        raw_dates: Liste de chaînes ISO 'YYYY-MM-DD'.
    """
    incoming: set[date] = set()
    for raw in raw_dates:
        raw = raw.strip()
        if raw:
            try:
                incoming.add(_parse_date(raw))
            except ValueError:
                pass

    existing = {ed.day: ed for ed in event.dates}

    for day, ed in list(existing.items()):
        if day not in incoming:
            db.session.delete(ed)

    for day in incoming:
        if day not in existing:
            db.session.add(EventDate(event_id=event.id, day=day))


def _add_slots(event: Event, slots_data: list[SlotIn]) -> None:
    """Ajoute des créneaux à un événement.

    Args:
        event: L'événement cible.
        slots_data: Liste de schémas SlotIn validés.
    """
    for slot_in in slots_data:
        slot = EventSlot(
            event_id=event.id,
            slot_date=_parse_date(slot_in.slot_date),
            start_time=_parse_time(slot_in.start_time),
            end_time=_parse_time(slot_in.end_time),
            label=slot_in.label,
        )
        db.session.add(slot)


def _event_to_out(event: Event) -> EventOut:
    """Convertit un objet Event en schéma EventOut."""
    slots = [
        SlotOut(
            id=s.id,
            slot_date=s.slot_date,
            start_time=s.start_time,
            end_time=s.end_time,
            label=s.label,
            volunteer_availabilities=[
                VolunteerSlotAvailabilityOut(
                    slot_id=va.slot_id,
                    volunteer_id=va.volunteer_id,
                    status=va.status.value,
                    updated_at=va.updated_at,
                )
                for va in s.volunteer_availabilities
            ],
        )
        for s in event.slots
    ]
    volunteers = [
        VolunteerOut(
            id=v.id,
            name=v.name,
            email=v.email,
            confirmed=v.confirmed,
            registered_at=v.registered_at,
        )
        for v in event.volunteers
        if v.confirmed
    ]
    return EventOut(
        id=event.id,
        title=event.title,
        description=event.description,
        status=event.status,
        event_date=event.event_date,
        end_date=event.end_date,
        location=event.location,
        website=event.website,
        volunteer_token=event.volunteer_token,
        created_by_id=event.created_by_id,
        created_at=event.created_at,
        slots=slots,
        volunteers=volunteers,
    )


def _load_event_full(event_id: int) -> Event | None:
    """Charge un Event avec ses slots, bénévoles et disponibilités des bénévoles."""
    return db.session.get(
        Event,
        event_id,
        options=[
            selectinload(Event.slots)
            .selectinload(EventSlot.volunteer_availabilities)
            .selectinload(VolunteerSlotAvailability.volunteer),
            selectinload(Event.volunteers),
            selectinload(Event.dates),
        ],
    )


def _json_error(message: str, status: int, code: str | None = None) -> tuple:
    body: dict = {"error": code or "error", "message": message}
    return jsonify(body), status


# ---------------------------------------------------------------------------
# POST /api/v1/events
# ---------------------------------------------------------------------------


@bp.route("/events", methods=["POST"])
@limiter.limit("60 per hour")
@api_permission_required(_EVENTS_PERM)
@spec.validate(
    json=EventCreateIn,
    resp=Response(HTTP_201=EventOut, HTTP_400=None, HTTP_422=None),
    tags=["events"],
    security={"BearerAuth": []},
    skip_validation=True,
)
def create_event():
    """Crée un nouvel événement.

    Exige la permission ``events``. Accepte les créneaux et dates
    non-consécutives optionnels dans le corps JSON.
    """
    raw = request.get_json(silent=True)
    if raw is None:
        return _json_error("Corps JSON attendu.", 400, "bad_request")

    try:
        payload = EventCreateIn.model_validate(raw)
    except Exception as exc:
        return _json_error(str(exc), 422, "validation_error")

    event = Event(
        title=payload.title.strip(),
        description=(payload.description or "").strip() or None,
        status=payload.status,
        event_date=payload.event_date.replace(tzinfo=UTC)
        if payload.event_date.tzinfo is None
        else payload.event_date.astimezone(UTC),
        end_date=(
            payload.end_date.replace(tzinfo=UTC)
            if payload.end_date is not None and payload.end_date.tzinfo is None
            else (payload.end_date.astimezone(UTC) if payload.end_date else None)
        ),
        location=(payload.location or "").strip() or None,
        website=(payload.website or "").strip() or None,
        volunteer_token=_secrets.token_urlsafe(32),
        created_by_id=g.api_user.id,
    )
    db.session.add(event)
    db.session.flush()  # obtenir event.id avant les relations

    if payload.slots:
        _add_slots(event, payload.slots)

    if payload.dates:
        _sync_event_dates(event, payload.dates)

    db.session.commit()

    event = _load_event_full(event.id)
    return jsonify(_event_to_out(event).model_dump(mode="json")), 201


# ---------------------------------------------------------------------------
# GET /api/v1/events
# ---------------------------------------------------------------------------


@bp.route("/events", methods=["GET"])
@limiter.limit("200 per hour")
@api_permission_required(_EVENTS_PERM)
@spec.validate(
    query=EventListQuery,
    resp=Response(HTTP_200=EventListOut, HTTP_422=None),
    tags=["events"],
    security={"BearerAuth": []},
    skip_validation=True,
)
def list_events():
    """Liste les événements avec filtres optionnels et pagination."""
    try:
        query = EventListQuery.model_validate(request.args.to_dict())
    except Exception as exc:
        return _json_error(str(exc), 422, "validation_error")

    stmt = db.select(Event).options(
        selectinload(Event.slots)
        .selectinload(EventSlot.volunteer_availabilities)
        .selectinload(VolunteerSlotAvailability.volunteer),
        selectinload(Event.volunteers),
        selectinload(Event.dates),
    )

    if query.status:
        stmt = stmt.where(Event.status == query.status)
    if query.date_from:
        dt_from = (
            query.date_from.replace(tzinfo=UTC)
            if query.date_from.tzinfo is None
            else query.date_from
        )
        stmt = stmt.where(Event.event_date >= dt_from)
    if query.date_to:
        dt_to = query.date_to.replace(tzinfo=UTC) if query.date_to.tzinfo is None else query.date_to
        stmt = stmt.where(Event.event_date <= dt_to)

    total = db.session.scalar(db.select(db.func.count()).select_from(stmt.subquery()))
    events = db.session.scalars(
        stmt.order_by(Event.event_date.asc()).limit(query.limit).offset(query.offset)
    ).all()

    out = EventListOut(
        total=total or 0,
        limit=query.limit,
        offset=query.offset,
        items=[_event_to_out(e) for e in events],
    )
    return jsonify(out.model_dump(mode="json")), 200


# ---------------------------------------------------------------------------
# GET /api/v1/events/<id>
# ---------------------------------------------------------------------------


@bp.route("/events/<int:event_id>", methods=["GET"])
@limiter.limit("200 per hour")
@api_permission_required(_EVENTS_PERM)
@spec.validate(
    resp=Response(HTTP_200=EventOut, HTTP_404=None),
    tags=["events"],
    security={"BearerAuth": []},
    skip_validation=True,
)
def get_event(event_id: int):
    """Retourne le détail d'un événement avec ses créneaux et bénévoles confirmés."""
    event = _load_event_full(event_id)
    if event is None:
        return _json_error("Événement introuvable.", 404, "not_found")
    return jsonify(_event_to_out(event).model_dump(mode="json")), 200


# ---------------------------------------------------------------------------
# POST /api/v1/events/<id>/slots
# ---------------------------------------------------------------------------


@bp.route("/events/<int:event_id>/slots", methods=["POST"])
@limiter.limit("60 per hour")
@api_permission_required(_EVENTS_PERM)
@spec.validate(
    json=SlotIn,
    resp=Response(HTTP_201=SlotOut, HTTP_400=None, HTTP_404=None, HTTP_422=None),
    tags=["events"],
    security={"BearerAuth": []},
    skip_validation=True,
)
def add_slot(event_id: int):
    """Ajoute un créneau horaire à un événement."""
    event = db.session.get(Event, event_id)
    if event is None:
        return _json_error("Événement introuvable.", 404, "not_found")

    raw = request.get_json(silent=True)
    if raw is None:
        return _json_error("Corps JSON attendu.", 400, "bad_request")

    try:
        slot_in = SlotIn.model_validate(raw)
    except Exception as exc:
        return _json_error(str(exc), 422, "validation_error")

    slot = EventSlot(
        event_id=event_id,
        slot_date=_parse_date(slot_in.slot_date),
        start_time=_parse_time(slot_in.start_time),
        end_time=_parse_time(slot_in.end_time),
        label=slot_in.label,
    )
    db.session.add(slot)
    db.session.commit()

    out = SlotOut(
        id=slot.id,
        slot_date=slot.slot_date,
        start_time=slot.start_time,
        end_time=slot.end_time,
        label=slot.label,
    )
    return jsonify(out.model_dump(mode="json")), 201


# ---------------------------------------------------------------------------
# GET /api/v1/events/<id>/volunteers
# ---------------------------------------------------------------------------


@bp.route("/events/<int:event_id>/volunteers", methods=["GET"])
@limiter.limit("200 per hour")
@api_permission_required(_EVENTS_PERM)
@spec.validate(
    resp=Response(HTTP_200=None, HTTP_404=None),
    tags=["events"],
    security={"BearerAuth": []},
    skip_validation=True,
)
def list_volunteers(event_id: int):
    """Liste tous les bénévoles inscrits à un événement."""
    event = db.session.get(Event, event_id, options=[selectinload(Event.volunteers)])
    if event is None:
        return _json_error("Événement introuvable.", 404, "not_found")

    out = [
        VolunteerOut(
            id=v.id,
            name=v.name,
            email=v.email,
            confirmed=v.confirmed,
            registered_at=v.registered_at,
        ).model_dump(mode="json")
        for v in event.volunteers
    ]
    return jsonify(out), 200


# ---------------------------------------------------------------------------
# GET /api/v1/members
# ---------------------------------------------------------------------------


@bp.route("/members", methods=["GET"])
@limiter.limit("200 per hour")
@api_permission_required(_EVENTS_PERM)
@spec.validate(
    resp=Response(HTTP_200=None),
    tags=["events"],
    security={"BearerAuth": []},
    skip_validation=True,
)
def list_members():
    """Liste les membres actifs de l'association (pour affecter sur des créneaux)."""
    members = db.session.scalars(
        db.select(User).where(User.is_active.is_(True)).order_by(User.last_name, User.first_name)
    ).all()
    out = [
        MemberOut(id=m.id, name=m.full_name, email=m.email).model_dump(mode="json") for m in members
    ]
    return jsonify(out), 200


# ---------------------------------------------------------------------------
# POST /api/v1/events/<id>/volunteers
# ---------------------------------------------------------------------------


@bp.route("/events/<int:event_id>/volunteers", methods=["POST"])
@limiter.limit("60 per hour")
@api_permission_required(_EVENTS_PERM)
@spec.validate(
    json=VolunteerIn,
    resp=Response(
        HTTP_200=VolunteerCreateOut,
        HTTP_201=VolunteerCreateOut,
        HTTP_400=None,
        HTTP_404=None,
        HTTP_422=None,
    ),
    tags=["events"],
    security={"BearerAuth": []},
    skip_validation=True,
)
def register_volunteer(event_id: int):
    """Inscrit ou ré-inscrit un bénévole (idempotent sur email).

    Si un bénévole avec cet email existe déjà pour cet événement, renvoie
    son enregistrement existant sans créer de doublon. Si ``send_confirmation``
    est True, déclenche l'envoi de l'email de confirmation via Celery.
    """
    event = db.session.get(Event, event_id)
    if event is None:
        return _json_error("Événement introuvable.", 404, "not_found")

    raw = request.get_json(silent=True)
    if raw is None:
        return _json_error("Corps JSON attendu.", 400, "bad_request")

    try:
        payload = VolunteerIn.model_validate(raw)
    except Exception as exc:
        return _json_error(str(exc), 422, "validation_error")

    email = str(payload.email).strip().lower()

    existing = db.session.scalars(
        db.select(EventVolunteer).where(
            EventVolunteer.event_id == event_id,
            EventVolunteer.email == email,
        )
    ).first()

    if existing:
        if payload.send_confirmation:
            try:
                from app.tasks.events import send_volunteer_confirmation

                send_volunteer_confirmation.delay(existing.id)
            except Exception:
                logger.warning(
                    "Impossible d'envoyer l'email de confirmation pour le bénévole %s",
                    existing.id,
                )
        out = VolunteerCreateOut(
            id=existing.id,
            name=existing.name,
            email=existing.email,
            confirmed=existing.confirmed,
            personal_token=existing.personal_token,
            registered_at=existing.registered_at,
        )
        return jsonify(out.model_dump(mode="json")), 200

    personal_token = _secrets.token_urlsafe(32)
    volunteer = EventVolunteer(
        event_id=event_id,
        name=payload.name.strip(),
        email=email,
        personal_token=personal_token,
        confirmed=False,
    )
    db.session.add(volunteer)
    db.session.commit()

    if payload.send_confirmation:
        try:
            from app.tasks.events import send_volunteer_confirmation

            send_volunteer_confirmation.delay(volunteer.id)
        except Exception:
            logger.warning(
                "Impossible d'envoyer l'email de confirmation pour le bénévole %s",
                volunteer.id,
            )

    out = VolunteerCreateOut(
        id=volunteer.id,
        name=volunteer.name,
        email=volunteer.email,
        confirmed=volunteer.confirmed,
        personal_token=volunteer.personal_token,
        registered_at=volunteer.registered_at,
    )
    return jsonify(out.model_dump(mode="json")), 201


# ---------------------------------------------------------------------------
# PUT /api/v1/events/<id>/slots/<slot_id>/availability
# ---------------------------------------------------------------------------


@bp.route("/events/<int:event_id>/slots/<int:slot_id>/availability", methods=["PUT"])
@limiter.limit("120 per hour")
@api_permission_required(_EVENTS_PERM)
@spec.validate(
    json=AvailabilityIn,
    resp=Response(HTTP_200=None, HTTP_400=None, HTTP_404=None, HTTP_422=None),
    tags=["events"],
    security={"BearerAuth": []},
    skip_validation=True,
)
def set_availability(event_id: int, slot_id: int):
    """Déclare ou met à jour la disponibilité d'un bénévole sur un créneau."""
    slot = db.session.get(EventSlot, slot_id)
    if slot is None or slot.event_id != event_id:
        return _json_error("Créneau introuvable.", 404, "not_found")

    raw = request.get_json(silent=True)
    if raw is None:
        return _json_error("Corps JSON attendu.", 400, "bad_request")

    try:
        payload = AvailabilityIn.model_validate(raw)
    except Exception as exc:
        return _json_error(str(exc), 422, "validation_error")

    status = SlotAvailabilityStatus(payload.status)

    # Affectation d'un membre (réservée au bureau) — table SlotAvailability.
    if payload.user_id is not None:
        if not g.api_user.is_bureau:
            return _json_error("Affecter un membre est réservé au bureau.", 403, "forbidden")
        member = db.session.get(User, payload.user_id)
        if member is None or not member.is_active:
            return _json_error("Membre introuvable.", 404, "not_found")

        avail = db.session.get(SlotAvailability, (slot_id, member.id))
        if avail is None:
            db.session.add(SlotAvailability(slot_id=slot_id, user_id=member.id, status=status))
        else:
            avail.status = status
        db.session.commit()
        return (
            jsonify({"slot_id": slot_id, "user_id": member.id, "status": status.value}),
            200,
        )

    # Résolution du bénévole
    volunteer: EventVolunteer | None = None
    if payload.volunteer_id is not None:
        volunteer = db.session.get(EventVolunteer, payload.volunteer_id)
        if volunteer is None or volunteer.event_id != event_id:
            return _json_error("Bénévole introuvable.", 404, "not_found")
    elif payload.email:
        email = payload.email.strip().lower()
        volunteer = db.session.scalars(
            db.select(EventVolunteer).where(
                EventVolunteer.event_id == event_id,
                EventVolunteer.email == email,
            )
        ).first()
        if volunteer is None:
            return _json_error("Bénévole introuvable pour cet email.", 404, "not_found")
    elif payload.id is not None:
        # Fallback : essayer comme volunteer_id
        volunteer = db.session.get(EventVolunteer, payload.id)
        if volunteer is None or volunteer.event_id != event_id:
            return _json_error("Bénévole introuvable.", 404, "not_found")
    else:
        return _json_error("user_id, volunteer_id ou email requis.", 422, "validation_error")

    avail = db.session.execute(
        db.select(VolunteerSlotAvailability).where(
            VolunteerSlotAvailability.slot_id == slot_id,
            VolunteerSlotAvailability.volunteer_id == volunteer.id,
        )
    ).scalar_one_or_none()

    if avail is None:
        avail = VolunteerSlotAvailability(
            slot_id=slot_id,
            volunteer_id=volunteer.id,
            status=status,
        )
        db.session.add(avail)
    else:
        avail.status = status

    db.session.commit()

    return (
        jsonify(
            {
                "slot_id": slot_id,
                "volunteer_id": volunteer.id,
                "status": status.value,
            }
        ),
        200,
    )
