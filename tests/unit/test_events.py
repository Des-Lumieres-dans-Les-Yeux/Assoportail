"""Unit tests for event management — CRUD, attendees, expenses, cashbox."""

from datetime import UTC, datetime
from decimal import Decimal

from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy.orm import selectinload

from app.extensions import db as _db
from app.models.event import CashBox, Event, EventStatus, Expense, ExpenseType
from app.models.user import User
from tests.conftest import UserInfo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(
    app: Flask,
    creator_id: int,
    *,
    title: str = "Journée d'installation",
    status: str = "planned",
) -> int:
    with app.app_context():
        event = Event(
            title=title,
            status=status,
            event_date=datetime(2026, 5, 10, 9, 0, tzinfo=UTC),
            location="Centre de Reims",
            created_by_id=creator_id,
        )
        _db.session.add(event)
        _db.session.commit()
        return event.id


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------


class TestEventAccess:
    def test_member_can_view_list(self, auth_client: FlaskClient) -> None:
        response = auth_client.get("/events/")
        assert response.status_code == 200

    def test_unauthenticated_redirected(self, client: FlaskClient) -> None:
        response = client.get("/events/", follow_redirects=False)
        assert response.status_code in {301, 302}
        assert "/auth/login" in response.headers["Location"]

    def test_member_cannot_create(self, auth_client: FlaskClient) -> None:
        response = auth_client.get("/events/new")
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Create event
# ---------------------------------------------------------------------------


