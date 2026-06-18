"""Pydantic 2 schemas for the REST API v1."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

# ---------------------------------------------------------------------------
# Shared config
# ---------------------------------------------------------------------------


class _Base(BaseModel):
    """Modèle de base commun à tous les schémas API."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


# ---------------------------------------------------------------------------
# Event schemas
# ---------------------------------------------------------------------------


class SlotIn(_Base):
    """Créneaux horaires à créer avec l'événement ou à ajouter après."""

    slot_date: str = Field(..., description="Date du créneau (YYYY-MM-DD)")
    start_time: str | None = Field(None, description="Heure de début (HH:MM)")
    end_time: str | None = Field(None, description="Heure de fin (HH:MM)")
    label: str | None = Field(None, max_length=100, description="Libellé du créneau")


class VolunteerSlotAvailabilityOut(_Base):
    """Représentation de la disponibilité d'un bénévole sur un créneau."""

    slot_id: int
    volunteer_id: int
    status: str
    updated_at: datetime


class SlotOut(_Base):
    """Représentation d'un créneau horaire."""

    id: int
    slot_date: Any  # date
    start_time: Any | None = None  # time
    end_time: Any | None = None  # time
    label: str | None = None
    volunteer_availabilities: list[VolunteerSlotAvailabilityOut] = Field(
        default_factory=list, description="Disponibilités des bénévoles pour ce créneau"
    )


class VolunteerOut(_Base):
    """Représentation d'un bénévole inscrit."""

    id: int
    name: str
    email: str
    confirmed: bool
    registered_at: datetime


class EventOut(_Base):
    """Représentation complète d'un événement."""

    id: int
    title: str
    description: str | None = None
    status: str
    event_date: datetime
    end_date: datetime | None = None
    location: str | None = None
    website: str | None = None
    volunteer_token: str | None = None
    created_by_id: int
    created_at: datetime
    slots: list[SlotOut] = Field(default_factory=list)
    volunteers: list[VolunteerOut] = Field(default_factory=list)


class EventCreateIn(_Base):
    """Corps de la requête POST /events."""

    title: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None)
    status: str = Field("planned", description="planned | in_progress | completed | cancelled")
    event_date: datetime = Field(..., description="Date/heure UTC de l'événement")
    end_date: datetime | None = Field(None, description="Date/heure UTC de fin (optionnelle)")
    location: str | None = Field(None, max_length=200)
    website: str | None = Field(None, max_length=500)
    slots: list[SlotIn] = Field(default_factory=list, description="Créneaux à créer")
    dates: list[str] = Field(
        default_factory=list,
        description="Dates non-consécutives supplémentaires (YYYY-MM-DD)",
    )

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        allowed = {"planned", "in_progress", "completed", "cancelled"}
        if v not in allowed:
            msg = f"Statut invalide : {v!r}. Valeurs acceptées : {sorted(allowed)}"
            raise ValueError(msg)
        return v


class EventListQuery(_Base):
    """Paramètres de filtrage pour GET /events."""

    status: str | None = Field(None, description="Filtrer par statut")
    date_from: datetime | None = Field(None, description="Date minimale (ISO 8601 UTC)")
    date_to: datetime | None = Field(None, description="Date maximale (ISO 8601 UTC)")
    limit: Annotated[int, Field(ge=1, le=200)] = Field(20, description="Nombre max de résultats")
    offset: Annotated[int, Field(ge=0)] = Field(0, description="Décalage pour la pagination")


class EventListOut(_Base):
    """Réponse paginée pour GET /events."""

    total: int
    limit: int
    offset: int
    items: list[EventOut]


# ---------------------------------------------------------------------------
# Volunteer schemas
# ---------------------------------------------------------------------------


class VolunteerIn(_Base):
    """Corps de la requête POST /events/{id}/volunteers."""

    name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr
    send_confirmation: bool = Field(
        False,
        description="Si True, envoie l'email de confirmation via Celery.",
    )


class VolunteerCreateOut(_Base):
    """Réponse à l'inscription d'un bénévole."""

    id: int
    name: str
    email: str
    confirmed: bool
    personal_token: str
    registered_at: datetime


# ---------------------------------------------------------------------------
# Availability schemas
# ---------------------------------------------------------------------------


class MemberOut(_Base):
    """Représentation d'un membre (utilisateur) de l'association."""

    id: int
    name: str
    email: str


class AvailabilityIn(_Base):
    """Corps de la requête PUT /events/{id}/slots/{slot_id}/availability.

    Cible soit un membre (``user_id``, réservé au bureau), soit un bénévole
    externe (``volunteer_id`` ou ``email``). ``user_id`` est prioritaire.
    """

    status: str = Field(..., description="present | maybe | absent")
    user_id: int | None = Field(
        None, description="ID du membre à affecter (réservé au bureau, prioritaire)"
    )
    volunteer_id: int | None = Field(None, description="ID du bénévole")
    email: str | None = Field(
        None, description="Email du bénévole (utilisé si volunteer_id absent)"
    )

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        allowed = {"present", "maybe", "absent"}
        if v not in allowed:
            msg = f"Statut invalide : {v!r}. Valeurs acceptées : {sorted(allowed)}"
            raise ValueError(msg)
        return v
