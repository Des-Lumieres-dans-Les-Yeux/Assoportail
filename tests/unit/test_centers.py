"""Unit tests for centers — CRUD, breakdown reporting, feedback moderation."""

import secrets
from datetime import UTC, datetime

from flask import Flask
from flask.testing import FlaskClient

from app.extensions import db as _db
from app.models.center import Center, CenterFeedback, CenterStatus
from app.models.task import Task, TaskSource, TaskStatus
from tests.conftest import UserInfo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _set_feedback_token(app: Flask, center_id: int) -> str:
    """Assign a random feedback token to *center_id* and return it."""
    token = secrets.token_urlsafe(32)
    with app.app_context():
        center = _db.session.get(Center, center_id)
        center.feedback_token = token
        _db.session.commit()
    return token


def _make_center(app: Flask, *, name: str = "CHU Nord", status: str = "active") -> int:
    with app.app_context():
        c = Center(name=name, city="Paris", zip_code="75001", status=status)
        _db.session.add(c)
        _db.session.commit()
        return c.id


def _make_feedback(
    app: Flask,
    center_id: int,
    *,
    submitted_by: str = "Marie Curie",
    content: str = "Très bien !",
    is_published: bool = False,
) -> int:
    with app.app_context():
        fb = CenterFeedback(
            center_id=center_id,
            submitted_by=submitted_by,
            content=content,
            submitted_at=datetime.now(UTC),
            is_published=is_published,
        )
        _db.session.add(fb)
        _db.session.commit()
        return fb.id


# ---------------------------------------------------------------------------
# Center CRUD
# ---------------------------------------------------------------------------


class TestCenterCreate:
    def test_bureau_can_create_center(self, app: Flask, bureau_client: FlaskClient) -> None:
        response = bureau_client.post(
            "/centers/new",
            data={
                "name": "Hôpital Sainte-Marie",
                "address": "12 rue de la Paix",
                "city": "Lyon",
                "zip_code": "69001",
                "contact_name": "",
                "contact_email": "",
                "contact_phone": "",
                "status": "prospect",
                "notes": "",
            },
            follow_redirects=False,
        )
        assert response.status_code in {301, 302}
        with app.app_context():
            c = _db.session.execute(
                _db.select(Center).filter_by(name="Hôpital Sainte-Marie")
            ).scalar_one_or_none()
            assert c is not None
            assert c.status == CenterStatus.PROSPECT

    def test_member_cannot_create_center(self, auth_client: FlaskClient) -> None:
        response = auth_client.get("/centers/new")
        assert response.status_code == 403


class TestCenterList:
    def test_member_can_view_center_list(self, auth_client: FlaskClient) -> None:
        response = auth_client.get("/centers/")
        assert response.status_code == 200

    def test_center_list_shows_center_name(self, app: Flask, auth_client: FlaskClient) -> None:
        _make_center(app, name="Clinique du Parc")
        response = auth_client.get("/centers/")
        assert "Clinique du Parc" in response.data.decode()

    def test_unauthenticated_user_redirected(self, client: FlaskClient) -> None:
        response = client.get("/centers/", follow_redirects=False)
        assert response.status_code in {301, 302}
        assert "/auth/login" in response.headers["Location"]


class TestCenterEdit:
    def test_bureau_can_change_center_status(self, app: Flask, bureau_client: FlaskClient) -> None:
        cid = _make_center(app, status="prospect")
        bureau_client.post(
            f"/centers/{cid}/edit",
            data={
                "name": "CHU Nord",
                "address": "",
                "city": "Paris",
                "zip_code": "75001",
                "contact_name": "",
                "contact_email": "",
                "contact_phone": "",
                "status": "active",
                "pathology": "",
                "target_audience": "",
                "latitude": "48.8566",
                "longitude": "2.3522",
                "notes": "",
            },
        )
        with app.app_context():
            c = _db.session.get(Center, cid)
            assert c.status == CenterStatus.ACTIVE


# ---------------------------------------------------------------------------
# Breakdown reporting
# ---------------------------------------------------------------------------


