"""Unit tests for meeting management — CRUD, attendees, task linking."""

from datetime import UTC, datetime

from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy.orm import selectinload

from app.extensions import db as _db
from app.models.meeting import Meeting
from app.models.task import Task, TaskSource, TaskStatus
from app.models.user import User
from tests.conftest import UserInfo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_meeting(
    app: Flask,
    creator_id: int,
    *,
    title: str = "Réunion de bureau",
    attendee_ids: list[int] | None = None,
) -> int:
    with app.app_context():
        meeting = Meeting(
            title=title,
            date=datetime(2026, 4, 1, 14, 0, tzinfo=UTC),
            location="Salle A",
            created_by_id=creator_id,
        )
        if attendee_ids:
            users = _db.session.scalars(_db.select(User).where(User.id.in_(attendee_ids))).all()
            meeting.attendees.extend(users)
        _db.session.add(meeting)
        _db.session.commit()
        return meeting.id


def _make_task(app: Flask, creator_id: int, *, title: str = "Tâche de test") -> int:
    with app.app_context():
        task = Task(
            title=title,
            status=TaskStatus.OPEN.value,
            created_by_id=creator_id,
            source=TaskSource.MANUAL,
        )
        _db.session.add(task)
        _db.session.commit()
        return task.id


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------


class TestMeetingAccess:
    def test_member_can_view_list(self, auth_client: FlaskClient) -> None:
        response = auth_client.get("/meetings/")
        assert response.status_code == 200

    def test_unauthenticated_redirected(self, client: FlaskClient) -> None:
        response = client.get("/meetings/", follow_redirects=False)
        assert response.status_code in {301, 302}
        assert "/auth/login" in response.headers["Location"]

    def test_member_cannot_create(self, auth_client: FlaskClient) -> None:
        response = auth_client.get("/meetings/new")
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Create meeting
# ---------------------------------------------------------------------------


