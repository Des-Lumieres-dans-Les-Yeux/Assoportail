from datetime import date, time
from uuid import uuid4

import pytest
from flask import Flask

from app.extensions import db as _db
from app.models.api_token import ApiToken
from app.models.event import (
    Event,
    EventSlot,
    EventStatus,
    EventVolunteer,
    SlotAvailability,
    SlotAvailabilityStatus,
    VolunteerSlotAvailability,
)
from app.models.user import User, UserPermission, UserRole


@pytest.fixture
def session(app: Flask):
    """Session SQLAlchemy fonctionnelle — app context ouvert pendant le test."""
    with app.app_context():
        yield _db.session


@pytest.fixture
def app_user(session):
    """Un membre actif en base (créé via la session de test)."""
    user = User(
        email="app_user@test.com",
        first_name="App",
        last_name="User",
        role=UserRole.MEMBER,
        is_active=True,
        must_change_password=False,
    )
    user.set_password("motdepasse123")
    session.add(user)
    session.commit()
    return user


@pytest.fixture
def auth_headers(app: Flask):
    """Factory : en-têtes Bearer pour un utilisateur API avec la permission events.

    Le rôle par défaut est ``bureau`` (qui a toutes les permissions d'office) ;
    ``auth_headers("member")`` crée un membre avec la permission ``events``.
    """

    def _make(role: str = "bureau") -> dict:
        role_enum = UserRole.BUREAU if role == "bureau" else UserRole.MEMBER
        with app.app_context():
            user = User(
                email=f"api_management_{role}_{uuid4().hex[:8]}@test.com",
                first_name="API",
                last_name="Gestion",
                role=role_enum,
                is_active=True,
                must_change_password=False,
                permissions=[UserPermission.EVENTS.value],
            )
            user.set_password("motdepasse123")
            _db.session.add(user)
            _db.session.commit()
            plaintext, token = ApiToken.generate(name="management test", user_id=user.id)
            _db.session.add(token)
            _db.session.commit()
            _db.session.remove()
        return {"Authorization": f"Bearer {plaintext}"}

    return _make


@pytest.fixture
def setup_event(session, app_user):
    event = Event(
        title="Test Event",
        status=EventStatus.PLANNED,
        event_date=date(2026, 9, 1),
        created_by_id=app_user.id,
    )
    session.add(event)
    session.commit()
    slot = EventSlot(
        event_id=event.id,
        slot_date=date(2026, 9, 1),
        start_time=time(9, 0),
        end_time=time(12, 0),
        label="Morning",
    )
    session.add(slot)
    session.commit()
    return event, slot


def _make_volunteer(session, event_id: int, name: str, email: str) -> EventVolunteer:
    vol = EventVolunteer(
        event_id=event_id,
        name=name,
        email=email,
        personal_token=uuid4().hex,
    )
    session.add(vol)
    session.commit()
    return vol


class TestEventManagement:
    def test_patch_event(self, client, auth_headers, session, setup_event):
        """Test PATCH /api/v1/events/<id>."""
        event, _ = setup_event
        resp = client.patch(
            f"/api/v1/events/{event.id}",
            headers=auth_headers("bureau"),
            json={"title": "Updated Event", "status": "completed"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["title"] == "Updated Event"
        assert data["status"] == "completed"

        # Verify in DB
        db_event = session.get(Event, event.id)
        assert db_event.title == "Updated Event"
        assert db_event.status == EventStatus.COMPLETED

    def test_delete_event(self, client, auth_headers, session, setup_event):
        """Test DELETE /api/v1/events/<id>."""
        event, _ = setup_event
        event_id = event.id
        resp = client.delete(
            f"/api/v1/events/{event_id}",
            headers=auth_headers("bureau"),
        )
        assert resp.status_code == 204

        # Verify in DB
        db_event = session.get(Event, event_id)
        assert db_event is None


class TestSlotManagement:
    def test_patch_slot(self, client, auth_headers, session, setup_event):
        """Test PATCH /api/v1/events/<id>/slots/<slot_id>."""
        event, slot = setup_event
        resp = client.patch(
            f"/api/v1/events/{event.id}/slots/{slot.id}",
            headers=auth_headers("bureau"),
            json={"start_time": "10:30", "end_time": "12:30", "label": "Updated Slot"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["start_time"] == "10:30:00"
        assert data["end_time"] == "12:30:00"
        assert data["label"] == "Updated Slot"

        # Verify in DB
        db_slot = session.get(EventSlot, slot.id)
        assert db_slot.label == "Updated Slot"

    def test_delete_slot(self, client, auth_headers, session, setup_event):
        """Test DELETE /api/v1/events/<id>/slots/<slot_id>."""
        event, slot = setup_event
        slot_id = slot.id
        resp = client.delete(
            f"/api/v1/events/{event.id}/slots/{slot_id}",
            headers=auth_headers("bureau"),
        )
        assert resp.status_code == 204

        # Verify in DB
        db_slot = session.get(EventSlot, slot_id)
        assert db_slot is None

    def test_get_slot_availabilities(self, client, auth_headers, session, setup_event, app_user):
        """Test GET /api/v1/events/<id>/slots/<slot_id>/availabilities."""
        event, slot = setup_event

        # Add some availabilities manually
        sa = SlotAvailability(
            slot_id=slot.id, user_id=app_user.id, status=SlotAvailabilityStatus.PRESENT
        )
        session.add(sa)

        vol = _make_volunteer(session, event.id, "Test Vol", "vol@test.com")

        vsa = VolunteerSlotAvailability(
            slot_id=slot.id, volunteer_id=vol.id, status=SlotAvailabilityStatus.ABSENT
        )
        session.add(vsa)
        session.commit()

        resp = client.get(
            f"/api/v1/events/{event.id}/slots/{slot.id}/availabilities",
            headers=auth_headers("bureau"),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["member_availabilities"]) == 1
        assert data["member_availabilities"][0]["user_id"] == app_user.id
        assert len(data["volunteer_availabilities"]) == 1
        assert data["volunteer_availabilities"][0]["volunteer_id"] == vol.id


class TestVolunteerManagement:
    def test_confirm_volunteer(self, client, auth_headers, session, setup_event):
        """Test PATCH /api/v1/events/<id>/volunteers/<volunteer_id>/confirm."""
        event, _ = setup_event
        vol = _make_volunteer(session, event.id, "Test Vol", "vol2@test.com")

        resp = client.patch(
            f"/api/v1/events/{event.id}/volunteers/{vol.id}/confirm",
            headers=auth_headers("bureau"),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["confirmed"] is True

        db_vol = session.get(EventVolunteer, vol.id)
        assert db_vol.confirmed is True

    def test_delete_volunteer(self, client, auth_headers, session, setup_event):
        """Test DELETE /api/v1/events/<id>/volunteers/<volunteer_id>."""
        event, _ = setup_event
        vol = _make_volunteer(session, event.id, "Test Vol", "vol3@test.com")

        vol_id = vol.id
        resp = client.delete(
            f"/api/v1/events/{event.id}/volunteers/{vol_id}",
            headers=auth_headers("bureau"),
        )
        assert resp.status_code == 204

        db_vol = session.get(EventVolunteer, vol_id)
        assert db_vol is None


class TestMemberManagement:
    def test_get_member(self, client, auth_headers, session, app_user):
        """Test GET /api/v1/members/<id>."""
        resp = client.get(
            f"/api/v1/members/{app_user.id}",
            headers=auth_headers("bureau"),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["email"] == app_user.email
        assert data["id"] == app_user.id