class TestBreakdownReport:
    def test_member_can_report_breakdown(
        self, app: Flask, auth_client: FlaskClient, member_user: UserInfo
    ) -> None:
        """Any logged-in member can report a breakdown — creates an open Task."""
        cid = _make_center(app)
        response = auth_client.post(
            f"/centers/{cid}/breakdown",
            data={"description": "Le flipper droit est bloqué.", "priority": "high"},
            follow_redirects=False,
        )
        assert response.status_code in {301, 302}

        with app.app_context():
            task = _db.session.execute(
                _db.select(Task).where(Task.source_center_id == cid)
            ).scalar_one_or_none()
            assert task is not None
            assert task.status == TaskStatus.OPEN
            assert task.source == TaskSource.CENTER_BREAKDOWN
            assert "CHU Nord" in task.title

    def test_breakdown_task_has_correct_priority(
        self, app: Flask, auth_client: FlaskClient
    ) -> None:
        cid = _make_center(app)
        auth_client.post(
            f"/centers/{cid}/breakdown",
            data={"description": "Panne moteur.", "priority": "urgent"},
        )
        with app.app_context():
            task = _db.session.execute(
                _db.select(Task).where(Task.source_center_id == cid)
            ).scalar_one()
            assert task.priority.value == "urgent"

    def test_unauthenticated_user_cannot_report_breakdown(
        self, app: Flask, client: FlaskClient
    ) -> None:
        cid = _make_center(app)
        response = client.post(
            f"/centers/{cid}/breakdown",
            data={"description": "Panne.", "priority": "high"},
            follow_redirects=False,
        )
        assert response.status_code in {301, 302}
        assert "/auth/login" in response.headers["Location"]


# ---------------------------------------------------------------------------
# Feedback moderation
# ---------------------------------------------------------------------------


class TestFeedbackModeration:
    def test_bureau_can_publish_feedback(self, app: Flask, bureau_client: FlaskClient) -> None:
        cid = _make_center(app)
        fb_id = _make_feedback(app, cid, is_published=False)

        response = bureau_client.post(
            f"/centers/{cid}/feedbacks/{fb_id}/publish",
            follow_redirects=False,
        )
        assert response.status_code in {301, 302}

        with app.app_context():
            fb = _db.session.get(CenterFeedback, fb_id)
            assert fb.is_published is True
            assert fb.published_at is not None

    def test_bureau_can_unpublish_feedback(self, app: Flask, bureau_client: FlaskClient) -> None:
        cid = _make_center(app)
        fb_id = _make_feedback(app, cid, is_published=True)

        bureau_client.post(f"/centers/{cid}/feedbacks/{fb_id}/unpublish")

        with app.app_context():
            fb = _db.session.get(CenterFeedback, fb_id)
            assert fb.is_published is False

    def test_guestbook_shows_only_published(self, app: Flask, auth_client: FlaskClient) -> None:
        """Unpublished feedbacks must not appear in the guestbook."""
        cid = _make_center(app)
        _make_feedback(app, cid, submitted_by="Visible", is_published=True)
        _make_feedback(app, cid, submitted_by="Caché", is_published=False)

        response = auth_client.get(f"/centers/{cid}/guestbook")
        assert response.status_code == 200
        body = response.data.decode()
        assert "Visible" in body
        assert "Caché" not in body

    def test_member_cannot_publish_feedback(self, app: Flask, auth_client: FlaskClient) -> None:
        cid = _make_center(app)
        fb_id = _make_feedback(app, cid)
        response = auth_client.post(f"/centers/{cid}/feedbacks/{fb_id}/publish")
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Public feedback submission (signed URL)
# ---------------------------------------------------------------------------


class TestFeedbackSubmission:
    def test_valid_token_renders_form(self, app: Flask, client: FlaskClient) -> None:
        """A valid token in the center's feedback_token column grants access."""
        cid = _make_center(app)
        token = _set_feedback_token(app, cid)

        response = client.get(f"/centers/feedback/{token}")
        assert response.status_code == 200
        assert "témoignage" in response.data.decode().lower()

    def test_invalid_token_returns_403(self, app: Flask, client: FlaskClient) -> None:
        _make_center(app)
        response = client.get("/centers/feedback/invalid-token")
        assert response.status_code == 403

    def test_valid_submission_creates_feedback(self, app: Flask, client: FlaskClient) -> None:
        cid = _make_center(app)
        token = _set_feedback_token(app, cid)

        response = client.post(
            f"/centers/feedback/{token}",
            data={
                "submitted_by": "Patient Test",
                "content": "Merci pour la machine de flipper !",
                "rating": "5",
                "website": "",  # honeypot empty
            },
            follow_redirects=False,
        )
        assert response.status_code in {301, 302}

        with app.app_context():
            fb = _db.session.execute(
                _db.select(CenterFeedback).where(CenterFeedback.center_id == cid)
            ).scalar_one_or_none()
            assert fb is not None
            assert fb.submitted_by == "Patient Test"
            assert fb.is_published is False  # awaiting moderation

    def test_honeypot_filled_discards_submission(self, app: Flask, client: FlaskClient) -> None:
        """A bot that fills the honeypot field gets a success response but no DB record."""
        cid = _make_center(app)
        token = _set_feedback_token(app, cid)

        client.post(
            f"/centers/feedback/{token}",
            data={
                "submitted_by": "Bot",
                "content": "Spam content",
                "rating": "",
                "website": "http://spam.example.com",  # honeypot filled
            },
        )

        with app.app_context():
            count = _db.session.execute(
                _db.select(_db.func.count(CenterFeedback.id)).where(CenterFeedback.center_id == cid)
            ).scalar()
            assert count == 0
