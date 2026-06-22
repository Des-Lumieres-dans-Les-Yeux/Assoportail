from datetime import time

import pytest

from app.models.event import (
    Event,
    EventSlot,
    EventStatus,
    EventVolunteer,
    SlotAvailability,
    SlotAvailabilityStatus,
    VolunteerSlotAvailability,
)


@pytest.fixture
def setup_event(session, app_user):
    event = Event(title="Test Event", status=EventStatus.PUBLISHED, created_by_id=app_user.id)
    session.add(event)
    session.commit()
    slot = EventSlot(
        event_id=event.id, start_time=time(9, 0), end_time=time(12, 0), label="Morning"
    )
    session.add(slot)
    session.commit()
    return event, slot


class TestEventManagement:
    def test_patch_event(self, client, auth_headers, session, setup_event):
        """Test PATCH /api/v1/events/<id>."""
        event, _ = setup_event
        resp = client.patch(
            f"/api/v1/events/{event.id}",
            headers=auth_headers("bureau"),
            json={"title": "Updated Event", "status": "published"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["title"] == "Updated Event"
        assert data["status"] == "published"

        # Verify in DB
        db_event = session.get(Event, event.id)
        assert db_event.title == "Updated Event"
        assert db_event.status == EventStatus.PUBLISHED

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
            slot_id=slot.id, user_id=app_user.id, status=SlotAvailabilityStatus.AVAILABLE
        )
        session.add(sa)

        vol = EventVolunteer(event_id=event.id, name="Test Vol", email="vol@test.com")
        session.add(vol)
        session.commit()

        vsa = VolunteerSlotAvailability(
            slot_id=slot.id, volunteer_id=vol.id, status=SlotAvailabilityStatus.UNAVAILABLE
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
        vol = EventVolunteer(event_id=event.id, name="Test Vol", email="vol2@test.com")
        session.add(vol)
        session.commit()

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
        vol = EventVolunteer(event_id=event.id, name="Test Vol", email="vol3@test.com")
        session.add(vol)
        session.commit()

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