class TestEventCreate:
    def test_bureau_can_create_event(
        self, app: Flask, bureau_client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        response = bureau_client.post(
            "/events/new",
            data={
                "title": "Inauguration flipper",
                "event_date": "2026-06-01T10:00",
                "location": "CHU de Reims",
                "status": "planned",
                "description": "",
            },
            follow_redirects=False,
        )
        assert response.status_code in {301, 302}
        with app.app_context():
            e = _db.session.execute(
                _db.select(Event).filter_by(title="Inauguration flipper")
            ).scalar_one_or_none()
            assert e is not None
            assert e.location == "CHU de Reims"
            assert e.status == EventStatus.PLANNED.value
            assert e.created_by_id == bureau_user.id

    def test_create_requires_date(self, bureau_client: FlaskClient) -> None:
        response = bureau_client.post(
            "/events/new",
            data={"title": "Événement sans date", "event_date": "", "status": "planned"},
        )
        assert response.status_code == 200
        assert "obligatoire" in response.data.decode()


# ---------------------------------------------------------------------------
# Edit event
# ---------------------------------------------------------------------------


class TestEventEdit:
    def test_bureau_can_edit_event(
        self, app: Flask, bureau_client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        eid = _make_event(app, bureau_user.id)
        bureau_client.post(
            f"/events/{eid}/edit",
            data={
                "title": "Journée modifiée",
                "event_date": "2026-05-10T09:00",
                "location": "Nouveau lieu",
                "status": "in_progress",
                "description": "",
            },
        )
        with app.app_context():
            e = _db.session.get(Event, eid)
            assert e.title == "Journée modifiée"
            assert e.status == EventStatus.IN_PROGRESS.value

    def test_member_cannot_edit(
        self, app: Flask, auth_client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        eid = _make_event(app, bureau_user.id)
        response = auth_client.get(f"/events/{eid}/edit")
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Attendee management
# ---------------------------------------------------------------------------


class TestEventAttendees:
    def test_bureau_can_add_attendee(
        self,
        app: Flask,
        bureau_client: FlaskClient,
        bureau_user: UserInfo,
        member_user: UserInfo,
    ) -> None:
        eid = _make_event(app, bureau_user.id)
        bureau_client.post(
            f"/events/{eid}/attendees",
            data={"user_id": member_user.id},
        )
        with app.app_context():
            e = _db.session.get(Event, eid, options=[selectinload(Event.attendees)])
            assert any(a.id == member_user.id for a in e.attendees)

    def test_bureau_can_remove_attendee(
        self,
        app: Flask,
        bureau_client: FlaskClient,
        bureau_user: UserInfo,
        member_user: UserInfo,
    ) -> None:
        eid = _make_event(app, bureau_user.id)
        with app.app_context():
            e = _db.session.get(Event, eid, options=[selectinload(Event.attendees)])
            user = _db.session.get(User, member_user.id)
            e.attendees.append(user)
            _db.session.commit()
        bureau_client.post(f"/events/{eid}/attendees/{member_user.id}/remove")
        with app.app_context():
            e = _db.session.get(Event, eid, options=[selectinload(Event.attendees)])
            assert not any(a.id == member_user.id for a in e.attendees)

    def test_member_cannot_add_attendee(
        self,
        app: Flask,
        auth_client: FlaskClient,
        bureau_user: UserInfo,
        member_user: UserInfo,
    ) -> None:
        eid = _make_event(app, bureau_user.id)
        response = auth_client.post(
            f"/events/{eid}/attendees",
            data={"user_id": member_user.id},
        )
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Expense submission and validation
# ---------------------------------------------------------------------------


class TestEventExpenses:
    def test_member_can_submit_expense(
        self,
        app: Flask,
        auth_client: FlaskClient,
        bureau_user: UserInfo,
        member_user: UserInfo,
    ) -> None:
        eid = _make_event(app, bureau_user.id)
        response = auth_client.post(
            f"/events/{eid}/expenses",
            data={
                "type": "travel",
                "amount": "12.50",
                "description": "Billet de train A→B",
            },
            follow_redirects=False,
        )
        assert response.status_code in {301, 302}
        with app.app_context():
            expense = _db.session.execute(
                _db.select(Expense).where(Expense.event_id == eid)
            ).scalar_one_or_none()
            assert expense is not None
            assert expense.amount == Decimal("12.50")
            assert expense.type == ExpenseType.TRAVEL.value
            assert expense.user_id == member_user.id
            assert not expense.is_validated

    def test_bureau_can_validate_expense(
        self,
        app: Flask,
        bureau_client: FlaskClient,
        bureau_user: UserInfo,
        member_user: UserInfo,
    ) -> None:
        eid = _make_event(app, bureau_user.id)
        with app.app_context():
            expense = Expense(
                event_id=eid,
                user_id=member_user.id,
                type=ExpenseType.SUPPLY.value,
                amount=Decimal("25.00"),
                description="Câbles HDMI",
            )
            _db.session.add(expense)
            _db.session.commit()
            expense_id = expense.id
        bureau_client.post(f"/events/{eid}/expenses/{expense_id}/validate")
        with app.app_context():
            e = _db.session.get(Expense, expense_id)
            assert e.is_validated
            assert e.validated_by_id == bureau_user.id

    def test_empty_expense_is_rejected(
        self,
        app: Flask,
        auth_client: FlaskClient,
        bureau_user: UserInfo,
    ) -> None:
        eid = _make_event(app, bureau_user.id)
        auth_client.post(
            f"/events/{eid}/expenses",
            data={"type": "other", "amount": "", "description": ""},
        )
        with app.app_context():
            count = _db.session.execute(
                _db.select(_db.func.count(Expense.id)).where(Expense.event_id == eid)
            ).scalar()
            assert count == 0


# ---------------------------------------------------------------------------
# Cash box
# ---------------------------------------------------------------------------


class TestEventCashBox:
    def test_bureau_can_open_cashbox(
        self,
        app: Flask,
        bureau_client: FlaskClient,
        bureau_user: UserInfo,
    ) -> None:
        eid = _make_event(app, bureau_user.id)
        bureau_client.post(
            f"/events/{eid}/cashbox",
            data={"opening_amount": "50.00"},
        )
        with app.app_context():
            e = _db.session.get(Event, eid, options=[selectinload(Event.cashbox)])
            assert e.cashbox is not None
            assert e.cashbox.opening_amount == Decimal("50.00")
            assert not e.cashbox.is_closed

    def test_cannot_open_second_cashbox(
        self,
        app: Flask,
        bureau_client: FlaskClient,
        bureau_user: UserInfo,
    ) -> None:
        eid = _make_event(app, bureau_user.id)
        # Open once
        bureau_client.post(f"/events/{eid}/cashbox", data={"opening_amount": "50.00"})
        # Try again
        bureau_client.post(f"/events/{eid}/cashbox", data={"opening_amount": "100.00"})
        with app.app_context():
            e = _db.session.get(Event, eid, options=[selectinload(Event.cashbox)])
            assert e.cashbox.opening_amount == Decimal("50.00")  # unchanged

    def test_bureau_can_add_cash_entries(
        self,
        app: Flask,
        bureau_client: FlaskClient,
        bureau_user: UserInfo,
    ) -> None:
        eid = _make_event(app, bureau_user.id)
        bureau_client.post(f"/events/{eid}/cashbox", data={"opening_amount": "0.00"})
        bureau_client.post(
            f"/events/{eid}/cashbox/entries",
            data={"type": "donation", "amount": "10.00", "note": "Jar de dons"},
        )
        with app.app_context():
            e = _db.session.get(
                Event,
                eid,
                options=[selectinload(Event.cashbox).selectinload(CashBox.entries)],
            )
            assert len(e.cashbox.entries) == 1
            assert e.cashbox.entries[0].amount == Decimal("10.00")
            assert e.cashbox.expected_amount == Decimal("10.00")

    def test_bureau_can_close_cashbox(
        self,
        app: Flask,
        bureau_client: FlaskClient,
        bureau_user: UserInfo,
    ) -> None:
        eid = _make_event(app, bureau_user.id)
        bureau_client.post(f"/events/{eid}/cashbox", data={"opening_amount": "20.00"})
        bureau_client.post(
            f"/events/{eid}/cashbox/close",
            data={"closing_amount": "20.00", "reconciliation_note": ""},
        )
        with app.app_context():
            e = _db.session.get(
                Event,
                eid,
                options=[selectinload(Event.cashbox).selectinload(CashBox.entries)],
            )
            assert e.cashbox.is_closed
            assert e.cashbox.closing_amount == Decimal("20.00")
            assert e.cashbox.discrepancy == Decimal("0.00")

    def test_discrepancy_computed_correctly(
        self,
        app: Flask,
        bureau_client: FlaskClient,
        bureau_user: UserInfo,
    ) -> None:
        eid = _make_event(app, bureau_user.id)
        bureau_client.post(f"/events/{eid}/cashbox", data={"opening_amount": "10.00"})
        bureau_client.post(
            f"/events/{eid}/cashbox/entries",
            data={"type": "donation", "amount": "5.00", "note": ""},
        )
        # Expected = 10 + 5 = 15; closing = 14 → discrepancy = -1
        bureau_client.post(
            f"/events/{eid}/cashbox/close",
            data={"closing_amount": "14.00", "reconciliation_note": ""},
        )
        with app.app_context():
            e = _db.session.get(
                Event,
                eid,
                options=[selectinload(Event.cashbox).selectinload(CashBox.entries)],
            )
            assert e.cashbox.discrepancy == Decimal("-1.00")

    def test_member_cannot_open_cashbox(
        self,
        app: Flask,
        auth_client: FlaskClient,
        bureau_user: UserInfo,
    ) -> None:
        eid = _make_event(app, bureau_user.id)
        response = auth_client.post(f"/events/{eid}/cashbox", data={"opening_amount": "50.00"})
        assert response.status_code == 403