class TestMeetingCreate:
    def test_bureau_can_create_meeting(
        self, app: Flask, bureau_client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        response = bureau_client.post(
            "/meetings/new",
            data={
                "title": "Réunion mensuelle",
                "date": "2026-04-15T18:30",
                "location": "Salle de réunion",
                "minutes": "",
            },
            follow_redirects=False,
        )
        assert response.status_code in {301, 302}
        with app.app_context():
            m = _db.session.execute(
                _db.select(Meeting).filter_by(title="Réunion mensuelle")
            ).scalar_one_or_none()
            assert m is not None
            assert m.location == "Salle de réunion"
            assert m.created_by_id == bureau_user.id
            assert m.date.tzinfo is not None

    def test_create_requires_title(self, bureau_client: FlaskClient) -> None:
        response = bureau_client.post(
            "/meetings/new",
            data={"title": "", "date": "2026-04-15T18:30"},
        )
        assert response.status_code == 200
        assert "obligatoire" in response.data.decode()


# ---------------------------------------------------------------------------
# Edit meeting
# ---------------------------------------------------------------------------


class TestMeetingEdit:
    def test_bureau_can_edit_minutes(
        self, app: Flask, bureau_client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        mid = _make_meeting(app, bureau_user.id)
        bureau_client.post(
            f"/meetings/{mid}/edit",
            data={
                "title": "Réunion de bureau",
                "date": "2026-04-01T14:00",
                "location": "Salle A",
                "minutes": "Décision : acheter 2 flippers.",
            },
        )
        with app.app_context():
            m = _db.session.get(Meeting, mid)
            assert m.minutes == "Décision : acheter 2 flippers."

    def test_member_cannot_edit(
        self, app: Flask, auth_client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        mid = _make_meeting(app, bureau_user.id)
        response = auth_client.get(f"/meetings/{mid}/edit")
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Attendee management
# ---------------------------------------------------------------------------


class TestMeetingAttendees:
    def test_bureau_can_add_attendee(
        self,
        app: Flask,
        bureau_client: FlaskClient,
        bureau_user: UserInfo,
        member_user: UserInfo,
    ) -> None:
        mid = _make_meeting(app, bureau_user.id)
        bureau_client.post(
            f"/meetings/{mid}/attendees",
            data={"user_id": member_user.id},
        )
        with app.app_context():
            m = _db.session.get(Meeting, mid, options=[selectinload(Meeting.attendees)])
            assert any(a.id == member_user.id for a in m.attendees)

    def test_adding_same_attendee_twice_is_idempotent(
        self,
        app: Flask,
        bureau_client: FlaskClient,
        bureau_user: UserInfo,
        member_user: UserInfo,
    ) -> None:
        mid = _make_meeting(app, bureau_user.id, attendee_ids=[member_user.id])
        # Second add should not raise and should keep one attendee
        bureau_client.post(
            f"/meetings/{mid}/attendees",
            data={"user_id": member_user.id},
        )
        with app.app_context():
            m = _db.session.get(Meeting, mid, options=[selectinload(Meeting.attendees)])
            assert len([a for a in m.attendees if a.id == member_user.id]) == 1

    def test_bureau_can_remove_attendee(
        self,
        app: Flask,
        bureau_client: FlaskClient,
        bureau_user: UserInfo,
        member_user: UserInfo,
    ) -> None:
        mid = _make_meeting(app, bureau_user.id, attendee_ids=[member_user.id])
        bureau_client.post(f"/meetings/{mid}/attendees/{member_user.id}/remove")
        with app.app_context():
            m = _db.session.get(Meeting, mid, options=[selectinload(Meeting.attendees)])
            assert not any(a.id == member_user.id for a in m.attendees)

    def test_member_cannot_add_attendee(
        self,
        app: Flask,
        auth_client: FlaskClient,
        bureau_user: UserInfo,
        member_user: UserInfo,
    ) -> None:
        mid = _make_meeting(app, bureau_user.id)
        response = auth_client.post(
            f"/meetings/{mid}/attendees",
            data={"user_id": member_user.id},
        )
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Task linking
# ---------------------------------------------------------------------------


class TestMeetingTaskLinking:
    def test_bureau_can_link_task(
        self,
        app: Flask,
        bureau_client: FlaskClient,
        bureau_user: UserInfo,
    ) -> None:
        mid = _make_meeting(app, bureau_user.id)
        tid = _make_task(app, bureau_user.id)
        bureau_client.post(
            f"/meetings/{mid}/tasks",
            data={"task_id": tid},
        )
        with app.app_context():
            m = _db.session.get(Meeting, mid, options=[selectinload(Meeting.tasks)])
            assert any(t.id == tid for t in m.tasks)

    def test_bureau_can_unlink_task(
        self,
        app: Flask,
        bureau_client: FlaskClient,
        bureau_user: UserInfo,
    ) -> None:
        mid = _make_meeting(app, bureau_user.id)
        tid = _make_task(app, bureau_user.id)
        # Link first
        with app.app_context():
            m = _db.session.get(Meeting, mid, options=[selectinload(Meeting.tasks)])
            task = _db.session.get(Task, tid)
            m.tasks.append(task)
            _db.session.commit()
        # Unlink
        bureau_client.post(f"/meetings/{mid}/tasks/{tid}/unlink")
        with app.app_context():
            m = _db.session.get(Meeting, mid, options=[selectinload(Meeting.tasks)])
            assert not any(t.id == tid for t in m.tasks)

    def test_linked_tasks_appear_on_detail_page(
        self,
        app: Flask,
        auth_client: FlaskClient,
        bureau_user: UserInfo,
    ) -> None:
        mid = _make_meeting(app, bureau_user.id)
        tid = _make_task(app, bureau_user.id, title="Réparer flipper Apollo")
        with app.app_context():
            m = _db.session.get(Meeting, mid, options=[selectinload(Meeting.tasks)])
            task = _db.session.get(Task, tid)
            m.tasks.append(task)
            _db.session.commit()
        response = auth_client.get(f"/meetings/{mid}")
        assert "Réparer flipper Apollo" in response.data.decode()
