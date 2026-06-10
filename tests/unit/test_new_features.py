"""Tests for features added during the March 2026 development session.

Covers:
- Profile self-editing
- Members CSV export/import
- Cashbox reset
- Volunteer registration flow
- Distance-based expense (km)
- Machine document upload
- Machine purchase fields
- Event task creation
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import patch

from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models.event import (
    Event,
    EventSlot,
    EventVolunteer,
    Expense,
)
from app.models.machine import Machine, MachineStatus
from app.models.user import User
from app.services.csv_io import parse_members_csv

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_event(app: Flask, user_id: int, **kwargs) -> int:
    """Insert a test event and return its ID."""
    with app.app_context():
        event = Event(
            title=kwargs.get("title", "Test Event"),
            event_date=kwargs.get("event_date", datetime(2026, 6, 1, 10, 0, tzinfo=UTC)),
            end_date=kwargs.get("end_date", datetime(2026, 6, 1, 18, 0, tzinfo=UTC)),
            location=kwargs.get("location", "TestVille"),
            created_by_id=user_id,
        )
        db.session.add(event)
        db.session.commit()
        eid = event.id
        db.session.remove()
    return eid


def _create_machine(app: Flask) -> int:
    """Insert a test machine and return its ID."""
    with app.app_context():
        machine = Machine(
            model="Test Pinball",
            manufacturer="TestCorp",
            status=MachineStatus.STOCK,
        )
        db.session.add(machine)
        db.session.commit()
        mid = machine.id
        db.session.remove()
    return mid


# ---------------------------------------------------------------------------
# Profile self-editing
# ---------------------------------------------------------------------------


class TestProfileEdit:
    def test_member_can_edit_own_profile(self, app: Flask, auth_client: FlaskClient, member_user):
        resp = auth_client.post(
            "/members/profile/edit",
            data={
                "first_name": "AliceModified",
                "last_name": "DupontModified",
                "email": "membre@test.com",
                "gender": "not_specified",
                "phone": "0612345678",
                "address": "1 Rue Test",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            user = db.session.get(User, member_user.id)
            assert user.first_name == "AliceModified"
            assert user.phone == "0612345678"
            db.session.remove()

    def test_member_cannot_take_existing_email(
        self, app: Flask, auth_client: FlaskClient, member_user, bureau_user
    ):
        resp = auth_client.post(
            "/members/profile/edit",
            data={
                "first_name": "Alice",
                "last_name": "Dupont",
                "email": "bureau@test.com",
                "gender": "not_specified",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            user = db.session.get(User, member_user.id)
            assert user.email == "membre@test.com"  # unchanged
            db.session.remove()


# ---------------------------------------------------------------------------
# CSV members export/import
# ---------------------------------------------------------------------------


class TestMembersCSV:
    def test_export_csv(self, app: Flask, bureau_client: FlaskClient, bureau_user):
        resp = bureau_client.get("/members/export.csv")
        assert resp.status_code == 200
        assert resp.mimetype == "text/csv"
        assert b"first_name" in resp.data
        assert bureau_user.email.encode() in resp.data

    def test_parse_members_csv_valid(self):
        data = b"first_name,last_name,email,role\nJean,Test,jean@test.fr,member\n"
        rows, errors = parse_members_csv(data)
        assert len(rows) == 1
        assert rows[0]["email"] == "jean@test.fr"
        assert not errors

    def test_parse_members_csv_missing_email(self):
        data = b"first_name,last_name,email,role\nJean,Test,,member\n"
        rows, errors = parse_members_csv(data)
        assert len(rows) == 0
        assert len(errors) == 1

    def test_parse_members_csv_invalid_role(self):
        data = b"first_name,last_name,email,role\nJean,Test,j@t.fr,admin\n"
        rows, errors = parse_members_csv(data)
        assert len(rows) == 0
        assert "admin" in errors[0]


# ---------------------------------------------------------------------------
# Cashbox reset
# ---------------------------------------------------------------------------


class TestCashboxReset:
    def test_bureau_can_reset_cashbox(self, app: Flask, bureau_client: FlaskClient, bureau_user):
        eid = _create_event(app, bureau_user.id)
        # Open cashbox
        bureau_client.post(f"/events/{eid}/cashbox", data={"opening_amount": "50.00"})
        # Reset
        resp = bureau_client.post(f"/events/{eid}/cashbox/reset", follow_redirects=True)
        assert resp.status_code == 200
        with app.app_context():
            event = db.session.get(Event, eid, options=[selectinload(Event.cashbox)])
            assert event.cashbox is None
            db.session.remove()

    def test_reset_without_cashbox(self, app: Flask, bureau_client: FlaskClient, bureau_user):
        eid = _create_event(app, bureau_user.id)
        resp = bureau_client.post(f"/events/{eid}/cashbox/reset", follow_redirects=True)
        assert resp.status_code == 200  # no crash, just a flash


# ---------------------------------------------------------------------------
# Volunteer flow
# ---------------------------------------------------------------------------


class TestVolunteerFlow:
    def test_generate_volunteer_link(self, app: Flask, bureau_client: FlaskClient, bureau_user):
        eid = _create_event(app, bureau_user.id)
        resp = bureau_client.post(f"/events/{eid}/volunteer-link", follow_redirects=True)
        assert resp.status_code == 200
        with app.app_context():
            event = db.session.get(Event, eid)
            assert event.volunteer_token is not None
            db.session.remove()

    def test_volunteer_register_page(
        self, app: Flask, client: FlaskClient, bureau_client: FlaskClient, bureau_user
    ):
        eid = _create_event(app, bureau_user.id)
        bureau_client.post(f"/events/{eid}/volunteer-link")
        with app.app_context():
            event = db.session.get(Event, eid)
            token = event.volunteer_token
            db.session.remove()

        resp = client.get(f"/events/volunteer/{token}")
        assert resp.status_code == 200
        assert b"Inscription" in resp.data

    @patch("app.blueprints.events.routes._send_volunteer_confirmation")
    def test_volunteer_submit_sends_email(
        self, mock_send, app: Flask, client: FlaskClient, bureau_client: FlaskClient, bureau_user
    ):
        eid = _create_event(app, bureau_user.id)
        bureau_client.post(f"/events/{eid}/volunteer-link")
        with app.app_context():
            event = db.session.get(Event, eid)
            token = event.volunteer_token
            db.session.remove()

        resp = client.post(
            f"/events/volunteer/{token}",
            data={"name": "VolTest", "email": "vol@test.fr"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        mock_send.assert_called_once()
        with app.app_context():
            vol = db.session.scalars(
                db.select(EventVolunteer).where(EventVolunteer.email == "vol@test.fr")
            ).first()
            assert vol is not None
            assert not vol.confirmed
            db.session.remove()

    @patch("app.blueprints.events.routes._send_volunteer_confirmation")
    def test_volunteer_confirm_and_portal(
        self, mock_send, app: Flask, client: FlaskClient, bureau_client: FlaskClient, bureau_user
    ):
        eid = _create_event(app, bureau_user.id)
        bureau_client.post(f"/events/{eid}/volunteer-link")
        with app.app_context():
            event = db.session.get(Event, eid)
            token = event.volunteer_token
            db.session.remove()

        client.post(
            f"/events/volunteer/{token}",
            data={"name": "VolTest", "email": "vol@test.fr"},
        )
        with app.app_context():
            vol = db.session.scalars(
                db.select(EventVolunteer).where(EventVolunteer.email == "vol@test.fr")
            ).first()
            personal_token = vol.personal_token
            db.session.remove()

        # Confirm
        resp = client.get(f"/events/volunteer/confirm/{personal_token}", follow_redirects=True)
        assert resp.status_code == 200
        with app.app_context():
            vol = db.session.scalars(
                db.select(EventVolunteer).where(EventVolunteer.email == "vol@test.fr")
            ).first()
            assert vol.confirmed
            db.session.remove()

    def test_delete_volunteer(self, app: Flask, bureau_client: FlaskClient, bureau_user):
        eid = _create_event(app, bureau_user.id)
        with app.app_context():
            vol = EventVolunteer(
                event_id=eid,
                name="ToDelete",
                email="del@test.fr",
                personal_token="test_token_del",
                confirmed=True,
            )
            db.session.add(vol)
            db.session.commit()
            vid = vol.id
            db.session.remove()

        resp = bureau_client.post(f"/events/{eid}/volunteers/{vid}/delete", follow_redirects=True)
        assert resp.status_code == 200
        with app.app_context():
            assert db.session.get(EventVolunteer, vid) is None
            db.session.remove()


# ---------------------------------------------------------------------------
# Distance-based expense (km)
# ---------------------------------------------------------------------------


class TestKmExpense:
    def test_travel_expense_calculates_from_km(
        self, app: Flask, auth_client: FlaskClient, member_user
    ):
        eid = _create_event(app, member_user.id)
        resp = auth_client.post(
            f"/events/{eid}/expenses",
            data={
                "type": "travel",
                "distance_km": "100",
                "description": "Aller-retour Strasbourg",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            expense = db.session.scalars(db.select(Expense).where(Expense.event_id == eid)).first()
            assert expense is not None
            assert expense.distance_km == Decimal("100")
            assert expense.amount > 0  # calculated from km_rate
            db.session.remove()


# ---------------------------------------------------------------------------
# Event task creation
# ---------------------------------------------------------------------------


class TestEventTaskCreation:
    def test_bureau_can_create_task_from_event(
        self, app: Flask, bureau_client: FlaskClient, bureau_user
    ):
        eid = _create_event(app, bureau_user.id)
        resp = bureau_client.post(
            f"/events/{eid}/tasks",
            data={"task_title": "Préparer les machines"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            from app.models.task import Task, TaskSource

            task = db.session.scalars(db.select(Task).where(Task.source_event_id == eid)).first()
            assert task is not None
            assert task.title == "Préparer les machines"
            assert task.source == TaskSource.EVENT
            db.session.remove()


# ---------------------------------------------------------------------------
# Machine purchase fields
# ---------------------------------------------------------------------------


class TestMachinePurchaseFields:
    def test_create_machine_with_purchase_info(self, app: Flask, bureau_client: FlaskClient):
        resp = bureau_client.post(
            "/machines/new",
            data={
                "model": "TestModel",
                "manufacturer": "TestMfg",
                "status": "stock",
                "purchase_date": "2025-01-15",
                "purchase_price": "2500.00",
                "estimated_value": "1800.00",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            m = db.session.scalars(db.select(Machine).where(Machine.model == "TestModel")).first()
            assert m is not None
            assert m.purchase_date == date(2025, 1, 15)
            assert m.purchase_price == Decimal("2500.00")
            assert m.estimated_value == Decimal("1800.00")
            db.session.remove()


# ---------------------------------------------------------------------------
# Volunteer hours calculation
# ---------------------------------------------------------------------------


class TestVolunteerHours:
    def test_volunteer_hours_calculation(self, app: Flask, bureau_user):
        eid = _create_event(app, bureau_user.id)
        with app.app_context():
            from app.models.event import SlotAvailability, SlotAvailabilityStatus

            slot = EventSlot(
                event_id=eid,
                slot_date=date(2026, 6, 1),
                start_time=datetime(2026, 6, 1, 10, 0).time(),
                end_time=datetime(2026, 6, 1, 14, 0).time(),
                label="Matin",
            )
            db.session.add(slot)
            db.session.flush()
            avail = SlotAvailability(
                slot_id=slot.id,
                user_id=bureau_user.id,
                status=SlotAvailabilityStatus.PRESENT,
            )
            db.session.add(avail)
            db.session.commit()

            event = db.session.get(
                Event,
                eid,
                options=[
                    selectinload(Event.slots).selectinload(EventSlot.availabilities),
                    selectinload(Event.slots).selectinload(EventSlot.volunteer_availabilities),
                ],
            )
            # 4 hours * 1 person = 4.0h
            assert event.volunteer_hours == 4.0
            db.session.remove()
